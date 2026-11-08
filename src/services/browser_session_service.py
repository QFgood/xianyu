"""Shared persistent browser control and explicit human handoff.

No challenge solving: a person logs in/verifies in the browser desktop and
acknowledges completion. State survives API/worker/container restarts.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import uuid

import httpx

from src.infrastructure.persistence.sqlite_connection import get_database_path, init_schema, sqlite_connection

STATE_KEY = 'browser:manual_handoff:v1'
VALIDATION_MARKERS = ('FAIL_SYS_USER_VALIDATE', 'baxia-dialog', 'J_MIDDLEWARE_FRAME_WIDGET')


def remote_browser_enabled() -> bool:
    mode = os.getenv('BROWSER_MODE', 'local').strip().lower()
    if mode not in {'local', 'remote'}:
        raise ValueError('BROWSER_MODE must be local or remote')
    return mode == 'remote'


def public_browser_url() -> str | None:
    value = os.getenv('BROWSER_PUBLIC_URL', '').strip()
    parsed = urlsplit(value)
    if parsed.scheme in {'http', 'https'} and parsed.netloc and not parsed.username and not parsed.password:
        return value
    return None


class BrowserBusyError(RuntimeError):
    pass


class BrowserStateConflict(RuntimeError):
    pass


class BrowserSessionControl:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or get_database_path()
        self.lock_path = Path(self.db_path).parent / 'browser-session.lock'

    @contextmanager
    def lease(self):
        # File locks are automatically released even if a scraper process dies.
        import fcntl
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open('a+') as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BrowserBusyError('Browser is in use') from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def busy(self) -> bool:
        try:
            with self.lease():
                return False
        except BrowserBusyError:
            return True

    def _update(self, transform):
        with sqlite_connection(self.db_path) as conn:
            init_schema(conn)
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT value FROM app_metadata WHERE key=?', (STATE_KEY,)).fetchone()
            state = json.loads(row['value']) if row else {
                'state': 'needs_login', 'incident_id': uuid.uuid4().hex,
                'reason': 'initial_login', 'task_name': None,
                'updated_at': datetime.now(timezone.utc).isoformat(),
                'notification': 'not_requested',
            }
            result = transform(state)
            conn.execute('INSERT OR REPLACE INTO app_metadata(key,value) VALUES (?,?)',
                         (STATE_KEY, json.dumps(state, ensure_ascii=False)))
            conn.commit()
            return result

    def read(self) -> dict:
        return self._update(lambda state: dict(state))

    def require_help(self, task_name: str, reason: str) -> dict:
        def update(state):
            if state['state'] == 'ready':
                state.update(state='verification_required', incident_id=uuid.uuid4().hex,
                             reason=reason, task_name=task_name, notification='pending',
                             updated_at=datetime.now(timezone.utc).isoformat())
            return dict(state)
        return self._update(update)

    def claim_notification(self) -> bool:
        def update(state):
            if state.get('notification') != 'pending':
                return False
            state['notification'] = 'sending'
            return True
        return self._update(update)

    def record_notification(self, success: bool):
        self._update(lambda state: state.update(notification='sent' if success else 'failed'))

    def acknowledge(self, incident_id: str) -> dict:
        def update(state):
            if state['incident_id'] != incident_id:
                raise BrowserStateConflict('Browser state changed; refresh before confirming')
            state.update(state='ready', reason=None, task_name=None, notification='not_requested',
                         incident_id=uuid.uuid4().hex, updated_at=datetime.now(timezone.utc).isoformat())
            return dict(state)
        return self._update(update)

    def pause(self, incident_id: str) -> dict:
        def update(state):
            if state['incident_id'] != incident_id:
                raise BrowserStateConflict('Browser state changed; refresh before pausing')
            state.update(state='needs_login', reason='manual_pause', task_name=None,
                         incident_id=uuid.uuid4().hex, notification='not_requested',
                         updated_at=datetime.now(timezone.utc).isoformat())
            return dict(state)
        return self._update(update)


async def connect_remote_browser(playwright):
    endpoint = os.getenv('BROWSER_CDP_URL', 'http://browser:9223').rstrip('/')
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Invalid BROWSER_CDP_URL')
    # Chrome only accepts localhost/IP Host values on the discovery endpoint.
    # Connect via Docker DNS, then rewrite only the advertised websocket authority.
    async with httpx.AsyncClient(trust_env=False, timeout=10) as client:
        response = await client.get(endpoint + '/json/version', headers={'Host': 'localhost'})
        response.raise_for_status()
        advertised = urlsplit(response.json()['webSocketDebuggerUrl'])
    if not advertised.path.startswith('/devtools/browser/'):
        raise ValueError('Invalid browser discovery response')
    websocket = urlunsplit(('wss' if parsed.scheme == 'https' else 'ws', parsed.netloc, advertised.path, '', ''))
    browser = await playwright.chromium.connect_over_cdp(websocket, timeout=15000)
    if not browser.contexts:
        raise RuntimeError('Persistent browser context is unavailable')
    return browser, browser.contexts[0]


class RemoteValidationWatch:
    """Stop the owning worker if any search/detail/seller API requests verification."""
    def __init__(self, control: BrowserSessionControl, task_name: str):
        self.control = control
        self.task_name = task_name
        self.owner = asyncio.current_task()
        self.triggered = False

    async def on_response(self, response):
        host = urlsplit(response.url).hostname or ''
        if self.triggered or not (host == 'goofish.com' or host.endswith('.goofish.com')):
            return
        if '/h5/' not in response.url:
            return
        try:
            payload = await response.json()
            ret = str(payload.get('ret', [])) if isinstance(payload, dict) else ''
        except Exception:
            return
        if 'FAIL_SYS_USER_VALIDATE' in ret and not self.triggered:
            self.triggered = True
            await asyncio.to_thread(self.control.require_help, self.task_name, 'FAIL_SYS_USER_VALIDATE')
            if self.owner and not self.owner.done():
                self.owner.cancel('manual_browser_verification')


async def notify_browser_handoff(control: BrowserSessionControl):
    """One enterprise WeChat notification per incident, no cookie/verification URL."""
    if not await asyncio.to_thread(control.claim_notification):
        return
    try:
        from src.services.notification_service import build_notification_service
        state = await asyncio.to_thread(control.read)
        service = build_notification_service()
        service.clients = [client for client in service.clients if client.channel_key == 'wecom']
        entry = public_browser_url()
        results = await service.send_notification(
            {'商品标题': '[需要人工验证] 闲鱼浏览器已暂停', '通知标题': '⚠️ 闲鱼需要人工验证', '当前售价': 'N/A', '商品链接': entry or '#'},
            f"任务：{state.get('task_name') or '监控任务'}。请打开浏览器桌面，由本人完成登录或验证，"
            f"然后在监控面板的账号页确认恢复。入口：{entry or '监控面板账号页'}。暂停期间不会自动重试。",
        )
        success = bool(results.get('wecom', {}).get('success'))
    except Exception:
        # Never include webhook credentials or remote exception payloads in logs.
        success = False
    await asyncio.to_thread(control.record_notification, success)
    print('browser.handoff_notification ' + ('sent' if success else 'failed_or_not_configured'))
