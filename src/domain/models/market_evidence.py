"""Ground short source excerpts separately from a model's explanatory narrative."""
import re
from src.domain.models.market_target import normalize, chips_in

# A target chip following these phrases refers to the replacement/comparison.
# Never reject unrelated phrases later in the title, e.g. 准备换个内存.
REFERENCE_PREFIX = re.compile(
    r'(?:因(?:为)?(?:已)?换(?:了|成)?|准备换|打算换|想换|换了|换成|换购|升级到|升级为|升级成|相比|对比|兼容|适用|不是|不含)'
    r'\s*(?:新款\s*)?(?:(?:mac\s*mini|苹果)\s*)?$', re.I)


def verified_excerpts(result, item, chip):
    sources = {'title': normalize(item.get('商品标题')), 'description': normalize(item.get('商品描述'))}
    supplied = result.get('evidence_quotes') or []
    candidates = [(entry.get('source'), entry.get('text')) for entry in supplied if isinstance(entry,dict)]
    # Backward compatibility for previous classifier outputs: a narrative isn't
    # one giant quote. Only independently grounded quoted fragments may count.
    if not supplied and result.get('evidence_source') == 'text':
        narrative = result.get('identity_evidence') or ''
        fragments = [narrative] + re.findall(r'[“「\"]([^”」\"]+)[”」\"]',narrative)
        candidates += [(source,fragment) for fragment in fragments for source in sources]
    if result.get('evidence_source') == 'image' and not supplied:
        candidates.append(('image',result.get('identity_evidence')))
    verified=[]
    referenced=False
    for source, raw in candidates:
        text=normalize(raw)
        if not text: continue
        if source=='image':
            if chip and chip not in chips_in(text):
                continue
            verified.append({'source':'image','text':str(raw)})
            continue
        full=sources.get(source,'')
        if not full or text not in full or (chip and chip not in chips_in(text)):
            continue
        occurrences=[m.start() for m in re.finditer(re.escape(text),full)]
        supported=False
        for offset in occurrences:
            # Evaluate context before the chip itself, not every word in a long quote.
            from src.domain.models.market_target import CHIP, chip_name
            mentions=[m for m in CHIP.finditer(text) if chip_name(m.group())==chip] if chip else []
            positions=[offset+m.start() for m in mentions] or [offset]
            if any(not REFERENCE_PREFIX.search(full[max(0,pos-32):pos]) for pos in positions):
                supported=True
                break
        if supported:
            entry={'source':source,'text':str(raw)}
            if entry not in verified: verified.append(entry)
        else:
            referenced=True
    return verified, referenced
