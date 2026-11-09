"""Offline replay of the production search loop and real dispatcher/cache."""
import asyncio
import copy
import importlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

POLICY = json.loads((Path(__file__).parents[2] / 'examples/mac-mini-prefilter.patch.json').read_text())["prefilter"]
TITLES = ['Mac mini M4 16+256', 'Mac mini M6 16+256', 'Mac mini 自用', 'Mac mini M4/M6 可选']


def item(i, title):
    return {'商品ID': str(i), '商品标题': title, '商品链接': f'https://www.goofish.com/item?id={i}', '当前售价': '¥2000'}


@pytest.fixture
def replay(monkeypatch, tmp_path):
    import src.scraper as scraper
    # Other tests reload scraper for environment settings; set every side effect.
    scraper = importlib.import_module('src.scraper')
    calls = defaultdict(list)
    db = str(tmp_path / 'replay.db')
    monkeypatch.setenv('APP_DATABASE_FILE', db)
    from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
    bootstrap_sqlite_storage(db, legacy_config_file=None, legacy_result_dir='/nonexistent', legacy_price_history_dir='/nonexistent')
    real_snapshots = scraper.record_market_snapshots
    real_save = scraper.save_to_jsonl
    real_notify_failure = scraper._notify_task_failure
    state = tmp_path / 'state.json'
    state.write_text('{}')
    monkeypatch.setattr(scraper, 'STATE_FILE', str(state))
    monkeypatch.setattr(scraper, 'load_state_files', lambda *_: [])
    monkeypatch.setattr(scraper, 'load_price_snapshots', lambda *_: [])
    monkeypatch.setattr(scraper, 'load_processed_link_keys', lambda *_: set())
    monkeypatch.setattr(scraper, 'cleanup_task_images', lambda *_: None)
    monkeypatch.setattr(scraper, 'SKIP_AI_ANALYSIS', False)
    monkeypatch.setattr(scraper, 'FAILURE_GUARD', SimpleNamespace(
        should_skip_start=lambda *a, **kw: SimpleNamespace(skip=False),
        record_success=lambda *_: None))
    monkeypatch.setattr(scraper, '_notify_task_failure', AsyncMock(side_effect=AssertionError('unexpected scrape failure')))
    monkeypatch.setattr(scraper, 'random_sleep', AsyncMock())
    real_sleep = asyncio.sleep
    monkeypatch.setattr(scraper.asyncio, 'sleep', AsyncMock())
    monkeypatch.setattr('builtins.input', lambda *_: '')
    monkeypatch.setattr(scraper, 'prepare_chromium_launch_args', lambda: [])
    monkeypatch.setattr(scraper, 'build_market_reference', lambda **kw: {})

    pages = []
    current = {'page': 0, 'shared_seller': False, 'validation': False, 'remote': False, 'seller_validation': False}
    cache_get = scraper.SellerProfileCache.get_or_load
    async def counted_get(cache, seller_id, loader):
        calls['seller_load'].append(seller_id)
        return await cache_get(cache, seller_id, loader)
    monkeypatch.setattr(scraper.SellerProfileCache, 'get_or_load', counted_get)

    class Response:
        ok = True
        async def json(self):
            return copy.deepcopy(pages[current['page']])

    class Expect:
        def __init__(self, page):
            self.page = page
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        @property
        def value(self):
            async def get():
                if self.page.search:
                    return Response()
                i = self.page.item_id
                response = SimpleNamespace(ok=True)
                if current['validation']:
                    response.json = AsyncMock(return_value={'ret': ['FAIL_SYS_USER_VALIDATE:verification required']})
                    return response
                response.json = AsyncMock(return_value={'data': {'sellerDO': {'sellerId': 'seller-shared' if current['shared_seller'] else 'seller' + i}, 'itemDO': {'imageInfos': [{'url': 'image-' + i}]}}})
                return response
            return get()

    class Page:
        def __init__(self, search):
            self.search = search
            self.url = ''
            self.closed = False
        async def goto(self, url, **kwargs):
            self.url = url
            if not self.search:
                self.item_id = url.split('id=')[1]
                calls['detail'].append(self.item_id)
        def expect_response(self, *a, **kw):
            return Expect(self)
        async def evaluate(self, *_):
            pass
        async def wait_for_selector(self, *a, **kw):
            pass
        def locator(self, *_):
            return self
        async def wait_for(self, *a, **kw):
            raise scraper.PlaywrightTimeoutError('no verification overlay')
        async def click(self, *a, **kw):
            pass
        def is_closed(self):
            return self.closed
        async def close(self):
            self.closed = True
            if not self.search:
                calls['closed'].append(self.item_id)

    class Context:
        def __init__(self):
            self.first = True
            self.handlers = {}
            self.user_page = Page(True)
            self._pages = [self.user_page] if current['remote'] else []
        @property
        def pages(self):
            return [p for p in self._pages if not p.closed]
        def on(self, event, callback):
            self.handlers[event] = callback
        def remove_listener(self, event, callback):
            self.handlers.pop(event, None)
        async def new_page(self):
            page = Page(self.first)
            if not self.first:
                calls['created'].append(1)
            self.first = False
            self._pages.append(page)
            return page
        async def add_init_script(self, *_):
            calls['scripts'].append(1)

    class Browser:
        async def new_context(self, **kwargs):
            calls['new_context'].append(kwargs)
            return Context()
        async def close(self):
            calls['browser_closed'].append(1)

    class Playwright:
        async def __aenter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=Browser())))
        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(scraper, 'async_playwright', Playwright)
    monkeypatch.setattr(scraper, '_parse_search_results_json', AsyncMock(side_effect=lambda data, *_: data))

    async def advance(**kw):
        current['page'] += 1
        calls['pagination'].append(current['page'])
        return SimpleNamespace(advanced=True, response=Response())
    monkeypatch.setattr(scraper, 'advance_search_page', advance)
    def snapshots(**kw):
        calls['snapshots'].append(copy.deepcopy(kw['items']))
        return real_snapshots(**kw)
    monkeypatch.setattr(scraper, 'record_market_snapshots', snapshots)
    async def seller(context, seller_id):
        calls['seller'].append(seller_id)
        if current['seller_validation']:
            await real_sleep(.05)
            callback = context.handlers.get('response')
            if callback:
                await callback(SimpleNamespace(url='https://h5api.m.goofish.com/h5/seller', json=AsyncMock(return_value={'ret': ['FAIL_SYS_USER_VALIDATE']})))
        return {'卖家昵称': seller_id}
    async def images(item_id, urls, task_name):
        calls['images'].append(item_id)
        return ['/nonexistent/offline-fixture.jpg']
    async def ai(record, paths, prompt):
        assert prompt == 'original prompt with unchanged criteria'
        assert record['卖家信息']['卖家昵称'].startswith('seller')
        calls['ai'].append(record['商品信息']['商品ID'])
        return {'is_recommended': True, 'reason': 'offline'}
    async def save(record, keyword):
        calls['saved'].append(record['商品信息']['商品ID'])
        return await real_save(record, keyword)
    async def notify(data, reason):
        calls['notified'].append(data['商品ID'])
    for name, fn in [('scrape_user_profile', seller), ('download_all_images', images), ('get_ai_analysis', ai), ('save_to_jsonl', save), ('send_ntfy_notification', notify)]:
        monkeypatch.setattr(scraper, name, fn)

    def run(mode, data=None, debug_limit=0, source=None, shared_seller=False, decision_mode='ai', validation=False, remote=False, seller_validation=False, monitor_mode='buying'):
        pages[:] = copy.deepcopy(data or [[item(i, title) for i, title in enumerate(TITLES)]])
        current['page'] = 0
        current['shared_seller'] = shared_seller
        current['validation'] = validation
        current['remote'] = remote
        current['seller_validation'] = seller_validation
        if remote:
            from src.services.browser_session_service import BrowserSessionControl
            monkeypatch.setenv('BROWSER_MODE', 'remote')
            control = BrowserSessionControl()
            session = control.read()
            if session['reason'] == 'initial_login':
                control.acknowledge(session['incident_id'])
            context = Context()
            calls['persistent_context'].append(context)
            async def connect(_):
                calls['cdp'].append(1)
                return Browser(), context
            monkeypatch.setattr(scraper, 'connect_remote_browser', connect)
        else:
            monkeypatch.setenv('BROWSER_MODE', 'local')
        config = dict(monitor_mode=monitor_mode, keyword='offline', task_name='Mac mini M6 16GB 256GB' if monitor_mode == 'market' else 'offline', max_pages=len(pages), account_strategy='fixed', account_state_file=str(state), personal_only=False, free_shipping=False, ai_prompt_text='original prompt with unchanged criteria', decision_mode=decision_mode, keyword_rules=['mac'], analyze_images=True, prefilter=POLICY | {'mode': mode})
        if source:
            import spider_v2
            from src.domain.models.task import Task
            from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository
            config.update(enabled=True, description='offline', ai_prompt_base_file='', ai_prompt_criteria_file='')
            if source == 'json':
                path = tmp_path / 'cli.json'
                path.write_text(json.dumps([config]))
                argv = ['spider_v2.py', '--config', str(path)]
            else:
                asyncio.run(SqliteTaskRepository(db, None).save(Task(**config)))
                argv = ['spider_v2.py']
            monkeypatch.setattr(spider_v2, 'STATE_FILE', str(state))
            monkeypatch.setattr(spider_v2, 'scrape_xianyu', scraper.scrape_xianyu)
            monkeypatch.setattr(sys, 'argv', argv)
            # SQLite does not persist generated prompt text: the real CLI composes it.
            if source == 'sqlite':
                base = tmp_path / 'base.txt'
                criteria = tmp_path / 'criteria.txt'
                base.write_text('original prompt with {{CRITERIA_SECTION}}')
                criteria.write_text('unchanged criteria')
                task = asyncio.run(SqliteTaskRepository(db, None).find_by_id(0))
                task.ai_prompt_base_file = str(base)
                task.ai_prompt_criteria_file = str(criteria)
                asyncio.run(SqliteTaskRepository(db, None).save(task))
            asyncio.run(spider_v2.main())
            return len(calls['detail'])
        return asyncio.run(scraper.scrape_xianyu(config, debug_limit))
    run.notify_failure = real_notify_failure
    return run, calls


