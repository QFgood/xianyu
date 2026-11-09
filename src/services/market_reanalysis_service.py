"""Reanalysis uses durable packets; older reports may import available saved detail records."""
import asyncio
import json
from fastapi import HTTPException
from src.infrastructure.persistence.market_batch_repository import MarketBatchRepository
from src.infrastructure.persistence.market_report_repository import MarketReportRepository
from src.infrastructure.persistence.sqlite_connection import sqlite_connection, init_schema
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.services.market_classification_service import MarketCollection, classification_lock
from src.services.market_report_service import MarketRun


async def prepare_reanalysis(report_id, *, excluded_only=False):
    repository = MarketBatchRepository()
    existing = await asyncio.to_thread(repository.get,report_id)
    if existing:
        if existing['state'] == 'collecting':
            with classification_lock(report_id) as available:
                if available:
                    await asyncio.to_thread(repository.seal,report_id,'interrupted',
                                            ['采集进程已中断，保存的详情可重新分析，但不作为完整行情推送'],existing['title_rejected'])
        return report_id
    def read():
        with sqlite_connection() as conn:
            init_schema(conn)
            row = conn.execute('SELECT payload FROM market_reports WHERE run_id=?',(report_id,)).fetchone()
            return json.loads(row[0]) if row else None
    report = await asyncio.to_thread(read)
    if report is None:
        raise HTTPException(status_code=404,detail='没有找到报告或采集批次。')
    try:
        task = await SqliteTaskRepository().find_by_id(int(report['task_key']))
    except (ValueError,TypeError):
        task = None
    if task is None:
        raise HTTPException(status_code=409,detail='旧报告没有目标快照，原任务已不存在，无法安全重新分析。')
    if excluded_only:
        raise HTTPException(status_code=409,detail='此旧报告没有完整采集快照，请先完整重新分析一次，之后可仅重筛未纳入样本。')
    observations = report.get('samples',[]) + report.get('excluded',[])
    ids = {str(s['item_id']) for s in observations}
    def saved_details():
        with sqlite_connection() as conn:
            init_schema(conn)
            rows = conn.execute('SELECT item_id,raw_json FROM result_items WHERE task_name=?',(report['task_name'],)).fetchall()
            return {str(row['item_id']):json.loads(row['raw_json']) for row in rows if str(row['item_id']) in ids}
    records = await asyncio.to_thread(saved_details)
    if not records:
        raise HTTPException(status_code=409,detail='此旧报告没有可读取的商品详情，请先运行一次采集。')
    market = MarketRun(task.model_dump())
    market.fail('旧报告恢复：使用当前任务目标与本地保存详情，未重新采集；不视为完整的新行情轮次')
    if len(records) != len(ids):
        market.fail('部分旧报告商品详情已缺失')
    collection = MarketCollection(market)
    for record in records.values():
        record.pop('market_analysis',None)
        record.pop('ai_analysis',None)
        record['任务名称'] = task.task_name
        record['搜索关键字'] = task.keyword
        await collection.collect(record)
    await collection.seal('legacy_reanalysis')
    return market.run_id
