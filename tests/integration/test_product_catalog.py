import asyncio
import json
import sqlite3
import pytest
from pydantic import ValidationError
from src.domain.models.product_selection import ProductSelection
from src.domain.models.market_target import MarketTarget
from src.domain.models.task import TaskCreate, TaskUpdate
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
from src.services.task_service import TaskService
from src.services.market_analysis_service import build_market_prompt

SELECTION = dict(catalog_id='apple-mac-mini-m6', memory_gb=16, storage_gb=256)


def test_catalog_search_requires_explicit_selection_and_supported_configuration(api_client):
    results = api_client.get('/api/catalog/products?q=MacminiM6').json()
    assert [p['id'] for p in results['items']] == ['apple-mac-mini-m6']
    assert results['items'][0]['source_url'] == 'https://www.apple.com.cn/mac-mini/specs/'
    assert results['items'][0]['memory_options'] == [16,24,32]
    assert api_client.get('/api/catalog/products?q=Mac mini M3').json()['items'] == []
    # Missing selection never silently guesses another model.
    payload = dict(task_name='Mac mini M6',keyword='Mac mini M6',description='16+256')
    assert api_client.post('/api/tasks/generate',json=payload).status_code == 422
    for invalid in (SELECTION | {'catalog_id':'not-existing'}, SELECTION | {'memory_gb':8},
                    SELECTION | {'storage_gb':128}, SELECTION | {'chip':'M4'}):
        response = api_client.post('/api/tasks/generate',json=payload | {'product_selection':invalid})
        assert response.status_code == 422
    created = api_client.post('/api/tasks/generate',json=payload | {'product_selection':SELECTION})
    assert created.status_code == 200
    task = created.json()['task']
    assert task['product_selection'] == SELECTION
    loaded = api_client.get('/api/tasks/'+str(task['id'])).json()
    assert loaded['product_selection'] == SELECTION
    assert api_client.patch('/api/tasks/'+str(task['id']),json={'product_selection':None}).status_code == 400


def test_selected_product_overrides_name_description_and_prompt_guesses(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DATABASE_FILE',str(tmp_path/'catalog.db'))
    service = TaskService(SqliteTaskRepository(str(tmp_path/'catalog.db'), None))
    async def scenario():
        task = await service.create_task(TaskCreate(task_name='随便起的名称 M4 32+512', keyword='Mac mini M4',
            description='换机时提到M2 8+512', product_selection=SELECTION))
        assert task.product_selection.model_dump() == SELECTION
        target = MarketTarget.from_task(task.model_dump())
        assert (target.chip,target.memory_gb,target.storage_gb,target.cpu_cores,target.gpu_cores) == ('m6',16,256,12,12)
        assert target.reject_title('Mac mini M4 16+256')
        prompt = build_market_prompt(task.model_dump())
        assert '目标：Mac mini M6 / 12核CPU 12核GPU / 16GB / 256GB内置SSD' in prompt
        # PATCH keeps the nested typed selection; saving/reloading doesn't lose it.
        updated = await service.update_task(task.id,TaskUpdate(description='新的补充说明'))
        assert updated.product_selection == task.product_selection
        choice = SELECTION | {'memory_gb':24,'storage_gb':512}
        updated = await service.update_task(task.id,TaskUpdate(product_selection=choice))
        assert isinstance(updated.product_selection,ProductSelection)
        loaded = await service.get_task(task.id)
        assert loaded.product_selection.model_dump() == choice
        assert MarketTarget.from_task(loaded.model_dump()).storage_gb == 512
    asyncio.run(scenario())


def test_catalog_core_variant_is_not_interchangeable():
    target = MarketTarget.from_task({'product_selection':dict(catalog_id='apple-mac-mini-m4-pro-12-16',memory_gb=24,storage_gb=512)})
    result = dict(match_status='match',product_family='mac mini',chip='M4 Pro',memory_gb=24,storage_gb=512,
                  cpu_cores=14,gpu_cores=20,identity_evidence='Mac mini M4 Pro',evidence_source='text')
    assert target.validate_identity(result,{'商品标题':'Mac mini M4 Pro'})['match_status'] == 'no_match'
    result['cpu_cores'] = None
    assert target.validate_identity(result,{'商品标题':'Mac mini M4 Pro'})['match_status'] == 'unknown'