@pytest.mark.parametrize('mode,expected', [('off', ['0','1','2','3']), ('shadow', ['0','1','2','3']), ('enforce', ['1','2','3'])])
def test_real_loop_and_dispatcher(replay, mode, expected, capsys):
    run, calls = replay
    assert run(mode) == len(expected)
    assert calls['detail'] == expected
    assert len(calls['created']) == len(expected)
    for key in ('images', 'ai', 'saved', 'notified'):
        assert sorted(calls[key]) == expected
    assert sorted(calls['seller']) == ['seller' + i for i in expected]
    assert len(calls['snapshots'][0]) == 4
    from src.infrastructure.persistence.sqlite_connection import sqlite_connection
    with sqlite_connection() as conn:
        assert conn.execute('SELECT count(*) FROM price_snapshots').fetchone()[0] == 4
        assert sorted(r[0] for r in conn.execute('SELECT item_id FROM result_items')) == expected
    output = capsys.readouterr().out
    if mode != 'off':
        summary = json.loads(output.split('prefilter.summary ')[1].splitlines()[0])
        assert summary['evaluated'] == 4
        assert summary['skipped'] == (1 if mode == 'enforce' else 0)
        assert summary['would_reject'] == (1 if mode == 'shadow' else 0)
        assert summary['status'] == 'completed'


