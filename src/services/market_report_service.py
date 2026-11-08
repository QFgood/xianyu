"""按配置核实的本轮在售标价；只在有效商品集合/价格变化时通知。"""
import asyncio
import hashlib
import json
import math
from datetime import datetime, timezone
from statistics import median, quantiles
from uuid import uuid4

from src.infrastructure.persistence.market_report_repository import MarketReportRepository
from src.services.notification_service import build_notification_service
from src.services.price_history_service import parse_price_value
from src.services.market_analysis_service import build_market_prompt


def summarize(samples):
    prices = sorted(sample['price'] for sample in samples)
    result = {'count': len(prices), 'min': min(prices) if prices else None,
              'max': max(prices) if prices else None,
              'median': median(prices) if prices else None, 'p25': None, 'p75': None}
    if len(prices) >= 4:
        q = quantiles(prices, n=4, method='inclusive')
        result.update(p25=q[0], p75=q[2])
    return result


class MarketRun:
    def __init__(self, task, repository=None):
        self.repository = repository or MarketReportRepository()
        self.task = task
        from src.domain.models.market_target import MarketTarget
        self.target = MarketTarget.from_task(task)
        self.title_rejected = 0
        self.observations = {}
        self.errors = []
        self.run_id = uuid4().hex
        self.prompt = build_market_prompt(task)
        # Keep changed search scopes / target / prompt revisions out of comparisons.
        scope_keys = ('keyword', 'task_name', 'description', 'max_pages', 'min_price', 'max_price',
                      'personal_only', 'free_shipping', 'new_publish_option', 'region',
                      'prefilter', 'product_selection', 'decision_mode', 'keyword_rules', 'analyze_images')
        scope = {key: task.get(key) for key in scope_keys}
        self.task_key = str(task.get('id') if task.get('id') is not None else task['task_name'])
        scope.update(task_key=self.task_key, prompt=self.prompt, version=2)
        self.cohort = hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def prompt_target_label(self):
        if self.task.get('product_selection'):
            from src.domain.models.product_selection import ProductSelection
            return ProductSelection.model_validate(self.task['product_selection']).label()
        return self.task.get('description') or self.task['keyword']

    def fail(self, reason):
        self.errors.append(reason)

    async def observe(self, record):
        item = record['商品信息']
        classification = self.target.validate_identity(record['market_analysis'], item)
        if classification.get('match_status') == 'match':
            if classification.get('condition') not in {'new','like_new','light_wear'}:
                classification['reason'] = '成色不符合全新、几乎全新、轻微痕迹三档，不纳入行情'
            elif classification.get('functional_status') != 'normal':
                classification['reason'] = '功能异常或未确认功能正常，不纳入行情'
        record['market_analysis'] = classification
        price = parse_price_value(item.get('当前售价'))
        valid = (classification.get('match_status') == 'match' and
                 classification.get('price_kind') == 'whole_unit' and
                 classification.get('condition') in {'new','like_new','light_wear'} and
                 classification.get('functional_status') == 'normal' and price is not None and
                 math.isfinite(price) and price > 0)
        key = str(item.get('商品ID') or item.get('商品链接'))
        self.observations[key] = dict(item_id=key, title=item.get('商品标题'),
                                     price=price if price is not None and math.isfinite(price) else None,
                                     link=f'https://www.goofish.com/item?id={key}' if key.isdigit() else item.get('商品链接'),
                                     seller=record.get('卖家信息', {}),
                                     classification=classification, valid=valid)
        if classification.get('error'):
            self.fail('部分商品分类失败')

    async def finish(self, status, notifier=None):
        samples = sorted((s for s in self.observations.values() if s['valid']), key=lambda s: s['item_id'])
        complete = status == 'completed' and not self.errors
        report = dict(run_id=self.run_id, task_key=self.task_key, cohort=self.cohort,
                      task_name=self.task['task_name'], created_at=datetime.now(timezone.utc).isoformat(),
                      status='complete' if complete else 'partial', errors=sorted(set(self.errors)),
                      target=self.prompt_target_label(),
                      product_selection=self.task.get('product_selection'),
                      title_rejected=self.title_rejected,
                      scope={k: self.task.get(k) for k in ('keyword','max_pages','min_price','max_price','region','personal_only','free_shipping','new_publish_option')},
                      summary=summarize(samples),
                      sample_count=len(samples), samples=samples,
                      excluded=[s for s in self.observations.values() if not s['valid']],
                      groups={condition: summarize([s for s in samples if s['classification']['condition'] == condition])
                              for condition in ('new','like_new','light_wear')})
        # Compare with last successfully delivered sample set; failed sends can retry.
        baseline = await asyncio.to_thread(self.repository.delivered, self.cohort)
        current = {s['item_id']: {'price': s['price'], 'condition': s['classification']['condition']} for s in samples}
        before = baseline or {}
        report['changes'] = dict(added=len(current.keys()-before.keys()),
                                 missing=len(before.keys()-current.keys()),
                                 repriced=sum(current[k]['price'] != before[k]['price'] for k in current.keys() & before.keys()))
        changed = current != before
        report['changed'] = complete and changed
        await asyncio.to_thread(self.repository.save, report)
        if complete and changed:
            send = notifier or build_notification_service().send_notification
            result = await send({'商品标题': report['task_name'], '通知标题': '行情更新 | ' + report['task_name'],
                                 '当前售价': '本轮在售标价', '商品链接': '#', 'market_report': report}, format_report(report))
            if result and all(channel.get('success') for channel in result.values()):
                await asyncio.to_thread(self.repository.acknowledge, self.cohort, current)
        return report


def format_report(report):
    summary = report['summary']
    n = report['sample_count']
    lines = [f"有效样本：{n} 件（本轮采样，非全市场）"]
    if summary['count']:
        lines.append(f"有效样本标价中位数：¥{summary['median']:g}；范围：¥{summary['min']:g}–¥{summary['max']:g}")
    if summary['p25'] is not None:
        lines.append(f"有效样本中间50%标价：¥{summary['p25']:g}–¥{summary['p75']:g}")
    if summary['count'] < 4:
        lines.append('样本较少，暂不判断市场趋势。')
    labels = {'new':'全新', 'like_new':'几乎全新', 'light_wear':'轻微痕迹'}
    for condition, group in report['groups'].items():
        if group['count']:
            lines.append(f"{labels[condition]}：{group['count']} 件，中位 ¥{group['median']:g}")
    change = report['changes']
    lines.append(f"较上次推送：新增观察 {change['added']}，价格变化 {change['repriced']}，本轮未见 {change['missing']}")
    lines.append(f"不匹配/信息不明/成色或功能不符：{len(report['excluded'])} 件；标题明确非目标：{report.get('title_rejected', 0)} 件")
    lines.append('仅为卖家在售标价，不是成交价；本轮未见不代表已售。')
    return '\n'.join(lines)
