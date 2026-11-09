"""Collection hands a durable packet to a browser-independent semantic worker."""
import asyncio
import copy
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from src.infrastructure.persistence.market_batch_repository import MarketBatchRepository
from src.infrastructure.persistence.sqlite_connection import get_database_path
from src.services.market_report_service import MarketRun
from src.services.market_analysis_service import MarketAnalyzer

TASK_FIELDS = ('id','task_name','keyword','description','product_selection','analyze_images','max_pages',
               'min_price','max_price','personal_only','free_shipping','new_publish_option','region',
               'prefilter','decision_mode','keyword_rules','monitor_mode')
ERROR_LABELS = {
    'ai_not_configured':'未配置可用的AI模型', 'authentication':'AI服务认证失败，请检查API配置',
    'invalid_request':'AI服务拒绝请求，请检查模型是否支持图片及当前参数',
    'rate_limit':'AI服务限流，稍后仅重试分类', 'timeout':'AI请求超时',
    'connection':'AI服务连接失败', 'response_format':'AI返回内容不符合分类格式',
    'image_download':'商品图片未能下载，等待重新分类', 'provider_error':'AI服务调用失败',
    'analysis_disabled':'AI分类已关闭',
}


def error_code(exc):
    status = getattr(exc, 'status_code', None)
    name = type(exc).__name__.lower()
    if name == 'aiunavailableerror': return 'ai_not_configured'
    if status in (401,403): return 'authentication'
    if status == 429: return 'rate_limit'
    if status == 400: return 'invalid_request'
    if 'timeout' in name: return 'timeout'
    if 'connection' in name: return 'connection'
    if any(s in name for s in ('validation','json','parse','response')) or isinstance(exc,(ValueError,TypeError)):
        return 'response_format'
    return 'provider_error'


def failure(code):
    return dict(match_status='unknown',price_kind='unknown',condition='unknown',model='',
                reason=ERROR_LABELS[code],error=code)


@contextmanager
def classification_lock(batch_id):
    directory = Path(get_database_path()).resolve().parent / '.market-classification-locks'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / (hashlib.sha256(batch_id.encode()).hexdigest()+'.lock')).open('a') as file:
        try:
            fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(file,fcntl.LOCK_UN)


class MarketCollection:
    def __init__(self, market, repository=None):
        self.market = market
        self.repository = repository or MarketBatchRepository()
        task = {k: copy.deepcopy(market.task[k]) for k in TASK_FIELDS if k in market.task}
        self.repository.create(market.run_id,task,market.prompt,asdict(market.target))
        self._lease = classification_lock(market.run_id)
        if not self._lease.__enter__():
            raise RuntimeError('Collection packet is already locked')

    async def collect(self, record):
        # No image fetch, seller browsing or model call in the collection stage.
        await asyncio.to_thread(self.repository.add,self.market.run_id,record)

    async def seal(self, status):
        try:
            await asyncio.to_thread(self.repository.seal,self.market.run_id,status,self.market.errors,self.market.title_rejected)
        finally:
            self._lease.__exit__(None,None,None)


async def classify_market_batch(batch_id, *, image_downloader=None, saver=None, notifier=None,
                                analyzer=None, skip_ai=False, rerun_all=False):
    from src.ai_handler import download_all_images
    from src.utils import save_to_jsonl
    from src.domain.models.market_target import MarketTarget
    repository = MarketBatchRepository()
    with classification_lock(batch_id) as acquired:
        if not acquired or not await asyncio.to_thread(repository.claim,batch_id):
            return None
        batch = await asyncio.to_thread(repository.get,batch_id)
        if rerun_all:
            await asyncio.to_thread(repository.reset,batch_id)
        task = json.loads(batch['task_json'])
        market = MarketRun(task,target=MarketTarget(**json.loads(batch['target_json'])))
        market.run_id = batch_id
        market.title_rejected = batch['title_rejected']
        market.errors = json.loads(batch['collection_errors'])
        client = analyzer or MarketAnalyzer()
        download = image_downloader or download_all_images
        save = saver or save_to_jsonl
        rows = await asyncio.to_thread(repository.candidates,batch_id)
        print(f'market.classification.start batch={batch_id} candidates={len(rows)} target={market.prompt_target_label()}')
        terminal_error = None
        try:
            # Deliberately sequential. Collection has ended and the browser lease is released.
            for index, row in enumerate(rows,1):
                record = json.loads(row['record_json'])
                if row['analysis_json']:
                    result = json.loads(row['analysis_json'])
                else:
                    paths = []
                    attempts = 0
                    code = terminal_error or ('analysis_disabled' if skip_ai else None)
                    result = None
                    try:
                        if not code:
                            item = record['商品信息']
                            urls = item.get('商品图片列表') or []
                            if task.get('analyze_images',True) and urls:
                                try:
                                    paths = await download(str(item['商品ID']),urls,task['task_name'])
                                except Exception:
                                    code = 'image_download'
                                if not paths:
                                    code = 'image_download'
                        if not code:
                            for attempt in range(2):
                                attempts += 1
                                try:
                                    prompt = market.prompt
                                    if attempt:
                                        from src.services.market_analysis_service import MarketClassification
                                        prompt += '\n若前次没有可核实的引文，请在evidence_quotes中复制标题/描述里的短原文，不要把分析说明作为引文。严格按以下JSON契约返回，未知的数值或型号使用null，枚举值未知使用unknown，不要猜测：' + json.dumps(MarketClassification.model_json_schema(),ensure_ascii=False)
                                    result = await client.analyze(record,paths,prompt)
                                    result = market.target.validate_identity(result,record['商品信息'])
                                    code = None
                                    if result.get('uncertainty_code') == 'unverified_excerpt' and attempt == 0:
                                        await asyncio.sleep(1)
                                        continue
                                    break
                                except Exception as exc:
                                    code = error_code(exc)
                                    if code in {'authentication','ai_not_configured','invalid_request'}:
                                        terminal_error = code
                                        break
                                    if attempt == 0:
                                        await asyncio.sleep(1)
                        result = result or failure(code or 'provider_error')
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        code = error_code(exc)
                        result = failure(code)
                    finally:
                        for path in paths:
                            try: os.remove(path)
                            except OSError: pass
                    await asyncio.to_thread(repository.checkpoint,batch_id,row['item_id'],
                                            None if result.get('error') else result,result.get('error'),attempts)
                record['market_analysis'] = result
                await market.observe(record)
                record['ai_analysis'] = dict(analysis_source='market',is_recommended=False,
                                             reason=record['market_analysis']['reason'],keyword_hit_count=0)
                await save(record,task['keyword'])
                print(f'market.classification.progress batch={batch_id} completed={index}/{len(rows)} outcome={result.get("error") or result.get("match_status")}')
            latest = next((b for b in await asyncio.to_thread(repository.list,1000) if b['task_key'] == market.task_key),None)
            report = await market.finish(batch['collection_status'],notifier,
                                         allow_notification=bool(latest and latest['id'] == batch_id))
            state = 'classification_failed' if any(s['classification'].get('error') for s in market.observations.values()) else 'completed'
            await asyncio.to_thread(repository.finish,batch_id,state)
            return report
        except BaseException:
            # Checkpoints survive a shutdown; retry uses the same raw packet/target.
            await asyncio.to_thread(repository.finish,batch_id,'classification_failed')
            raise
        finally:
            if analyzer is None:
                await client.close()