def test_all_rejected_page_then_corrected_link_and_debug_limit(replay):
    run, calls = replay
    assert run('enforce', [[item(0, 'Mac mini M4')], [item(0, 'Mac mini M6'), item(1, 'Mac mini M6')]], debug_limit=1) == 1
    assert calls['detail'] == ['0']
    assert len(calls['snapshots']) == 2
    assert calls['pagination'] == [1]


def test_reject_does_not_persist_dedup(replay):
    run, calls = replay
    assert run('enforce', [[item(0, 'Mac mini M4')]]) == 0
    assert not calls['detail']
    assert run('off', [[item(0, 'Mac mini M4')]]) == 1
    assert calls['detail'] == ['0']


def test_evaluator_exception_keeps_real_downstream(replay, monkeypatch):
    def fail(*args):
        raise ValueError('test')
    monkeypatch.setattr('src.services.listing_prefilter.evaluate_title', fail)
    run, calls = replay
    assert run('enforce', [[item(0, 'Mac mini M4')]]) == 1
    assert calls['ai'] == ['0']


def test_cancelled_run_has_one_partial_summary(replay, monkeypatch, capsys):
    monkeypatch.setattr('src.services.listing_prefilter.evaluate_title', lambda *a: (_ for _ in ()).throw(asyncio.CancelledError()))
    run, calls = replay
    with pytest.raises(asyncio.CancelledError):
        run('enforce')
    output = capsys.readouterr().out
    assert output.count('prefilter.summary ') == 1
    assert '"status": "cancelled"' in output
    assert not calls['detail']


@pytest.mark.parametrize('source', ['json', 'sqlite'])
def test_cli_sources_reach_actual_scraper(replay, source):
    run, calls = replay
    assert run('enforce', source=source) == 3
    assert calls['detail'] == ['1', '2', '3']
    assert calls['ai'] == []  # retired template paths are not read by CLI


