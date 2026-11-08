from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api.routes import browser
from src.services.browser_session_service import BrowserSessionControl


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DATABASE_FILE', str(tmp_path / 'api.db'))
    monkeypatch.setenv('BROWSER_MODE', 'remote')
    app = FastAPI()
    app.include_router(browser.router)
    return TestClient(app)



def test_handoff_needs_no_credentials_but_requires_current_incident_and_idle_browser(client, monkeypatch):
    from unittest.mock import AsyncMock
    reachable = AsyncMock()
    monkeypatch.setattr(browser, 'reachable', reachable)
    status = client.get('/api/browser/status').json()
    assert status['enabled'] and status['state'] == 'needs_login'
    body = {'incident_id': status['incident_id']}
    assert client.get('/api/browser/status').json()['state'] == 'needs_login'
    control = BrowserSessionControl()
    with control.lease():
        assert client.post('/api/browser/ready', json=body).status_code == 409
    assert client.post('/api/browser/ready', json=body).status_code == 200
    assert client.get('/api/browser/status').json()['state'] == 'ready'
    assert client.post('/api/browser/ready', json=body).status_code == 409
    ready = client.get('/api/browser/status').json()
    assert client.post('/api/browser/pause', json={'incident_id': ready['incident_id']}).json()['state'] == 'needs_login'
    reachable.assert_awaited()


def test_browser_unreachable_preserves_wait_state(client, monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    monkeypatch.setattr(browser, 'reachable', AsyncMock(side_effect=HTTPException(503, 'unavailable')))
    initial = client.get('/api/browser/status').json()
    response = client.post('/api/browser/ready', json={'incident_id': initial['incident_id']})
    assert response.status_code == 503
    assert client.get('/api/browser/status').json()['incident_id'] == initial['incident_id']


def test_manual_completion_does_not_start_tasks_or_visit_platform(client, monkeypatch):
    from unittest.mock import AsyncMock
    reachable = AsyncMock()
    monkeypatch.setattr(browser, 'reachable', reachable)
    state = client.get('/api/browser/status').json()
    response = client.post('/api/browser/ready', json={'incident_id': state['incident_id']})
    assert response.status_code == 200
    reachable.assert_awaited_once_with()
    assert response.json()['state'] == 'ready'


def test_dashboard_auth_status_is_public_compatibility_endpoint():
    from src.app import app
    client = TestClient(app)
    for response in (client.get('/auth/status'), client.post('/auth/status'), client.post('/auth/status', json={'username': 'anything', 'password': 'anything'})):
        assert response.status_code == 200
        assert response.json()['authentication_required'] is False
        assert 'www-authenticate' not in response.headers


def test_handoff_reports_websocket_failure_and_preserves_wait_state(client, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from src.services.browser_session_service import BrowserConnectionError
    class FakePlaywright:
        async def __aenter__(self):
            return SimpleNamespace()
        async def __aexit__(self, *_):
            pass
    monkeypatch.setattr(browser, 'async_playwright', FakePlaywright)
    monkeypatch.setattr(browser, 'connect_remote_browser', AsyncMock(side_effect=BrowserConnectionError('cdp_websocket_failed', 'HTTP 探测成功，但 WebSocket 握手失败。')))
    initial = client.get('/api/browser/status').json()
    response = client.post('/api/browser/ready', json={'incident_id': initial['incident_id']})
    assert response.status_code == 503
    assert 'cdp_websocket_failed' in response.json()['detail']
    assert 'WebSocket' in response.json()['detail']
    assert client.get('/api/browser/status').json()['incident_id'] == initial['incident_id']
    assert client.get('/api/browser/status').json()['state'] == 'needs_login'
