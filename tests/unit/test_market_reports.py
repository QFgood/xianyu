import asyncio
from unittest.mock import AsyncMock

from src.services.market_report_service import MarketRun, format_report
from src.infrastructure.persistence.market_report_repository import MarketReportRepository
from src.services.market_analysis_service import MarketClassification


def task(**updates):
    return dict(id=0, task_name='Mac mini M6 16GB 256GB', keyword='mac mini',
                description='M6 16GB 256GB内置SSD', max_pages=3, **updates)


def record(item_id='1', price='3000', match='match', kind='whole_unit', condition='light_wear', **extra):
    return {'商品信息': {'商品ID': item_id, '当前售价': price, '商品标题': 'Mac mini M6 16+256',
                        '商品链接': 'https://www.goofish.com/item?id='+item_id},
            '卖家信息': {'卖家芝麻信用': '优秀'},
            'market_analysis': dict(match_status=match, price_kind=kind, condition=condition,
                                    functional_status='normal', model='M6 16+256', reason='正文配置对应整机价格', product_family='mac mini', chip='M6', memory_gb=16, storage_gb=256, identity_evidence='Mac mini M6 16+256', evidence_source='text', **extra)}


def test_effective_samples_price_changes_and_notification_retry(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DATABASE_FILE', str(tmp_path/'market.db'))
    sent = AsyncMock(return_value={'wecom': {'success': True}})
    async def run(records, status='completed', config=None, notifier=sent):
        market = MarketRun(config or task())
        for r in records:
            await market.observe(r)
        return await market.finish(status, notifier)
    async def scenario():
        initial = await run([record(), record(), record('2','4000'), record('3','2000',match='no_match'),
                             record('4','100',kind='deposit'), record('5','100',kind='accessory'),
                             record('6','100',kind='option_min'), record('7','999', match='unknown'),
                             record('8','500',condition='faulty')])
        assert initial['sample_count'] == 2
        assert initial['summary']['median'] == 3500  # faulty unit has its own group
        assert 'faulty' not in initial['groups']
        assert len(initial['excluded']) == 6
        assert sent.await_count == 1
        text = format_report(initial)
        assert '不是成交价' in text and '轻微痕迹' in text
        assert '新推荐' not in text and '购买' not in text
        await run([record('8','500',condition='faulty'), record('2','4000'), record()])
        assert sent.await_count == 1  # order/reason/unknown observations don't matter
        await run([record(price='3100'), record('2','4000'), record('8','500',condition='faulty')])
        assert sent.await_count == 2
        # failed / interrupted scans never advance the baseline or send disappearance alerts
        partial = await run([record('2','4000')], status='waiting_for_human')
        assert partial['status'] == 'partial' and sent.await_count == 2
        failed = await run([record(error='AIUnavailable')])
        assert failed['status'] == 'partial' and sent.await_count == 2
        failure = AsyncMock(return_value={'wecom': {'success': False}})
        await run([record(price='3200')], notifier=failure)
        await run([record(price='3200')])
        assert sent.await_count == 3  # failed send retried next scan
        zero = await run([])
        assert zero['changes']['missing'] == 1 and sent.await_count == 4
        await run([])
        assert sent.await_count == 4
        # New search scope is not compared against the old one.
        scope = await run([record()], config=task(region='广东'))
        assert scope['changes']['missing'] == 0
        assert len(MarketReportRepository().reports('0')) == 10
    asyncio.run(scenario())


def test_nonfinite_and_nonpositive_prices_not_counted(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DATABASE_FILE', str(tmp_path/'market.db'))
    async def scenario():
        market = MarketRun(task())
        for idx, value in enumerate(['nan', 'inf', '0', '-1', '100起', '不明']):
            await market.observe(record(str(idx), value))
        notify = AsyncMock()
        report = await market.finish('completed', notify)
        assert report['sample_count'] == 0
        notify.assert_not_called()
    asyncio.run(scenario())


def test_classifier_and_prompt_are_not_buyer_schema(monkeypatch):
    from src.infrastructure.external.ai_client import AIClient
    from src.services.market_analysis_service import build_market_prompt
    client = object.__new__(AIClient)
    message = client._build_messages({'analysis_purpose':'market'}, [], build_market_prompt(task()))[0]['content']
    assert '必须保留原有 is_recommended' not in message
    assert 'value_score' not in message
    result = MarketClassification.model_validate(record()['market_analysis'])
    assert result.match_status == 'match'


def test_chip_family_and_storage_are_independent_hard_constraints():
    from src.domain.models.market_target import MarketTarget
    target = MarketTarget.from_task(task())
    assert (target.product_family, target.chip, target.memory_gb, target.storage_gb) == ('mac mini','m6',16,256)
    assert target.reject_title('Mac mini M4 16+256 自用')
    assert target.reject_title('MacminiM4 16+256 自用')
    assert target.reject_title('Mac mini 2024款，M3芯片，16G内存+256G固态')
    assert target.reject_title('Mac mini M6 Pro 16+256')
    assert not target.reject_title('Mac mini M6 16+256')
    assert not target.reject_title('Mac mini M4/M6 可选')
    assert not target.reject_title('Mac mini 自用')
    assert not target.reject_title('Mac mini M4，对比M6')
    assert not target.reject_title('M64 Mac mini 16+256...')
    base = record()['market_analysis']
    item = record()['商品信息']
    assert target.validate_identity(base, item)['match_status'] == 'match'
    compact = base | {'identity_evidence':'MacminiM6 16+256'}
    assert target.validate_identity(compact, item | {'商品标题':'MacminiM6 16+256'})['match_status'] == 'match'
    for change in ({'chip':'M4'}, {'chip':'M3'}, {'chip':'M2'}, {'chip':'M6 Pro'},
                   {'product_family':'macbook air'}, {'storage_gb':512}, {'memory_gb':24}):
        assert target.validate_identity(base | change, item)['match_status'] == 'no_match'
    assert target.validate_identity(base | {'chip':None}, item)['match_status'] == 'unknown'
    # Even an erroneous model PASS cannot override the sole chip in the listing.
    assert target.validate_identity(base | {'identity_evidence':'Mac mini M4 16+256'}, item | {'商品标题':'Mac mini M4 16+256'})['match_status'] != 'match'
    source = {'商品标题':'个人一手自用银色M4 MacBook Air 16+256，因换M6 Mac mini故出'}
    for quote in ('因换M6 Mac mini故出', 'M6 Mac mini'):
        mistaken = base | {'identity_evidence':quote}
        assert target.validate_identity(mistaken, source)['match_status'] == 'unknown'
    assert target.validate_identity(base | {'identity_evidence':'并不存在的M6截图'}, item)['match_status'] == 'unknown'


def test_target_normalizes_chip_suffix_and_terabyte():
    from src.domain.models.market_target import MarketTarget
    target = MarketTarget.from_task({'task_name':'Ｍａｃ mini Ｍ６ Pro 32+1T'})
    assert target.chip == 'm6 pro' and target.storage_gb == 1024 and target.memory_gb == 32


def test_wecom_market_payload_is_compact_and_uses_short_links(tmp_path, monkeypatch):
    from src.infrastructure.external.notification_clients.wecom_bot_client import WeComBotClient
    from unittest.mock import Mock
    monkeypatch.setenv('APP_DATABASE_FILE', str(tmp_path/'market.db'))
    response = Mock()
    response.json.return_value = {'errcode':0}
    post = Mock(return_value=response)
    monkeypatch.setattr('src.infrastructure.external.notification_clients.wecom_bot_client.requests.post', post)
    async def scenario():
        run = MarketRun(task())
        await run.observe(record())
        report = await run.finish('completed', AsyncMock(return_value={}))
        await WeComBotClient('https://example.invalid/test-only').send({'market_report':report},format_report(report))
        content = post.call_args.kwargs['json']['markdown']['content']
        assert '行情更新' in content and '样本 ¥3000' in content
        assert '新推荐' not in content and '手机端链接' not in content
        assert 'sharexy' not in content and '不是成交价' in content
        assert len(content) < 1000
    asyncio.run(scenario())


def test_only_three_good_condition_grades_enter_market(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_DATABASE_FILE', str(tmp_path/'market.db'))
    async def scenario():
        market = MarketRun(task())
        for index, condition in enumerate(['new','like_new','light_wear','worn','faulty','unknown']):
            await market.observe(record(str(index),condition=condition))
        functional_fault = record('7', condition='like_new')
        functional_fault['market_analysis']['functional_status'] = 'faulty'
        await market.observe(functional_fault)
        unavailable = record('8', condition='like_new')
        unavailable['market_analysis']['functional_status'] = 'unknown'
        await market.observe(unavailable)
        report = await market.finish('completed',AsyncMock(return_value={}))
        assert report['sample_count'] == 3
        assert set(report['groups']) == {'new','like_new','light_wear'}
        assert all(group['count'] == 1 for group in report['groups'].values())
        assert len(report['excluded']) == 5
        message = format_report(report)
        assert '故障：' not in message and '几乎全新：1 件' in message
    asyncio.run(scenario())


def test_market_analyzer_does_not_treat_task_labels_as_observed_evidence():
    from src.services.market_analysis_service import MarketAnalyzer
    from types import SimpleNamespace
    async def scenario():
        analyze = AsyncMock(return_value=record()['market_analysis'])
        analyzer = MarketAnalyzer()
        analyzer.client = SimpleNamespace(analyze=analyze)
        observed = record() | {'任务名称':'别名M4', '搜索关键字':'其他芯片'}
        result = await analyzer.analyze(observed, [], '明确的结构化目标')
        assert result['match_status'] == 'match'
        sent = analyze.call_args.args[0]
        assert set(sent) == {'商品信息','卖家信息','analysis_purpose'}
        assert '任务名称' not in sent and sent['商品信息'] == observed['商品信息']
    asyncio.run(scenario())