def test_repeated_reject_then_corrected_title(replay, capsys):
    run, calls = replay
    assert run('enforce', [[item(0, 'Mac mini M4')], [item(0, 'Mac mini M4')], [item(0, 'Mac mini M6')]]) == 1
    assert calls['detail'] == ['0']
    summary = json.loads(capsys.readouterr().out.split('prefilter.summary ')[1].splitlines()[0])
    assert summary['skipped'] == 2
    assert summary['evaluated'] == 3


def test_history_dedup_before_prefilter(replay, monkeypatch, capsys):
    monkeypatch.setattr('src.scraper.load_processed_link_keys', lambda *_: {'https://www.goofish.com/item?id=0'})
    run, calls = replay
    assert run('enforce') == 3
    summary = json.loads(capsys.readouterr().out.split('prefilter.summary ')[1].splitlines()[0])
    assert summary['evaluated'] == 3
    assert summary['skipped'] == 0
    assert len(calls['snapshots'][0]) == 4


@pytest.mark.parametrize('mode,loads', [('off', 4), ('shadow', 4), ('enforce', 3)])
def test_shared_seller_cache_is_measured_separately(replay, mode, loads):
    run, calls = replay
    assert run(mode, shared_seller=True) == loads
    assert len(calls['seller_load']) == loads
    assert calls['seller'] == ['seller-shared']


def test_invalid_configuration_fails_open_in_real_loop(replay, monkeypatch):
    import src.services.listing_prefilter as module
    def fail(*args):
        raise ValueError('synthetic compile failure')
    monkeypatch.setattr(module, 'compile_prefilter', fail)
    run, calls = replay
    assert run('enforce') == 4
    assert len(calls['ai']) == 4


@pytest.mark.parametrize('mode,expected', [('off', ['0', '1', '2', '3']), ('shadow', ['0', '1', '2', '3']), ('enforce', ['1', '2', '3'])])
def test_keyword_decision_mode_remains_independent(replay, mode, expected):
    run, calls = replay
    assert run(mode, decision_mode='keyword') == len(expected)
    assert calls['detail'] == expected
    assert sorted(calls['saved']) == expected
    assert sorted(calls['notified']) == expected
    assert not calls['ai']
    assert not calls['images']


def test_original_scrape_errors_and_retries_have_one_failed_summary(replay, monkeypatch, capsys):
    import src.scraper as scraper
    monkeypatch.setattr(scraper, '_parse_search_results_json', AsyncMock(side_effect=scraper.PlaywrightTimeoutError('offline timeout')))
    settings = scraper._get_rotation_settings({})
    monkeypatch.setattr(scraper, '_get_rotation_settings', lambda _: settings | {'account_retry_limit': 2, 'proxy_retry_limit': 2})
    failure_notification = AsyncMock()
    monkeypatch.setattr(scraper, '_notify_task_failure', failure_notification)
    run, calls = replay
    assert run('enforce') == 0
    output = capsys.readouterr().out
    assert output.count('prefilter.summary ') == 1
    summary = json.loads(output.split('prefilter.summary ')[1].splitlines()[0])
    assert summary['status'] == 'failed'
    assert summary['attempts'] == 2
    assert summary['evaluated'] == 0
    failure_notification.assert_awaited_once()
    assert not calls['detail']


def test_detail_validation_stops_attempt_and_pauses_next_run(replay, monkeypatch, tmp_path, capsys):
    import src.scraper as scraper
    from src.failure_guard import FailureGuard

    guard = FailureGuard(str(tmp_path / 'guard.json'), threshold=3, pause_seconds=86400)
    monkeypatch.setattr(scraper, 'FAILURE_GUARD', guard)
    # Use the real failure recorder, replacing only its notification transport.
    run, calls = replay
    monkeypatch.setattr(scraper, '_notify_task_failure', run.notify_failure)
    notifier = AsyncMock()
    monkeypatch.setattr(scraper, 'send_ntfy_notification', notifier)
    data = [[item(0, 'Mac mini M6'), item(1, 'Mac mini M6')], [item(2, 'Mac mini M6')]]
    assert run('shadow', data=data, validation=True) == 0
    assert calls['detail'] == ['0']
    assert calls['closed'] == ['0']
    assert not calls['pagination']
    for key in ('seller', 'images', 'ai', 'saved', 'notified'):
        assert not calls[key]
    summary = json.loads(capsys.readouterr().out.split('prefilter.summary ')[1].splitlines()[0])
    assert summary['status'] == 'failed'
    assert summary['attempts'] == 1
    assert guard.should_skip_start('offline').skip
    notifier.assert_awaited_once()
    assert run('shadow', data=data, validation=True) == 0
    assert calls['detail'] == ['0']
    assert '"status": "paused"' in capsys.readouterr().out


