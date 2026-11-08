import asyncio
import time


def test_create_list_update_delete_task(api_client, api_context, sample_task_payload):
    response = api_client.post("/api/tasks/", json=sample_task_payload)
    assert response.status_code == 200
    created = response.json()["task"]
    assert created["task_name"] == sample_task_payload["task_name"]
    assert created["analyze_images"] is True
    assert created["next_run_at"] == "2026-03-19T08:15:00+08:00"

    response = api_client.get("/api/tasks")
    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["keyword"] == sample_task_payload["keyword"]
    assert tasks[0]["analyze_images"] is True
    assert tasks[0]["next_run_at"] == "2026-03-19T08:15:00+08:00"

    response = api_client.patch("/api/tasks/0", json={"enabled": False, "analyze_images": False})
    assert response.status_code == 200
    updated = response.json()["task"]
    assert updated["enabled"] is False
    assert updated["analyze_images"] is False
    assert updated["next_run_at"] is None

    response = api_client.delete("/api/tasks/0")
    assert response.status_code == 200

    response = api_client.get("/api/tasks")
    assert response.status_code == 200
    assert response.json() == []


def test_start_stop_task_updates_status(api_client, api_context, sample_task_payload):
    response = api_client.post("/api/tasks/", json=sample_task_payload)
    assert response.status_code == 200

    response = api_client.post("/api/tasks/start/0")
    assert response.status_code == 200

    response = api_client.get("/api/tasks/0")
    assert response.status_code == 200
    assert response.json()["is_running"] is True

    response = api_client.post("/api/tasks/stop/0")
    assert response.status_code == 200

    response = api_client.get("/api/tasks/0")
    assert response.status_code == 200
    assert response.json()["is_running"] is False

    process_service = api_context["process_service"]
    assert process_service.started == [(0, sample_task_payload["task_name"])]
    assert process_service.stopped == [0]


def test_generate_keyword_mode_task_without_ai_criteria(api_client):
    payload = {
        "prefilter": {"mode": "off"},
        "task_name": "A7M4 关键词筛选",
        "keyword": "sony a7m4",
        "description": "",
        "decision_mode": "keyword",
        "keyword_rules": ["a7m4", "验货宝"],
        "max_pages": 2,
        "personal_only": True,
    }

    response = api_client.post("/api/tasks/generate", json=payload)
    assert response.status_code == 200
    created = response.json()["task"]
    assert created["prefilter"]["mode"] == "off"
    assert created["decision_mode"] == "keyword"
    assert created["ai_prompt_criteria_file"] == ""
    assert created["keyword_rules"] == ["a7m4", "验货宝"]


def test_generate_ai_task_returns_job_and_completes_async(api_client, api_context, monkeypatch):
    payload = {
        "prefilter": {"mode": "off"},
        "task_name": "Apple Watch S10",
        "keyword": "apple watch s10",
        "monitor_mode": "buying",
        "description": "只看国行蜂窝版，电池健康高于 95%，拒绝维修机。",
        "analyze_images": False,
        "decision_mode": "ai",
        "max_pages": 2,
        "personal_only": True,
    }

    async def fake_generate_criteria(*_args, **_kwargs):
        await asyncio.sleep(0.05)
        return "[V6.3 核心升级]\\nApple Watch criteria"

    monkeypatch.setattr(
        "src.services.task_generation_runner.generate_criteria",
        fake_generate_criteria,
    )

    response = api_client.post("/api/tasks/generate", json=payload)

    assert response.status_code == 202
    job = response.json()["job"]
    assert isinstance(job["job_id"], str)
    assert job["status"] in {"queued", "running"}
    assert job["task"] is None

    status_response = api_client.get(f"/api/tasks/generate-jobs/{job['job_id']}")
    assert status_response.status_code == 200

    for _ in range(50):
        status_response = api_client.get(f"/api/tasks/generate-jobs/{job['job_id']}")
        latest_job = status_response.json()["job"]
        if latest_job["status"] == "completed":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("任务生成作业未在预期时间内完成")

    assert latest_job["task"]["prefilter"]["mode"] == "off"
    assert latest_job["task"]["task_name"] == payload["task_name"]
    assert latest_job["task"]["ai_prompt_criteria_file"].endswith("_criteria.txt")
    assert latest_job["task"]["analyze_images"] is False
    assert api_context["scheduler_service"].reload_calls == 1


def test_create_task_accepts_cron_alias(api_client, sample_task_payload):
    payload = dict(sample_task_payload)
    payload["cron"] = "@daily"

    response = api_client.post("/api/tasks/", json=payload)

    assert response.status_code == 200
    assert response.json()["task"]["cron"] == "0 0 * * *"


def test_create_task_rejects_fixed_account_strategy_without_state_file(api_client, sample_task_payload):
    payload = dict(sample_task_payload)
    payload["account_strategy"] = "fixed"

    response = api_client.post("/api/tasks/", json=payload)

    assert response.status_code == 422


