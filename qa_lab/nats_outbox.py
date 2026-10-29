"""PostgreSQL transactional outbox and NATS JetStream publisher.

PostgreSQL remains source of truth. At-least-once delivery is expected;
consumer deduplication is required. No claim of exactly-once effects.
"""
import json
import uuid

OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_outbox (
 id UUID PRIMARY KEY,
 subject TEXT NOT NULL,
 payload JSONB NOT NULL,
 published_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS qa_outbox_pending_idx
 ON qa_outbox (created_at) WHERE published_at IS NULL;
"""

def enqueue(conn,subject,payload,event_id=None):
    if subject not in ('qa.jobs.created','qa.jobs.finished'):
        raise ValueError('subject not allowlisted')
    if not isinstance(payload,dict):raise ValueError('payload must be object')
    eid=uuid.UUID(str(event_id)) if event_id else uuid.uuid4()
    data=json.dumps(payload,ensure_ascii=False,sort_keys=True,allow_nan=False)
    if len(data.encode())>65536:raise ValueError('event too large')
    conn.execute('INSERT INTO qa_outbox(id,subject,payload) VALUES (%s,%s,%s::jsonb) ON CONFLICT(id) DO NOTHING',(eid,subject,data))
    return str(eid)

def publish_batch(conn, publish,limit=20):
    """Call inside a DB transaction. Publish then mark; crash can redeliver."""
    if not 1<=limit<=100:raise ValueError('invalid batch size')
    rows=conn.execute("""SELECT id,subject,payload FROM qa_outbox
        WHERE published_at IS NULL ORDER BY created_at,id
        FOR UPDATE SKIP LOCKED LIMIT %s""",(limit,)).fetchall()
    count=0
    for eid,subject,payload in rows:
        publish(subject,{'event_id':str(eid),'payload':payload})
        conn.execute('UPDATE qa_outbox SET published_at=now() WHERE id=%s AND published_at IS NULL',(eid,))
        count+=1
    return count
