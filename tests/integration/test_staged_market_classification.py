import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from src.services.market_report_service import MarketRun
from src.services.market_classification_service import MarketCollection, classify_market_batch, classification_lock
from src.infrastructure.persistence.market_batch_repository import MarketBatchRepository
from src.infrastructure.persistence.market_report_repository import MarketReportRepository
from src.domain.models.product_selection import ProductSelection


def task():
    return dict(id=0,task_name='Mac mini M6 16GB 256GB',keyword='Mac mini M6',description='仅整机在售报价',
                product_selection=dict(catalog_id='apple-mac-mini-m6',memory_gb=16,storage_gb=256),analyze_images=False)


def detail(id='1',title='Mac mini M6 16+256',price='3000'):
    return {'商品信息':{'商品ID':id,'商品标题':title,'商品描述':title+'，功能正常，轻微使用痕迹',
                         '当前售价':price,'商品链接':'https://www.goofish.com/item?id='+id},'卖家信息':{}}


def result(chip='M6', match='match'):
    return dict(match_status=match,price_kind='whole_unit',condition='light_wear',functional_status='normal',
                product_family='mac mini',chip=chip,memory_gb=16,storage_gb=256,cpu_cores=12,gpu_cores=12,
                identity_evidence='Mac mini M6 16+256',evidence_source='text',model=chip+' 16+256',reason='识别实际出售品')


@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setenv('APP_DATABASE_FILE',str(tmp_path/'stage.db'))
    return tmp_path


def test_durable_packet_separates_stages_and_only_retries_failures(db):
    async def scenario():
        run=MarketRun(task()); collection=MarketCollection(run)
        await collection.collect(detail())
        await collection.collect(detail())  # per packet ID dedup
        await collection.collect(detail('2','Mac mini M4，换了M6所以卖出M4'))
        await collection.collect(detail('3'))
        repository=MarketBatchRepository()
        assert len(repository.candidates(run.run_id))==3
        # A live collection cannot be taken over; no AI has run yet.
        with classification_lock(run.run_id) as available: assert not available
        await collection.seal('completed')
        raw=repository.get(run.run_id)
        assert json.loads(raw['target_json'])['chip']=='m6'
        assert 'account_state_file' not in json.loads(raw['task_json'])
        state={'fail':True}
        async def analyze(record,images,prompt):
            assert repository.get(run.run_id)['state']=='classifying'
            assert '结构化目标' in prompt and 'm6' in prompt
            id=record['商品信息']['商品ID']
            if id=='3' and state['fail']: raise TimeoutError('sensitive provider payload must not be stored')
            return result('M4','no_match') if id=='2' else result()
        model=SimpleNamespace(analyze=AsyncMock(side_effect=analyze))
        send=AsyncMock(return_value={'wecom':{'success':True}})
        save=AsyncMock(return_value=True)
        report=await classify_market_batch(run.run_id,analyzer=model,saver=save,notifier=send)
        assert report['status']=='partial' and report['sample_count']==1
        assert len(report['categories']['not_target'])==1 and len(report['categories']['failed'])==1
        assert report['categories']['failed'][0]['classification']['error']=='timeout'
        assert model.analyze.await_count==4  # one retry, not another crawl
        send.assert_not_called()
        state['fail']=False
        report=await classify_market_batch(run.run_id,analyzer=model,saver=save,notifier=send)
        assert report['status']=='complete' and report['sample_count']==2
        assert model.analyze.await_count==5  # successful checkpoint rows were retained
        assert send.await_count==1 and len(MarketReportRepository().reports())==1
        await classify_market_batch(run.run_id,analyzer=model,saver=save,notifier=send,rerun_all=True)
        assert model.analyze.await_count==8 and send.await_count==1
    asyncio.run(scenario())


def test_dead_collection_can_recover_but_live_worker_cannot_duplicate(db):
    from src.services.market_reanalysis_service import prepare_reanalysis
    async def scenario():
        run=MarketRun(task()); collection=MarketCollection(run)
        await collection.collect(detail())
        # Simulate crash: OS releases lock while durable collection state remains.
        collection._lease.__exit__(None,None,None)
        assert await prepare_reanalysis(run.run_id)==run.run_id
        assert MarketBatchRepository().get(run.run_id)['collection_status']=='interrupted'
        with classification_lock(run.run_id) as available:
            assert available
            analyzer=SimpleNamespace(analyze=AsyncMock())
            assert await classify_market_batch(run.run_id,analyzer=analyzer) is None
            analyzer.analyze.assert_not_called()
    asyncio.run(scenario())


def test_api_reanalysis_enqueues_browser_free_worker(api_client,db,monkeypatch):
    from src.services.background_task_service import BackgroundTaskService
    queued=[]
    def track(self,coroutine): queued.append(coroutine)
    monkeypatch.setattr(BackgroundTaskService,'track',track)
    run=MarketRun(task()); collection=MarketCollection(run)
    asyncio.run(collection.collect(detail())); asyncio.run(collection.seal('completed'))
    response=api_client.post(f'/api/market/reports/{run.run_id}/reanalyze')
    assert response.status_code==202
    assert response.json()['batch_id']==run.run_id
    assert len(queued)==1
    queued.pop().close()
    assert api_client.get('/api/market/batches').json()[0]['collected']==1