def test_remote_session_reuses_context_without_storage_import_or_closing_browser(replay):
    run, calls = replay
    assert run('enforce', remote=True) == 3
    assert len(calls['cdp']) == 1
    assert not calls['new_context']
    assert not calls['scripts']
    assert not calls['browser_closed']
    context = calls['persistent_context'][0]
    assert context.pages == [context.user_page]
    assert calls['detail'] == ['1', '2', '3']


def test_remote_verification_keeps_challenge_page_and_blocks_next_run(replay, monkeypatch):
    import src.scraper as scraper
    from src.services.browser_session_service import BrowserSessionControl
    run, calls = replay
    monkeypatch.setattr(scraper, '_notify_task_failure', run.notify_failure)
    notifier = AsyncMock()
    monkeypatch.setattr(scraper, 'notify_browser_handoff', notifier)
    assert run('shadow', remote=True, validation=True) == 0
    assert calls['detail'] == ['0']
    assert not calls['closed']
    assert not calls['browser_closed']
    assert len(calls['persistent_context'][0].pages) == 3
    assert BrowserSessionControl().read()['state'] == 'verification_required'
    notifier.assert_awaited_once()
    assert run('shadow', remote=True, validation=True) == 0
    assert len(calls['cdp']) == 1
    assert not calls['ai']


def test_seller_validation_is_watched_until_dispatcher_finishes(replay, monkeypatch):
    import src.scraper as scraper
    from src.services.browser_session_service import BrowserSessionControl
    notifier = AsyncMock()
    monkeypatch.setattr(scraper, 'notify_browser_handoff', notifier)
    run, calls = replay
    assert run('shadow', remote=True, seller_validation=True) == 0
    assert BrowserSessionControl().read()['state'] == 'verification_required'
    notifier.assert_awaited_once()
    assert not calls['ai']
    assert not calls['saved']
    assert not calls['browser_closed']


def test_market_only_uses_details_and_resamples_seen_prices(replay, monkeypatch):
    from src.services.market_analysis_service import MarketAnalyzer
    from src.services.notification_service import NotificationService
    from src.infrastructure.persistence.market_report_repository import MarketReportRepository
    run, calls = replay
    async def classify(self, record, images, prompt):
        assert '购买推荐' in prompt and '{{TARGET}}' not in prompt
        assert len(calls['closed']) == len(calls['detail'])  # all browser collection finished
        assert len(calls['detail']) in (3,6,7)
        assert record['卖家信息']['信息来源'] == '商品详情页'
        title = record['商品信息']['商品标题']
        return dict(match_status='match' if 'M6 16+256' in title else 'unknown',
                    price_kind='whole_unit', condition='light_wear', functional_status='normal', model='M6 16+256',
                    product_family='mac mini', chip='M6', memory_gb=16, storage_gb=256,
                    identity_evidence='Mac mini M6 16+256', evidence_source='text', reason='详情配置对应整机报价')
    monkeypatch.setattr(MarketAnalyzer, 'analyze', classify)
    notify = AsyncMock(return_value={'wecom':{'success':True}})
    monkeypatch.setattr(NotificationService, 'send_notification', notify)
    import src.scraper as scraper
    monkeypatch.setattr(scraper, 'load_processed_link_keys', lambda *_: {'1','2','3'})
    assert run('off', monitor_mode='market') == 3
    assert calls['seller'] == [] and calls['seller_load'] == []
    assert calls['notified'] == []  # no individual recommendations
    assert len(calls['detail']) == 3
    assert notify.await_count == 1
    report = MarketReportRepository().reports()[0]
    assert report['sample_count'] == 1 and report['summary']['median'] == 2000
    run('off', monitor_mode='market')
    assert len(calls['detail']) == 6 and notify.await_count == 1
    changed = [[item(1, 'Mac mini M6 16+256') | {'当前售价':'¥2500'}]]
    run('off', data=changed, monitor_mode='market')
    assert notify.await_count == 2
    assert MarketReportRepository().reports()[0]['changes']['repriced'] == 1


def test_market_verification_keeps_partial_report_and_never_sends_market_alert(replay, monkeypatch):
    from src.services.notification_service import NotificationService
    from src.infrastructure.persistence.market_report_repository import MarketReportRepository
    notify = AsyncMock()
    monkeypatch.setattr(NotificationService, 'send_notification', notify)
    run, calls = replay
    import src.scraper as scraper
    monkeypatch.setattr(scraper, '_notify_task_failure', AsyncMock())
    run('off', monitor_mode='market', remote=True, validation=True)
    report = MarketReportRepository().reports()[0]
    assert report['status'] == 'partial'
    assert not any(call.args[0].get('market_report') for call in notify.await_args_list)