def test_create_task_accepts_rotate_account_strategy(api_client, sample_task_payload):
    payload = dict(sample_task_payload)
    payload["account_strategy"] = "rotate"

    response = api_client.post("/api/tasks/", json=payload)

    assert response.status_code == 200
    task = response.json()["task"]
    assert task["account_strategy"] == "rotate"


def test_update_task_accepts_six_field_cron_expression(api_client, sample_task_payload):
    create_response = api_client.post("/api/tasks/", json=sample_task_payload)
    assert create_response.status_code == 200

    response = api_client.patch("/api/tasks/0", json={"cron": "0 0 8 * * *"})

    assert response.status_code == 200

    task_response = api_client.get("/api/tasks/0")
    assert task_response.status_code == 200
    assert task_response.json()["cron"] == "0 0 8 * * *"


def test_create_task_rejects_invalid_cron_expression(api_client, sample_task_payload):
    payload = dict(sample_task_payload)
    payload["cron"] = "every day at 8"

    response = api_client.post("/api/tasks/", json=payload)

    assert response.status_code == 422


def test_delete_task_stops_runtime_and_reindexes_process_state(
    api_client,
    api_context,
    sample_task_payload,
):
    second_payload = dict(sample_task_payload)
    second_payload["task_name"] = "Sony A7CR"
    second_payload["keyword"] = "sony a7cr"
    second_payload["ai_prompt_criteria_file"] = "prompts/sony_a7cr_criteria.txt"

    assert api_client.post("/api/tasks/", json=sample_task_payload).status_code == 200
    assert api_client.post("/api/tasks/", json=second_payload).status_code == 200
    assert api_client.post("/api/tasks/start/0").status_code == 200

    response = api_client.delete("/api/tasks/0")

    assert response.status_code == 200
    process_service = api_context["process_service"]
    assert process_service.stopped == [0]
    assert process_service.reindexed == []


def test_prefilter_patch_contract_and_validation(api_client, sample_task_payload, monkeypatch):
    from unittest.mock import AsyncMock
    policy = dict(version=1, mode='shadow', kind='anchored_title_tokens', product_aliases=['Product'], reject_tokens=['V1'], keep_tokens=['V2'])
    generate = AsyncMock(side_effect=AssertionError('prefilter must not regenerate criteria'))
    monkeypatch.setattr('src.api.routes.tasks.generate_criteria', generate)
    response = api_client.post('/api/tasks/', json=sample_task_payload | {'prefilter': policy})
    assert response.status_code == 200
    assert response.json()['task']['prefilter']['mode'] == 'shadow'
    assert api_client.patch('/api/tasks/0', json={'enabled': False}).status_code == 200
    assert api_client.get('/api/tasks/0').json()['prefilter']['mode'] == 'shadow'
    for invalid in (policy | {'mode': 'invalid'}, policy | {'unknown': True}, policy | {'keep_tokens': ['v1']}, [], 'invalid'):
        assert api_client.patch('/api/tasks/0', json={'prefilter': invalid}).status_code == 422
        assert api_client.get('/api/tasks/0').json()['prefilter']['mode'] == 'shadow'
    assert api_client.patch('/api/tasks/0', json={'prefilter': policy | {'mode': 'enforce'}}).json()['task']['prefilter']['mode'] == 'enforce'
    off = {'version': 1, 'mode': 'off', 'kind': 'anchored_title_tokens'}
    assert api_client.patch('/api/tasks/0', json={'prefilter': off}).json()['task']['prefilter']['product_aliases'] == []
    assert api_client.patch('/api/tasks/0', json={'prefilter': None}).json()['task']['prefilter'] is None
    generate.assert_not_called()


def test_market_creation_and_edit_do_not_generate_buying_prompt(api_client, monkeypatch):
    from unittest.mock import AsyncMock
    forbidden = AsyncMock(side_effect=AssertionError('market must not generate buyer persona'))
    monkeypatch.setattr('src.api.routes.tasks.generate_criteria', forbidden)
    monkeypatch.setattr('src.services.task_generation_runner.generate_criteria', forbidden)
    response = api_client.post('/api/tasks/generate', json={
        'task_name':'Mac mini M6 16GB 256GB', 'keyword':'Mac mini M6', 'description':'16GB+256GB内置SSD',
        'product_selection':{'catalog_id':'apple-mac-mini-m6','memory_gb':16,'storage_gb':256}} )
    assert response.status_code == 200
    task = response.json()['task']
    assert task['monitor_mode'] == 'market'
    assert task['personal_only'] is False and task['free_shipping'] is False
    response = api_client.patch(f"/api/tasks/{task['id']}", json={'description':'16GB+512GB'})
    assert response.status_code == 200
    forbidden.assert_not_called()
    assert api_client.get('/api/market/reports').status_code == 200
