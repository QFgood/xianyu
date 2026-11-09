"""只读行情快照 API。"""
import asyncio
from fastapi import APIRouter, Query
from pydantic import BaseModel
from typing import Literal
from src.infrastructure.persistence.market_report_repository import MarketReportRepository

router = APIRouter(prefix='/api/market', tags=['market'])


@router.get('/reports')
async def reports(task_key: str | None = None, limit: int = Query(50, ge=1, le=200)):
    return await asyncio.to_thread(MarketReportRepository().reports, task_key, limit)


@router.get('/batches')
async def batches(limit: int = Query(100,ge=1,le=1000)):
    from src.infrastructure.persistence.market_batch_repository import MarketBatchRepository
    return await asyncio.to_thread(MarketBatchRepository().list,limit)


class ReanalysisRequest(BaseModel):
    scope: Literal['all','excluded'] = 'all'


@router.post('/reports/{report_id}/reanalyze',status_code=202)
async def reanalyze(report_id: str, payload: ReanalysisRequest | None = None):
    from fastapi import HTTPException
    from src.api.dependencies import get_background_task_service
    from src.services.market_classification_service import classify_market_batch, classification_lock
    from src.services.market_reanalysis_service import prepare_reanalysis
    from src.infrastructure.persistence.market_batch_repository import MarketBatchRepository
    scope=payload.scope if payload else 'all'
    batch_id = await prepare_reanalysis(report_id,excluded_only=scope=='excluded')
    batch = await asyncio.to_thread(MarketBatchRepository().get,batch_id)
    if batch['state'] == 'collecting':
        raise HTTPException(status_code=409,detail='采集尚未完成，请稍后再分析。')
    with classification_lock(batch_id) as available:
        if not available:
            raise HTTPException(status_code=409,detail='此批次正在分析，请等待完成。')
    get_background_task_service().track(classify_market_batch(batch_id,rerun_all=scope=='all',reanalyze_excluded=scope=='excluded'))
    return {'batch_id':batch_id,'message':'已提交重新分析，不会重新采集闲鱼。'}
