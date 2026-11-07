"""Offline JSON title replay; never starts scraper, network, AI or notifications.

Run from repository root: python -m tools.replay_listing_prefilter --help
"""
import argparse
import json
from collections import Counter
from pathlib import Path

from src.domain.models.prefilter import ListingPrefilterConfig
from src.services.listing_prefilter import compile_prefilter, evaluate_title


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--policy', required=True, help='PATCH JSON containing prefilter')
    parser.add_argument('--input', required=True, help='JSON array of titles or objects with 商品标题/商品链接')
    args = parser.parse_args()
    raw = json.loads(Path(args.policy).read_text(encoding='utf-8'))
    policy = ListingPrefilterConfig.model_validate(raw['prefilter'])
    compiled = compile_prefilter(policy)
    titles = json.loads(Path(args.input).read_text(encoding='utf-8'))
    if not isinstance(titles, list):
        parser.error('input must be a JSON array')
    counts = Counter()
    reasons = Counter()
    unique = set()
    for i, entry in enumerate(titles, 1):
        title = entry.get('商品标题') if isinstance(entry, dict) else entry
        if policy.mode == 'off':
            counts['off'] += 1
            continue
        decision = evaluate_title(title, compiled)
        counts[decision.action] += 1
        reasons[decision.reason] += 1
        if decision.action == 'REJECT' and isinstance(entry, dict):
            link = entry.get('商品链接')
            if isinstance(link, str) and link:
                unique.add(link.split('&', 1)[0])
        # Only controlled row indices and rule labels are exposed.
        print(json.dumps(dict(candidate=i, action=decision.action, reason=decision.reason,
                              matched_token=decision.matched_token), ensure_ascii=False))
    print(json.dumps(dict(mode=policy.mode, occurrences=len(titles), decisions=dict(counts),
                          reasons=dict(reasons), unique_rejected_links=len(unique)), ensure_ascii=False))


if __name__ == '__main__':
    main()