def test_internal_contract_ignores_stale_user_prompt(db,monkeypatch):
    from pathlib import Path
    from src.services.market_analysis_service import build_market_prompt
    # No prompt directory is created/read. Internal classifier rules ship with code.
    root=db/'empty';root.mkdir();(root/'static').symlink_to(Path('static').resolve(),target_is_directory=True)
    monkeypatch.chdir(root)
    text=build_market_prompt(task())
    assert 'fault_status' in text and '结构化目标' in text
    assert 'like_new' not in text and '"cpu_cores"' not in text
    assert not (root/'prompts').exists()


def test_auth_failure_is_explicit_and_does_not_hammer_provider(db):
    async def scenario():
        run=MarketRun(task()); collection=MarketCollection(run)
        for id in ('1','2','3'): await collection.collect(detail(id))
        await collection.seal('completed')
        class AuthenticationError(Exception): status_code=401
        model=SimpleNamespace(analyze=AsyncMock(side_effect=AuthenticationError('do not expose key')))
        report=await classify_market_batch(run.run_id,analyzer=model,saver=AsyncMock(return_value=True),notifier=AsyncMock())
        assert model.analyze.await_count==1
        assert len(report['categories']['failed'])==3
        assert all(s['classification']['error']=='authentication' for s in report['categories']['failed'])
        assert 'do not expose key' not in json.dumps(report)
    asyncio.run(scenario())


def test_legacy_report_reanalysis_restores_saved_details_without_claiming_new_scan(api_client,db):
    from src.domain.models.task import TaskCreate
    from src.services.task_service import TaskService
    from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
    from src.services.market_reanalysis_service import prepare_reanalysis
    from src.utils import save_to_jsonl
    async def scenario():
        service=TaskService(SqliteTaskRepository(legacy_config_file=None))
        saved=await service.create_task(TaskCreate(**task()))
        run=MarketRun(saved.model_dump())
        record=detail();record.update(任务名称=saved.task_name,搜索关键字=saved.keyword,爬取时间='2026-11-09T12:00:00',market_analysis=result())
        await run.observe(record)
        await save_to_jsonl(record,saved.keyword)
        await run.finish('completed',AsyncMock(return_value={}))
        restored=await prepare_reanalysis(run.run_id)
        assert restored != run.run_id
        packet=MarketBatchRepository().get(restored)
        assert packet['collection_status']=='legacy_reanalysis'
        assert len(MarketBatchRepository().candidates(restored))==1
        assert json.loads(packet['target_json'])['chip']=='m6'
    asyncio.run(scenario())


def test_unverifiable_ai_narrative_gets_one_evidence_repair(db):
    async def scenario():
        run=MarketRun(task());collection=MarketCollection(run)
        await collection.collect(detail());await collection.seal('completed')
        first=result() | {'identity_evidence':'型号与配置全部符合，建议计入样本'}
        second=result() | {'identity_evidence':'出售品为M6',
                           'evidence_quotes':[{'source':'title','text':'Mac mini M6 16+256'}]}
        model=SimpleNamespace(analyze=AsyncMock(side_effect=[first,second]))
        report=await classify_market_batch(run.run_id,analyzer=model,saver=AsyncMock(return_value=True),notifier=AsyncMock(return_value={}))
        assert model.analyze.await_count==2
        assert report['sample_count']==1
        assert 'evidence_quotes' in model.analyze.call_args.args[2]
        row=MarketBatchRepository().candidates(run.run_id)[0]
        assert row['attempts']==2 and row['error_code'] is None
    asyncio.run(scenario())


def test_reanalyze_excluded_preserves_accepted_without_model_calls(db):
    async def scenario():
        run=MarketRun(task());collection=MarketCollection(run)
        for id in ('1','2','3'): await collection.collect(detail(id))
        await collection.seal('completed')
        initial=SimpleNamespace(analyze=AsyncMock(side_effect=[result(), result(match='unknown'), result('M4','no_match')]))
        send=AsyncMock(return_value={});save=AsyncMock(return_value=True)
        first=await classify_market_batch(run.run_id,analyzer=initial,saver=save,notifier=send)
        assert [s['item_id'] for s in first['samples']]==['1']
        async def retry(record,*_):
            assert record['商品信息']['商品ID']!='1','accepted sample must not be sent to model'
            return result() if record['商品信息']['商品ID']=='2' else result('M4','no_match')
        second=SimpleNamespace(analyze=AsyncMock(side_effect=retry))
        report=await classify_market_batch(run.run_id,analyzer=second,saver=save,notifier=send,reanalyze_excluded=True)
        assert second.analyze.await_count==2
        assert [s['item_id'] for s in report['samples']]==['1','2']
        rows=MarketBatchRepository().candidates(run.run_id)
        assert next(r for r in rows if r['item_id']=='1')['attempts']==1
        assert next(r for r in rows if r['item_id']=='2')['attempts']==2
    asyncio.run(scenario())


def test_api_accepts_excluded_scope(api_client,db,monkeypatch):
    from src.services.background_task_service import BackgroundTaskService
    queued=[]
    monkeypatch.setattr(BackgroundTaskService,'track',lambda self,coro:queued.append(coro))
    run=MarketRun(task());collection=MarketCollection(run)
    asyncio.run(collection.collect(detail()));asyncio.run(collection.seal('completed'))
    response=api_client.post(f'/api/market/reports/{run.run_id}/reanalyze',json={'scope':'excluded'})
    assert response.status_code==202
    assert queued[0].cr_frame.f_locals['reanalyze_excluded'] is True
    assert queued[0].cr_frame.f_locals['rerun_all'] is False
    queued.pop().close()
    assert api_client.post(f'/api/market/reports/{run.run_id}/reanalyze',json={'scope':'invalid'}).status_code==422
