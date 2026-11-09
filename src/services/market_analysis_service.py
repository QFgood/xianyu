"""详情页行情分类；与旧的购买画像/推荐 schema 分离。"""
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator
from typing import Literal
from src.infrastructure.external.ai_client import AIClient


class EvidenceQuote(BaseModel):
    source: Literal['title','description','image']
    text: str = Field(min_length=1,max_length=1000)


class MarketClassification(BaseModel):
    model_config = ConfigDict(extra='ignore')
    match_status: Literal['match', 'no_match', 'unknown']
    price_kind: Literal['whole_unit', 'deposit', 'accessory', 'option_min', 'not_sale', 'unknown']
    fault_status: Literal['faulty', 'not_reported'] = 'not_reported'
    model: str
    reason: str = Field(max_length=1000)
    product_family: str | None = None
    chip: str | None = None
    memory_gb: StrictInt | None = None
    storage_gb: StrictInt | None = None
    identity_evidence: str = Field(default='', max_length=1000)
    evidence_source: Literal['text', 'image', 'unknown'] = 'unknown'
    evidence_quotes: list[EvidenceQuote] = Field(default_factory=list,max_length=10)

    @model_validator(mode='before')
    @classmethod
    def legacy_explicit_fault(cls,value):
        if isinstance(value,dict) and (value.get('condition') == 'faulty' or value.get('functional_status') == 'faulty'):
            return {**value,'fault_status':'faulty'}
        return value

    @field_validator('memory_gb','storage_gb',mode='before')
    @classmethod
    def exact_numeric_string(cls,value):
        # Accept JSON numeric strings, never infer units, capacities or a chip.
        return int(value) if isinstance(value,str) and value.isdecimal() else value


def build_market_prompt(task: dict, *, target=None) -> str:
    template = Path('static/market_prompt.txt').read_text(encoding='utf-8')
    # This internal contract ships with the classifier. Old mounted prompts must
    # not silently supply a previous enum/schema after an app upgrade.
    from src.domain.models.market_target import MarketTarget
    import json
    template += '\n必须逐项核对的结构化目标：' + json.dumps((target or MarketTarget.from_task(task)).matching_spec(), ensure_ascii=False)
    from src.domain.models.product_selection import ProductSelection
    selected_label = ProductSelection.model_validate(task['product_selection']).label() if task.get('product_selection') else str(task.get('task_name') or task['keyword'])
    return template.replace('{{TARGET}}', selected_label).replace(
        '{{DESCRIPTION}}', str(task.get('description') or task['keyword']))


class MarketAnalyzer:
    def __init__(self):
        self.client = None

    async def analyze(self, record, images, prompt):
        if self.client is None:
            self.client = AIClient()
        # The generic client must not append the legacy buying instructions.
        result = await self.client.analyze_or_raise({'商品信息': record.get('商品信息', {}),
                                            '卖家信息': record.get('卖家信息', {}),
                                            'analysis_purpose': 'market'}, images, prompt)
        if result is None:
            raise ValueError('行情分类不可用，样本不计入统计')
        validated = MarketClassification.model_validate(result).model_dump()
        if not images:
            validated['evidence_quotes'] = [q for q in validated['evidence_quotes'] if q['source'] != 'image']
        if validated['evidence_source'] == 'image' and not images and not validated['evidence_quotes']:
            validated.update(match_status='unknown', reason='未提供图片，不能采用图片身份依据')
        return validated

    async def close(self):
        if self.client is not None:
            await self.client.close()
