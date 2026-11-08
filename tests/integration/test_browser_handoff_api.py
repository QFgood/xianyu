import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api.routes import browser
from src.services.browser_session_service import BrowserSessionControl


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DATABASE_FILE', str(tmp_path / 'api.db'))
    monkeypatch.setenv('BROWSER_MODE', 'remote')
    from types import SimpleNamespace
    monkeypatch.setattr(browser, 'get_settings', lambda: SimpleNamespace(web_username='testadmin', web_password='testpassword'))
    app = FastAPI()
    app.include_router(browser.router)
    return TestClient(app)


def auth():
    return {'Authorization': 'Basic ' + base64.b64encode(b'testadmin:testpassword').decode()}


def test_handoff_requires_admin_current_incident_and_idle_browser(client, monkeypatch):
    from unittest.mock import AsyncMock
    reachable = AsyncMock()
    monkeypatch.setattr(browser, 'reachable', reachable)
    status = client.get('/api/browser/status').json()
    assert status['enabled'] and status['state'] == 'needs_login'
    body = {'incident_id': status['incident_id']}
    assert client.post('/api/browser/ready', json=body).status_code == 401
    assert client.post('/api/browser/ready', json=body, auth=('testadmin', 'wrong')).status_code == 401
    assert client.get('/api/browser/status').json()['state'] == 'needs_login'
    control = BrowserSessionControl()
    with control.lease():
        assert client.post('/api/browser/ready', json=body, headers=auth()).status_code == 409
    assert client.post('/api/browser/ready', json=body, headers=auth()).status_code == 200
    assert client.get('/api/browser/status').json()['state'] == 'ready'
    assert client.post('/api/browser/ready', json=body, headers=auth()).status_code == 409
    ready = client.get('/api/browser/status').json()
    assert client.post('/api/browser/pause', json={'incident_id': ready['incident_id']}, headers=auth()).json()['state'] == 'needs_login'
    reachable.assert_awaited()


def test_browser_unreachable_preserves_wait_state(client, monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    monkeypatch.setattr(browser, 'reachable', AsyncMock(side_effect=HTTPException(503, 'unavailable')))
    initial = client.get('/api/browser/status').json()
    response = client.post('/api/browser/ready', json={'incident_id': initial['incident_id']}, headers=auth())
    assert response.status_code == 503
    assert client.get('/api/browser/status').json()['incident_id'] == initial['incident_id']


def test_manual_completion_does_not_start_tasks_or_visit_platform(client, monkeypatch):
    from unittest.mock import AsyncMock
    reachable = AsyncMock()
    monkeypatch.setattr(browser, 'reachable', reachable)
    state = client.get('/api/browser/status').json()
    response = client.post('/api/browser/ready', json={'incident_id': state['incident_id']}, headers=auth())
    assert response.status_code == 200
    reachable.assert_awaited_once_with()
    assert response.json()['state'] == 'ready'
