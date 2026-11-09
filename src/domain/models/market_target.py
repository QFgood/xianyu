"""Literal hardware constraints inferred from a task's explicit target, never from listings."""
import re
import unicodedata
from dataclasses import dataclass


def normalize(value):
    text = unicodedata.normalize('NFKC', str(value or '')).casefold()
    text = re.sub(r'(?<![a-z])mac\s*mini(?=m\d)', 'mac mini ', text)
    return ' '.join(text.split())


CHIP = re.compile(r'(?<![a-z0-9])m\s*([1-9]\d?)\s*(pro|max|ultra)?(?![a-z0-9])', re.I)


def chip_name(value):
    match = CHIP.fullmatch(normalize(value))
    return ('m' + match[1] + (' ' + match[2] if match[2] else '')) if match else None


def chips_in(value):
    return {chip_name(m.group()) for m in CHIP.finditer(normalize(value))}


@dataclass(frozen=True)
class MarketTarget:
    product_family: str | None = None
    chip: str | None = None
    memory_gb: int | None = None
    storage_gb: int | None = None
    cpu_cores: int | None = None
    gpu_cores: int | None = None

    @classmethod
    def from_task(cls, task):
        selection = task.get('product_selection')
        if selection:
            from src.domain.models.product_selection import ProductSelection, find_product
            selected = ProductSelection.model_validate(selection)
            product = find_product(selected.catalog_id)
            return cls(product['product_family'], chip_name(product['chip']), selected.memory_gb,
                       selected.storage_gb, product['cpu_cores'], product['gpu_cores'])
        # Task name is the primary identity. Description fills missing constraints.
        name = normalize(task.get('task_name') or task.get('keyword'))
        name = re.sub(r'(?<![a-z])mac\s*mini', 'mac mini ', name)
        detail = normalize(task.get('description'))
        detail = re.sub(r'(?<![a-z])mac\s*mini', 'mac mini ', detail)
        keyword = normalize(task.get('keyword'))
        combined = name + ' ' + detail + ' ' + keyword
        family = 'mac mini' if re.search(r'(?<![a-z])mac\s*mini(?![a-z])', combined) else None
        selected = chips_in(name) or chips_in(detail) or chips_in(keyword)
        chip = next(iter(selected)) if len(selected) == 1 else None
        memory = storage = None
        for text in (name, detail, keyword):
            pair = re.search(r'(?<!\d)(\d+)\s*(?:g(?:b)?)?\s*[+＋/]\s*(\d+)\s*(t(?:b)?|g(?:b)?)?(?!\d)', text)
            if pair:
                memory = memory or int(pair[1])
                storage = storage or int(pair[2]) * (1024 if (pair[3] or '').startswith('t') else 1)
            units = list(re.finditer(r'(?<!\d)(\d+)\s*(gb|g|tb|t)(?![a-z])', text))
            if len(units) >= 2:
                memory = memory or int(units[0][1])
                storage = storage or int(units[1][1]) * (1024 if units[1][2].startswith('t') else 1)
            ram = re.search(r'(\d+)\s*(?:gb|g)?\s*(?:内存|统一内存|ram)', text)
            disk = re.search(r'(\d+)\s*(gb|g|tb|t)?\s*(?:内置\s*)?(?:ssd|固态|硬盘)', text)
            if ram:
                memory = memory or int(ram[1])
            if disk:
                storage = storage or int(disk[1]) * (1024 if (disk[2] or '').startswith('t') else 1)
        return cls(family, chip, memory, storage)

    def reject_title(self, title):
        """Reject only an explicit other Mac mini generation; ambiguity goes to details."""
        text = normalize(title)
        if not self.chip or self.product_family != 'mac mini' or not isinstance(title, str):
            return False
        if len(text) > 512 or text.endswith('...'):
            return False
        chips = chips_in(text)
        # Target references/comparisons require the detail identity check below.
        if not chips or self.chip in chips or len(chips) != 1:
            return False
        if re.search(r'[/、?？]|对比|比较|兼容|适用|不是|并非|求购|换|可选|不含|非卖|评测', text):
            return False
        return bool(re.search(r'(?<![a-z])mac\s*mini(?![a-z])', text))

    def validate_identity(self, result, item):
        result = dict(result)
        if result['match_status'] != 'match':
            return result
        actual = dict(product_family=normalize(result.get('product_family')),
                      chip=chip_name(result.get('chip')), memory_gb=result.get('memory_gb'),
                      storage_gb=result.get('storage_gb'), cpu_cores=result.get('cpu_cores'), gpu_cores=result.get('gpu_cores'))
        for key in ('product_family', 'chip', 'memory_gb', 'storage_gb'):
            target = getattr(self, key)
            if target is None:
                continue
            label = {'product_family':'商品品类','chip':'芯片型号','memory_gb':'内存容量','storage_gb':'内置硬盘容量','cpu_cores':'CPU核心数','gpu_cores':'GPU核心数'}[key]
            observed = actual[key]
            if observed is None or observed == '':
                result.update(match_status='unknown', reason=f'出售商品的{label}证据不足，不计入目标行情')
                return result
            if observed != target:
                result.update(match_status='no_match', reason=f'出售商品的{label}为{observed}，目标为{target}')
                return result
        from src.domain.models.market_evidence import verified_excerpts, unique_catalog_cores
        verified, referenced = verified_excerpts(result,item,self.chip)
        if not verified:
            code = 'replacement_reference' if referenced else 'unverified_excerpt'
            reason = ('目标芯片仅出现在换机或比较语境中，不能确认出售品型号' if referenced else
                      'AI未提供可在详情中核实的身份片段，请重新分析；不代表卖家没有写配置')
            result.update(match_status='unknown',reason=reason,uncertainty_code=code)
            return result
        result['verified_evidence'] = verified
        # Fill only a uniquely published specification for the observed chip.
        # Never copy the desired CPU/GPU numbers into an unconfirmed listing.
        pair = unique_catalog_cores(actual['product_family'],actual['chip'])
        derived=[]
        for index,key in enumerate(('cpu_cores','gpu_cores')):
            if actual[key] is None and pair:
                actual[key]=pair[index]
                result[key]=pair[index]
                derived.append(key)
            expected=getattr(self,key)
            if expected is None: continue
            if actual[key] is None:
                result.update(match_status='unknown',reason='该芯片有多个核心版本，详情未确认具体版本',uncertainty_code='core_variant_unknown')
                return result
            if actual[key]!=expected:
                result.update(match_status='no_match',reason=f'出售商品的{key}为{actual[key]}，目标为{expected}')
                return result
        if derived:
            result['catalog_derived_fields']=derived
            result['catalog_evidence_note']='CPU/GPU核心数依据已核实芯片的商品库唯一规格补全；不是从目标值推测'
        text = normalize(str(item.get('商品标题') or '')+' '+str(item.get('商品描述') or ''))
        text_chips=chips_in(text)
        if self.chip and len(text_chips)==1 and self.chip not in text_chips:
            result.update(match_status='no_match',reason='商品文字明确为其他芯片代际，不计入目标行情')
        return result
