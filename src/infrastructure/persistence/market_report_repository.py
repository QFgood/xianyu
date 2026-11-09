"""每轮行情报告保留独立快照，历史结果去重不覆盖价格轨迹。"""
import json
from src.infrastructure.persistence.sqlite_connection import sqlite_connection, init_schema


class MarketReportRepository:
    def save(self, report):
        with sqlite_connection() as conn:
            init_schema(conn)
            conn.execute('INSERT INTO market_reports(run_id, task_key, cohort, created_at, payload) VALUES(?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET payload=excluded.payload',
                         (report['run_id'], report['task_key'], report['cohort'], report['created_at'], json.dumps(report, ensure_ascii=False)))
            conn.commit()

    def get(self, run_id):
        with sqlite_connection() as conn:
            init_schema(conn)
            row=conn.execute('SELECT payload FROM market_reports WHERE run_id=?',(run_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def reports(self, task_key=None, limit=100):
        with sqlite_connection() as conn:
            init_schema(conn)
            where = ' WHERE task_key=?' if task_key is not None else ''
            params = [str(task_key)] if task_key is not None else []
            rows = conn.execute('SELECT payload FROM market_reports' + where + ' ORDER BY created_at DESC, rowid DESC LIMIT ?', (*params, limit)).fetchall()
            return [json.loads(row[0]) for row in rows]

    def delivered(self, cohort):
        with sqlite_connection() as conn:
            init_schema(conn)
            row = conn.execute('SELECT value FROM app_metadata WHERE key=?', ('market:delivered:' + cohort,)).fetchone()
            return json.loads(row[0]) if row else None

    def acknowledge(self, cohort, samples):
        with sqlite_connection() as conn:
            init_schema(conn)
            conn.execute('INSERT OR REPLACE INTO app_metadata(key,value) VALUES(?,?)',
                         ('market:delivered:' + cohort, json.dumps(samples, sort_keys=True)))
            conn.commit()
