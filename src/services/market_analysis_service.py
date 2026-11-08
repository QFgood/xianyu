"""详情页行情分类；与旧的购买画像/推荐 schema 分离。"""
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from typing import Literal
from src.infrastructure.external.ai_client import AIClient


class MarketClassification(BaseModel):
    model_config = ConfigDict(extra='ignore')
    match_status: Literal['match', 'no_match', 'unknown']
    price_kind: Literal['whole_unit', 'deposit', 'accessory', 'option_min', 'not_sale', 'unknown']
    condition: Literal['new', 'like_new', 'light_wear', 'worn', 'faulty', 'unknown']
    functional_status: Literal['normal', 'faulty', 'unknown'] = 'unknown'
    model: str
    reason: str = Field(max_length=1000)
    product_family: str | None = None
    chip: str | None = None
    memory_gb: StrictInt | None = None
    storage_gb: StrictInt | None = None
    cpu_cores: StrictInt | None = None
    gpu_cores: StrictInt | None = None
    identity_evidence: str = Field(default='', max_length=1000)
    evidence_source: Literal['text', 'image', 'unknown'] = 'unknown'


def ensure_market_prompt() -> Path:
    path = Path('prompts/market_prompt.txt')
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves an administrator's concurrent edit.
        try:
            with path.open('x', encoding='utf-8') as file:
                file.write(Path('static/market_prompt.txt').read_text(encoding='utf-8'))
        except FileExistsError:
            pass
    return path


def build_market_prompt(task: dict) -> str:
    template = ensure_market_prompt().read_text(encoding='utf-8')
    from dataclasses import asdict
    from src.domain.models.market_target import MarketTarget
    import json
    template += '\n必须逐项核对的结构化目标：' + json.dumps(asdict(MarketTarget.from_task(task)), ensure_ascii=False)
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
        result = await self.client.analyze({**record, 'analysis_purpose': 'market'}, images, prompt)
        if result is None:
            raise ValueError('行情分类不可用，样本不计入统计')
        validated = MarketClassification.model_validate(result).model_dump()
        if validated['evidence_source'] == 'image' and not images:
            validated.update(match_status='unknown', reason='未提供图片，不能采用图片身份依据')
        return validated

    async def close(self):
        if self.client is not None:
            await self.client.close()
