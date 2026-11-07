import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.domain.models.prefilter import ListingPrefilterConfig
from src.services.listing_prefilter import PrefilterRun, compile_prefilter, evaluate_title

POLICY = json.loads((Path(__file__).parents[2] / 'examples/mac-mini-prefilter.patch.json').read_text())["prefilter"]


@pytest.mark.parametrize('title', [
    *[f'Mac mini M{i} 16+256' for i in range(1, 6)],
    'Mac mini M2 2023', '苹果 ＭａｃｍｉｎｉＭ５ 16＋256',
    'Mac mini M2 Pro 自用', 'Mac mini Intel i5', '自用Mac mini M4国行',
    'MAC MINI m4', 'Mac mini\tM4', 'MacminiM4', 'Mac mini-M4 Max',
])
def test_explicit_reject(title):
    c = compile_prefilter(ListingPrefilterConfig(**POLICY))
    d = evaluate_title(title, c)
    assert d.action == 'REJECT'
    assert d.reason == 'explicit_excluded_variant'
    assert evaluate_title(title, c) == d


@pytest.mark.parametrize('title', [
    '自用 Mac mini 16+256', 'Mac mini 2020', 'Mac mini i5',
    'Mac mini M6 16+256', 'MacminiM6 24+512', 'Mac mini M6 Pro',
    'Mac mini M6 16+256，赠 1TB 外置 SSD', 'Mac mini M4/M6 可选',
    'Mac mini M4/M5 可选', 'Mac mini M4/7', 'Mac mini M4 同款，实际 M6',
    'Mac mini M4 对比评测', '适用 Mac mini M4 的支架', 'Mac mini M4 换新',
    'Mac mini 16+256，另出 MacBook M4', 'Mac mini A7M4 联名款',
    'Mac mini M40', 'Mac mini AM4', 'Mac mini M4A', 'Mac mini M7',
    'Mac mini M4 M7', 'Mac mini M4 MacminiM7', '未知标题', None, {}, 123, '', ' ' * 10,
    'Mac mini M4' + 'x' * 513, 'Mac mini M4\x00', 'Mac mini M4…', 'Mac mini M4...', 'Mac mini M4 配件',
    'Mac mini 自用，MacBook M4', 'M4 Mac mini', 'Mac mini，M4',
    'Mac mini M4 vs MacBook', '不是 Mac mini M4',
    'Mac mini M4 搭配出售', 'MacminiM6 Mac mini M4',
])
def test_conservative_keep(title):
    c = compile_prefilter(ListingPrefilterConfig(**POLICY))
    assert evaluate_title(title, c).action == 'KEEP'


def test_literal_rules_and_normalization():
    c = ListingPrefilterConfig(mode='enforce', product_aliases=[' X[1] ', 'x[1]'], reject_tokens=['V.2'])
    assert c.product_aliases == ['x[1]']
    assert evaluate_title('X[1] V.2', compile_prefilter(c)).action == 'REJECT'
    assert evaluate_title('X1 Vx2', compile_prefilter(c)).action == 'KEEP'


@pytest.mark.parametrize('patch', [
    {'version': True}, {'version': 2}, {'version': '1'}, {'mode': 'on'},
    {'kind': 'regex'}, {'script': 'anything'}, {'product_aliases': []},
    {'reject_tokens': ['']}, {'keep_tokens': ['Ｍ４']},
    {'reject_tokens': ['x' * 65]}, {'ambiguity_terms': ['x'] * 65},
    {'product_aliases': 'Mac mini'}, {'reject_tokens': [1]},
])
def test_strict_config(patch):
    with pytest.raises(ValidationError):
        ListingPrefilterConfig(**(POLICY | patch))


def test_modes_and_error_fail_open(monkeypatch, capsys):
    import src.services.listing_prefilter as module
    def fail(*args):
        raise ValueError('sensitive title must not be logged')
    off = PrefilterRun(POLICY | {'mode': 'off'})
    monkeypatch.setattr(module, 'evaluate_title', fail)
    assert not off.should_skip('Mac mini M4', 1, 1)
    assert not off.counts
    run = PrefilterRun(POLICY | {'mode': 'enforce'})
    assert not run.should_skip('Mac mini M4', 1, 1)
    assert run.counts['evaluated'] == run.counts['passed'] == run.counts['fail_open'] == 1
    run.summary('cancelled')
    assert 'sensitive' not in capsys.readouterr().out
    assert not PrefilterRun({'bad': 'secret'}).enabled


def test_run_counts_and_log_limit(capsys):
    for mode in ('shadow', 'enforce'):
        run = PrefilterRun(POLICY | {'mode': mode})
        for i in range(30):
            assert run.should_skip('Mac mini M4 private title', 1, i) == (mode == 'enforce')
        assert run.counts['evaluated'] == run.counts['passed'] + run.counts['skipped'] == 30
        assert run.counts['would_reject'] == (30 if mode == 'shadow' else 0)
    output = capsys.readouterr().out
    assert output.count('prefilter.skipped ') == 20
    assert output.count('prefilter.would_reject ') == 20
    assert 'private title' not in output


def test_compiled_policy_isolated_from_other_tasks_and_source_mutation():
    raw = ListingPrefilterConfig(**POLICY)
    first = compile_prefilter(raw)
    raw.reject_tokens.clear()
    other = compile_prefilter(ListingPrefilterConfig(mode='enforce', product_aliases=['Camera'], reject_tokens=['V1']))
    assert evaluate_title('Mac mini M4', first).action == 'REJECT'
    assert evaluate_title('Mac mini M4', other).action == 'KEEP'
    assert evaluate_title('Camera V1', first).action == 'KEEP'
