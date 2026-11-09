"""Durable collection packets and independent classification checkpoints."""
import json
from datetime import datetime, timezone
from src.infrastructure.persistence.sqlite_connection import sqlite_connection, init_schema


def now():
    return datetime.now(timezone.utc).isoformat()


class MarketBatchRepository:
    def create(self, batch_id, task, prompt, target):
        with sqlite_connection() as conn:
            init_schema(conn)
            conn.execute('INSERT INTO market_batches(id,task_key,task_name,created_at,updated_at,state,task_json,prompt,target_json) VALUES(?,?,?,?,?,?,?,?,?)',
                         (batch_id,str(task['id'] if task.get('id') is not None else task['task_name']),task['task_name'],now(),now(),'collecting',json.dumps(task,ensure_ascii=False),prompt,json.dumps(target,ensure_ascii=False)))
            conn.commit()

    def add(self, batch_id, record):
        key = str(record['商品信息'].get('商品ID') or record['商品信息']['商品链接'])
        with sqlite_connection() as conn:
            init_schema(conn)
            conn.execute('INSERT OR IGNORE INTO market_candidates(batch_id,item_id,record_json) VALUES(?,?,?)',
                         (batch_id,key,json.dumps(record,ensure_ascii=False)))
            conn.commit()

    def seal(self, batch_id, status, errors, rejected):
        with sqlite_connection() as conn:
            init_schema(conn)
            conn.execute("UPDATE market_batches SET state='collected',collection_status=?,collection_errors=?,title_rejected=?,updated_at=? WHERE id=? AND state='collecting'",
                         (status,json.dumps(errors,ensure_ascii=False),rejected,now(),batch_id))
            conn.commit()

    def get(self, batch_id):
        with sqlite_connection() as conn:
            init_schema(conn)
            row = conn.execute('SELECT * FROM market_batches WHERE id=?',(batch_id,)).fetchone()
            return dict(row) if row else None

    def list(self, limit=50):
        with sqlite_connection() as conn:
            init_schema(conn)
            rows = conn.execute('''SELECT b.id,b.task_key,b.task_name,b.created_at,b.updated_at,b.state,b.collection_status,b.target_json,
                count(c.item_id) AS collected,sum(CASE WHEN c.analysis_json IS NOT NULL THEN 1 ELSE 0 END) AS classified,
                sum(CASE WHEN c.error_code IS NOT NULL THEN 1 ELSE 0 END) AS failed
                FROM market_batches b LEFT JOIN market_candidates c ON c.batch_id=b.id
                GROUP BY b.id ORDER BY b.created_at DESC LIMIT ?''',(limit,)).fetchall()
            return [dict(r, target=json.loads(r['target_json'])) for r in rows]

    def candidates(self, batch_id):
        with sqlite_connection() as conn:
            init_schema(conn)
            return [dict(r) for r in conn.execute('SELECT * FROM market_candidates WHERE batch_id=? ORDER BY rowid',(batch_id,))]

    def claim(self, batch_id):
        # No timeout takeover: a long model request must never create a second worker.
        # Restart recovery uses an OS file lock in the worker, below this durable state.
        with sqlite_connection() as conn:
            init_schema(conn)
            row = conn.execute('SELECT state FROM market_batches WHERE id=?',(batch_id,)).fetchone()
            if not row or row['state'] == 'collecting':
                return False
            conn.execute("UPDATE market_batches SET state='classifying',updated_at=? WHERE id=?",(now(),batch_id))
            conn.commit()
            return True

    def reset(self, batch_id, keep_item_ids=()):
        with sqlite_connection() as conn:
            init_schema(conn)
            keep=set(keep_item_ids)
            rows=conn.execute('SELECT item_id FROM market_candidates WHERE batch_id=?',(batch_id,)).fetchall()
            conn.executemany('UPDATE market_candidates SET analysis_json=NULL,error_code=NULL WHERE batch_id=? AND item_id=?',
                             [(batch_id,r['item_id']) for r in rows if r['item_id'] not in keep])
            conn.commit()

    def checkpoint(self, batch_id, item_id, analysis, error_code, attempts):
        with sqlite_connection() as conn:
            init_schema(conn)
            conn.execute('UPDATE market_candidates SET analysis_json=?,error_code=?,attempts=attempts+? WHERE batch_id=? AND item_id=?',
                         (json.dumps(analysis,ensure_ascii=False) if analysis else None,error_code,attempts,batch_id,item_id))
            conn.execute('UPDATE market_batches SET updated_at=? WHERE id=?',(now(),batch_id))
            conn.commit()

    def finish(self, batch_id, state):
        with sqlite_connection() as conn:
            init_schema(conn)
            conn.execute('UPDATE market_batches SET state=?,updated_at=? WHERE id=?',(state,now(),batch_id))
            conn.commit()
