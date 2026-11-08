"""只读行情快照 API。"""
import asyncio
from fastapi import APIRouter, Query
from src.infrastructure.persistence.market_report_repository import MarketReportRepository

router = APIRouter(prefix='/api/market', tags=['market'])


@router.get('/reports')
async def reports(task_key: str | None = None, limit: int = Query(50, ge=1, le=200)):
    return await asyncio.to_thread(MarketReportRepository().reports, task_key, limit)
