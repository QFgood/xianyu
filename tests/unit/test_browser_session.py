import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.browser_session_service import (
    BrowserSessionControl, BrowserBusyError, BrowserStateConflict,
    RemoteValidationWatch, connect_remote_browser, notify_browser_handoff,
)


def test_manual_state_survives_restart_and_uses_exclusive_lease(tmp_path):
    db = str(tmp_path / 'state.db')
    control = BrowserSessionControl(db)
    first = control.read()
    assert first['state'] == 'needs_login'
    control.acknowledge(first['incident_id'])
    with control.lease():
        assert BrowserSessionControl(db).busy()
        with pytest.raises(BrowserBusyError):
            with BrowserSessionControl(db).lease():
                pass
    assert not control.busy()
    incident = control.require_help('task-a', 'FAIL_SYS_USER_VALIDATE')
    assert incident['state'] == 'verification_required'
    assert BrowserSessionControl(db).read() == incident
    assert control.require_help('task-b', 'other') == incident
    assert control.claim_notification()
    assert not control.claim_notification()
    control.record_notification(incident['incident_id'], False)
    assert control.read()['notification'] == 'failed'
    with pytest.raises(BrowserStateConflict):
        control.acknowledge(first['incident_id'])
    ready = control.acknowledge(incident['incident_id'])
    assert ready['state'] == 'ready'
    assert ready['incident_id'] != incident['incident_id']
    control.record_notification(incident['incident_id'], True)
    assert control.read()['notification'] == 'not_requested'
    assert control.pause(ready['incident_id'])['state'] == 'needs_login'


def test_notification_is_enterprise_wechat_only_and_bounded(tmp_path, monkeypatch):
    control = BrowserSessionControl(str(tmp_path / 'notify.db'))
    control.acknowledge(control.read()['incident_id'])
    control.require_help('my task', 'FAIL_SYS_USER_VALIDATE')
    wecom = SimpleNamespace(channel_key='wecom')
    other = SimpleNamespace(channel_key='other')
    service = SimpleNamespace(clients=[wecom, other], send_notification=AsyncMock(return_value={'wecom': {'success': True}}))
    monkeypatch.setattr('src.services.notification_service.build_notification_service', lambda: service)
    monkeypatch.setenv('BROWSER_PUBLIC_URL', 'http://192.168.9.202:7900/')
    asyncio.run(notify_browser_handoff(control))
    asyncio.run(notify_browser_handoff(control))
    assert service.clients == [wecom]
    service.send_notification.assert_awaited_once()
    product, reason = service.send_notification.call_args.args
    assert product['商品链接'] == 'http://192.168.9.202:7900/'
    assert '本人' in reason
    assert control.read()['notification'] == 'sent'


def test_passive_validation_stops_owner_and_persists_handoff(tmp_path):
    control = BrowserSessionControl(str(tmp_path / 'watch.db'))
    control.acknowledge(control.read()['incident_id'])
    async def run():
        watch = RemoteValidationWatch(control, 'seller task')
        response = SimpleNamespace(url='https://h5api.m.goofish.com/h5/seller', json=AsyncMock(return_value={'ret': ['FAIL_SYS_USER_VALIDATE']}))
        asyncio.create_task(watch.on_response(response))
        try:
            await asyncio.sleep(2)
        except asyncio.CancelledError as exc:
            assert exc.args == ('manual_browser_verification',)
            return
        pytest.fail('owner was not stopped')
    asyncio.run(run())
    assert control.read()['state'] == 'verification_required'


def test_cdp_discovery_rewrites_only_authority_and_uses_existing_context(monkeypatch):
    import httpx
    captured = []
    def respond(request):
        captured.append(request)
        return httpx.Response(200, json={'webSocketDebuggerUrl': 'ws://localhost:9222/devtools/browser/test-id'})
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw))
    monkeypatch.setenv('BROWSER_CDP_URL', 'http://browser:9223')
    context = object()
    browser = SimpleNamespace(contexts=[context])
    connect = AsyncMock(return_value=browser)
    actual = asyncio.run(connect_remote_browser(SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))))
    assert actual == (browser, context)
    connect.assert_awaited_once_with('ws://browser:9223/devtools/browser/test-id', timeout=15000)
    assert captured[0].headers['host'] == 'localhost'


def test_verification_is_not_triggered_by_unrelated_sites(tmp_path):
    control = BrowserSessionControl(str(tmp_path / 'unrelated.db'))
    control.acknowledge(control.read()['incident_id'])
    async def run():
        watch = RemoteValidationWatch(control, 'task')
        response = SimpleNamespace(url='https://unrelated.test/h5/test', json=AsyncMock(return_value={'ret': ['FAIL_SYS_USER_VALIDATE']}))
        await watch.on_response(response)
        response.json.assert_not_awaited()
        assert not watch.triggered
    asyncio.run(run())
    assert control.read()['state'] == 'ready'


def test_browser_state_does_not_expire_or_recover_from_unrelated_cookie_files(tmp_path):
    control = BrowserSessionControl(str(tmp_path / 'durable.db'))
    control.acknowledge(control.read()['incident_id'])
    incident = control.require_help('task', 'FAIL_SYS_USER_VALIDATE')
    (tmp_path / 'old_cookie.json').write_text('{"new":"cookie"}')
    assert BrowserSessionControl(control.db_path).read()['state'] == 'verification_required'
    with pytest.raises(BrowserStateConflict):
        control.acknowledge('0' * 32)
    assert control.read()['incident_id'] == incident['incident_id']
