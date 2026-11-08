"""Human-operated browser handoff controls; no CAPTCHA automation."""
import asyncio

from fastapi import APIRouter, HTTPException
from playwright.async_api import async_playwright
from pydantic import BaseModel, ConfigDict, Field

from src.services.browser_session_service import (
    BrowserSessionControl, BrowserBusyError, BrowserStateConflict,
    connect_remote_browser, public_browser_url, remote_browser_enabled,
)

router = APIRouter(prefix='/api/browser', tags=['browser'])


class Confirmation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    incident_id: str = Field(min_length=32, max_length=32)


@router.get('/status')
async def status():
    if not remote_browser_enabled():
        return {'enabled': False}
    control = BrowserSessionControl()
    state = await asyncio.to_thread(control.read)
    return dict(enabled=True, **state, busy=await asyncio.to_thread(control.busy), browser_url=public_browser_url())


async def reachable():
    try:
        async with async_playwright() as playwright:
            await connect_remote_browser(playwright)
            # Exit disconnects the CDP client; never send Browser.close.
    except Exception:
        raise HTTPException(503, '浏览器连接失败，请先确认浏览器容器正在运行。') from None


async def transition(data: Confirmation, ready: bool):
    if not remote_browser_enabled():
        raise HTTPException(409, 'Remote browser mode is disabled')
    control = BrowserSessionControl()
    try:
        with control.lease():
            if ready:
                await reachable()
            operation = control.acknowledge if ready else control.pause
            return await asyncio.to_thread(operation, data.incident_id)
    except BrowserBusyError:
        raise HTTPException(409, '浏览器正在采集中，请先停止运行任务再接管。') from None
    except BrowserStateConflict:
        raise HTTPException(409, '浏览器状态已变化，请刷新后再确认。') from None


@router.post('/ready')
async def ready(data: Confirmation):
    return await transition(data, True)


@router.post('/pause')
async def pause(data: Confirmation):
    return await transition(data, False)
