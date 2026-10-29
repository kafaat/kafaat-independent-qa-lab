"""Experimental PostgreSQL queue; deliberately not wired to production worker.

Requires psycopg>=3 and a dedicated PostgreSQL instance. A RUNNING job
is never automatically reclaimed; unknown outcomes require reconciliation.
"""
import hashlib
import json
import re
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_jobs (
 id UUID PRIMARY KEY,
 idempotency_key TEXT NOT NULL UNIQUE,
 request_hash CHAR(64) NOT NULL,
 request JSONB NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PENDING','RUNNING','PASSED','FAILED','BLOCKED','TIMED_OUT')),
 worker_id TEXT,
 receipt JSONB
);
"""

def canonical(request):
    return json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def validate(request, request_hash):
    if not isinstance(request,dict):
        raise ValueError('request must be object')
    if not isinstance(request_hash,str) or not re.fullmatch('[0-9a-f]{64}',request_hash):
        raise ValueError('invalid request digest')
    try:
        payload=canonical(request).encode('utf-8')
    except (TypeError,ValueError):
        raise ValueError('invalid request JSON') from None
    if len(payload)>1024*1024:raise ValueError('request too large')
    if hashlib.sha256(payload).hexdigest()!=request_hash:
        raise ValueError('request digest mismatch')
    key=request.get('idempotency_key')
    if not isinstance(key,str) or not 1<=len(key)<=128:
        raise ValueError('invalid idempotency key')
    return payload.decode('utf-8')

class PostgresQueue:
    def __init__(self,dsn):
        if not isinstance(dsn,str) or not dsn:raise ValueError('PostgreSQL DSN required')
        self.dsn=dsn
    def _connect(self):
        import psycopg
        return psycopg.connect(self.dsn)
    def init(self):
        with self._connect() as db:
            db.execute(SCHEMA)
    def submit(self,request,request_hash):
        serialized=validate(request,request_hash)
        with self._connect() as db:
            with db.transaction():
                row=db.execute('SELECT id,request_hash FROM qa_jobs WHERE idempotency_key=%s FOR UPDATE',(request['idempotency_key'],)).fetchone()
                if row:
                    if row[1]!=request_hash:raise ValueError('idempotency key conflict')
                    return {'run_id':str(row[0]),'reused':True}
                rid=uuid.uuid4()
                db.execute('INSERT INTO qa_jobs(id,idempotency_key,request_hash,request,status) VALUES (%s,%s,%s,%s::jsonb,%s)',(rid,request['idempotency_key'],request_hash,serialized,'PENDING'))
                return {'run_id':str(rid),'reused':False}
    def claim(self,worker_id):
        if not isinstance(worker_id,str) or not 1<=len(worker_id)<=128:raise ValueError('invalid worker')
        with self._connect() as db:
            with db.transaction():
                row=db.execute("""WITH selected AS (
SELECT id FROM qa_jobs WHERE status='PENDING' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1
) UPDATE qa_jobs SET status='RUNNING',worker_id=%s WHERE id IN (SELECT id FROM selected)
RETURNING id,request""",(worker_id,)).fetchone()
                return {'run_id':str(row[0]),'request':row[1]} if row else None
    def get(self,run_id):
        try: rid=uuid.UUID(str(run_id))
        except (ValueError,TypeError,AttributeError):raise ValueError('invalid run id') from None
        with self._connect() as db:
            row=db.execute('SELECT id,status,worker_id,receipt FROM qa_jobs WHERE id=%s',(rid,)).fetchone()
        if row is None:raise KeyError(run_id)
        return {'run_id':str(row[0]),'status':row[1],'worker_id':row[2],'receipt':row[3]}
    def reconcile(self,run_id):
        item=self.get(run_id)
        return {'run_id':item['run_id'],'status':item['status'],'action':'manual-review' if item['status']=='RUNNING' else 'none'}
    def finish(self,run_id,worker_id,status,receipt):
        if status not in ('PASSED','FAILED','BLOCKED','TIMED_OUT'):raise ValueError('invalid terminal status')
        if not isinstance(worker_id,str) or not 1<=len(worker_id)<=128:raise ValueError('invalid worker')
        try: rid=uuid.UUID(str(run_id))
        except (ValueError,TypeError,AttributeError):raise ValueError('invalid run id') from None
        try: data=canonical(receipt).encode('utf-8')
        except (TypeError,ValueError):raise ValueError('invalid receipt') from None
        if not isinstance(receipt,dict) or len(data)>1024*1024:raise ValueError('invalid receipt')
        with self._connect() as db:
            with db.transaction():
                row=db.execute("UPDATE qa_jobs SET status=%s,receipt=%s::jsonb WHERE id=%s AND status='RUNNING' AND worker_id=%s RETURNING id",(status,data.decode('utf-8'),rid,worker_id)).fetchone()
                if row is None:raise ValueError('run not owned or not running')
                return {'run_id':str(row[0]),'status':status}
