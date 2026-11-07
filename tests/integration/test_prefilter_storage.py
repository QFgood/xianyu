import asyncio
import json
import multiprocessing
import sqlite3
from pathlib import Path

import pytest

from src.domain.models.prefilter import ListingPrefilterConfig
from src.domain.models.task import Task, TaskUpdate
from src.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from src.infrastructure.persistence.sqlite_connection import SCHEMA_STATEMENTS, sqlite_connection
from src.infrastructure.persistence.sqlite_task_repository import SqliteTaskRepository, find_task_by_name_sync
from src.infrastructure.persistence.json_task_repository import JsonTaskRepository

POLICY = json.loads((Path(__file__).parents[2] / 'examples/mac-mini-prefilter.patch.json').read_text())["prefilter"]


def bootstrap_worker(db):
    bootstrap_sqlite_storage(db, legacy_config_file=None, legacy_result_dir='/nonexistent', legacy_price_history_dir='/nonexistent')


@pytest.mark.parametrize('attempt', range(3))
def test_old_schema_concurrent_migration_and_retained_data(tmp_path, attempt):
    db = str(tmp_path / 'old.db')
    with sqlite3.connect(db) as conn:
        for sql in SCHEMA_STATEMENTS:
            conn.execute(sql.replace(',\n        prefilter_json TEXT', ''))
        conn.execute("INSERT INTO tasks (id, task_name, enabled, keyword, analyze_images, max_pages, personal_only, ai_prompt_base_file, ai_prompt_criteria_file, account_strategy, free_shipping, decision_mode, keyword_rules_json, is_running) VALUES (0, 'existing', 1, 'keyword', 1, 1, 1, 'base', 'criteria', 'auto', 1, 'ai', '[]', 0)")
        conn.execute("INSERT INTO result_items (result_filename, keyword, task_name, crawl_time, link_unique_key, is_recommended, keyword_hit_count, raw_json) VALUES ('kept', 'keyword', 'existing', 'now', 'kept', 0, 0, '{}')")
        conn.execute("INSERT INTO price_snapshots (keyword_slug, keyword, task_name, snapshot_time, snapshot_day, run_id, item_id, price, tags_json) VALUES ('keyword', 'keyword', 'existing', 'now', 'today', 'run', 'kept', 123, '[]')")
        conn.execute("INSERT INTO app_metadata VALUES ('retained', 'yes')")
    ctx = multiprocessing.get_context('spawn')
    processes = [ctx.Process(target=bootstrap_worker, args=(db,)) for _ in range(4)]
    for p in processes:
        p.start()
    for p in processes:
        p.join(20)
        assert p.exitcode == 0
    bootstrap_worker(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute('SELECT task_name, prefilter_json FROM tasks').fetchone() == ('existing', None)
        assert conn.execute('SELECT result_filename FROM result_items').fetchone()[0] == 'kept'
        assert conn.execute('SELECT price FROM price_snapshots').fetchone()[0] == 123
        assert 'prefilter_json' in [r[1] for r in conn.execute('PRAGMA table_info(tasks)')]
        assert conn.execute("SELECT value FROM app_metadata WHERE key='retained'").fetchone()[0] == 'yes'


def test_repository_roundtrip_update_corruption_and_import(tmp_path, sample_task_payload, monkeypatch):
    db = str(tmp_path / 'tasks.db')
    monkeypatch.setenv('APP_DATABASE_FILE', db)
    legacy = tmp_path / 'config.json'
    legacy.write_text(json.dumps([sample_task_payload | {'prefilter': POLICY}, sample_task_payload | {'task_name': 'other', 'prefilter': {'unknown': 1}}]))
    repo = SqliteTaskRepository(db, str(legacy))
    tasks = asyncio.run(repo.find_all())
    assert tasks[0].prefilter.mode == 'shadow'
    assert tasks[1].prefilter is None
    updated = tasks[0].apply_update(TaskUpdate(prefilter=POLICY | {'mode': 'enforce'}))
    assert isinstance(updated.prefilter, ListingPrefilterConfig)
    assert updated.model_dump()['prefilter']['mode'] == 'enforce'
    asyncio.run(repo.save(updated))
    assert find_task_by_name_sync(updated.task_name).prefilter.mode == 'enforce'
    updated = updated.apply_update(TaskUpdate(enabled=False))
    asyncio.run(repo.save(updated))
    assert asyncio.run(repo.find_by_id(0)).prefilter.mode == 'enforce'
    with sqlite_connection(db) as conn:
        conn.execute("UPDATE tasks SET prefilter_json = '{broken' WHERE id=0")
        conn.commit()
    assert all(t.prefilter is None for t in asyncio.run(repo.find_all()))
    # Bootstrap remains one-time: changed legacy settings do not re-import.
    legacy.write_text('[]')
    assert len(asyncio.run(repo.find_all())) == 2
    for raw in ('{"mode":"invalid"}', '[]', '42', '9' * 5000, '[' * 2000 + ']' * 2000):
        with sqlite_connection(db) as conn:
            conn.execute('UPDATE tasks SET prefilter_json=? WHERE id=0', (raw,))
            conn.commit()
        assert asyncio.run(repo.find_by_id(0)).prefilter is None

    # Only optional policy corruption is suppressed, never other task errors.
    with sqlite_connection(db) as conn:
        conn.execute("UPDATE tasks SET keyword_rules_json='not-json' WHERE id=0")
        conn.commit()
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(repo.find_by_id(0))


def test_json_roundtrip_and_clear(tmp_path, sample_task_payload):
    repo = JsonTaskRepository(str(tmp_path / 'tasks.json'))
    task = Task(**sample_task_payload, prefilter=POLICY)
    saved = asyncio.run(repo.save(task))
    assert asyncio.run(repo.find_by_id(saved.id)).prefilter.mode == 'shadow'
    cleared = saved.apply_update(TaskUpdate(prefilter=None))
    asyncio.run(repo.save(cleared))
    assert asyncio.run(repo.find_by_id(saved.id)).prefilter is None
