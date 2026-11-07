"""Pure conservative title evaluation, with a separate per-run observer."""
import hashlib
import json
import re
import time
import unicodedata
import uuid
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from src.domain.models.prefilter import ListingPrefilterConfig, normalize_title


@dataclass(frozen=True)
class PrefilterDecision:
    action: Literal["KEEP", "REJECT"]
    reason: Literal["explicit_excluded_variant", "target_token_present", "ambiguous_title",
                    "no_clear_product_variant", "title_unusable", "evaluator_error"]
    matched_token: str | None = None


@dataclass(frozen=True)
class CompiledPrefilter:
    mode: str
    tokens: re.Pattern
    keep_tokens: frozenset
    anchored: re.Pattern
    ambiguity_terms: tuple
    variants: re.Pattern


def _alternatives(values):
    return "|".join(re.escape(v) for v in sorted(values, key=len, reverse=True))


def compile_prefilter(config: ListingPrefilterConfig) -> CompiledPrefilter:
    # Copy all mutable configuration into task-local immutable compiled values.
    tokens = _alternatives(config.reject_tokens + config.keep_tokens) or r"(?!)"
    aliases = _alternatives(config.product_aliases) or r"(?!)"
    separator = r"[\s._:：-]{0,8}"
    families = {m.group() for token in config.reject_tokens + config.keep_tokens
                if (m := re.match(r"[a-z]+(?=\d)", token))}
    variants = re.compile(
        r"(?<![a-z0-9])(?:(?:" + aliases + r")" + separator + r")?"
        r"(?P<variant>(?:" + _alternatives(families) + r")\d+[a-z]*)(?![a-z0-9])"
    ) if families else re.compile(r"(?!)")
    return CompiledPrefilter(
        config.mode,
        re.compile(r"(?<![a-z0-9])(?:" + tokens + r")(?![a-z0-9])"),
        frozenset(config.keep_tokens),
        re.compile(r"(?<![a-z0-9])(?:" + aliases + r")" + separator + r"(?P<token>" + tokens + r")(?![a-z0-9])"),
        tuple(config.ambiguity_terms),
        variants,
    )


def evaluate_title(title: object, compiled: CompiledPrefilter) -> PrefilterDecision:
    def keep(reason):
        return PrefilterDecision("KEEP", reason)
    if not isinstance(title, str) or not title.strip() or len(title) > 512:
        return keep("title_unusable")
    text = normalize_title(title)
    if text.endswith("...") or len(text) > 512 or text in {"未知标题", "unknown", "unknown title"} or any(unicodedata.category(c).startswith("C") for c in text):
        return keep("title_unusable")
    anchored = list(compiled.anchored.finditer(text))
    matches = [(m.start(), m.end(), m.group()) for m in compiled.tokens.finditer(text)]
    # Compact anchor+token is a controlled exception to the ASCII left boundary.
    matches += [(m.start("token"), m.end("token"), m.group("token")) for m in anchored]
    labels = {token for _, _, token in matches}
    targets = labels & compiled.keep_tokens
    if targets:
        return keep("ambiguous_title" if labels - targets else "target_token_present")
    if any(term in text for term in compiled.ambiguity_terms):
        return keep("ambiguous_title")
    # Slash/option syntax cannot establish a single variant; '+' alone is a spec.
    if re.search(r"[/\\|、?？]", text) or len(labels) > 1:
        return keep("ambiguous_title")
    if not anchored:
        return keep("no_clear_product_variant")
    # Unconfigured alphanumeric variants elsewhere (including future models) keep
    # the title ambiguous. Numeric RAM/SSD specifications do not match this form.
    spans = [(s, e) for s, e, _ in matches]
    for m in compiled.variants.finditer(text):
        if not any(s <= m.start("variant") and m.end("variant") <= e for s, e in spans):
            return keep("ambiguous_title")
    return PrefilterDecision("REJECT", "explicit_excluded_variant", anchored[0].group("token"))


class PrefilterRun:
    """Bounded privacy-safe diagnostics and fail-open handling for one run."""
    def __init__(self, raw, *, task_id=None):
        self.exit_status = "completed"
        self.attempts = 0
        self.compiled = None
        self.counts = Counter()
        self.reasons = Counter()
        self.elapsed = 0.0
        self.run_id = uuid.uuid4().hex[:12]
        self.task_id = task_id if type(task_id) is int else None
        fingerprint = None
        try:
            if raw is not None:
                config = ListingPrefilterConfig.model_validate(raw)
                self.compiled = compile_prefilter(config)
                fingerprint = hashlib.sha256(config.model_dump_json().encode()).hexdigest()[:16]
        except Exception as exc:
            self.log("config_invalid", error=type(exc).__name__, field="prefilter")
        if self.enabled:
            self.log("start", task_id=self.task_id, version=1, mode=self.compiled.mode, fingerprint=fingerprint)

    @property
    def enabled(self):
        return self.compiled is not None and self.compiled.mode != "off"

    def log(self, event, **fields):
        print("prefilter." + event + " " + json.dumps(dict(run_id=self.run_id, **fields), ensure_ascii=False))

    def should_skip(self, title, page, candidate):
        if not self.enabled:
            return False
        start = time.perf_counter()
        try:
            decision = evaluate_title(title, self.compiled)
        except Exception as exc:
            decision = PrefilterDecision("KEEP", "evaluator_error")
            if not self.counts["fail_open"]:
                self.log("eval_failed", error=type(exc).__name__)
            self.counts["fail_open"] += 1
        finally:
            self.elapsed += time.perf_counter() - start
        self.counts["evaluated"] += 1
        self.reasons[decision.reason] += 1
        reject = decision.action == "REJECT"
        skip = reject and self.compiled.mode == "enforce"
        self.counts["skipped" if skip else "passed"] += 1
        if reject:
            event = "skipped" if skip else "would_reject"
            if not skip:
                self.counts[event] += 1
            if self.counts["skipped"] + self.counts["would_reject"] <= 20:
                self.log(event, page=page, candidate=candidate, reason=decision.reason, matched_token=decision.matched_token)
        return skip

    def summary(self, status):
        if self.enabled:
            self.log("summary", mode=self.compiled.mode, status=status, attempts=self.attempts,
                     **{k: self.counts[k] for k in ("evaluated", "passed", "skipped", "would_reject", "fail_open")},
                     reasons=dict(self.reasons), elapsed_seconds=self.elapsed)
