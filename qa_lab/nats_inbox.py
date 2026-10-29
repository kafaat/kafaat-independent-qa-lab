"""Durable JetStream consumer inbox: at-least-once delivery, idempotent processing.

The handler executes only on first delivery. A redelivered event ID is
acknowledged without repeating effects. Caller must ack after DB commit.
"""
import json
import uuid

INBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_inbox (
 event_id UUID PRIMARY KEY,
 subject TEXT NOT NULL,
 payload JSONB NOT NULL,
 processed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

def accept_event(conn, subject, event):
    if subject not in ('qa.jobs.created','qa.jobs.finished'):
        raise ValueError('subject not allowlisted')
    if not isinstance(event,dict) or set(event)!={'event_id','payload'}:
        raise ValueError('invalid event envelope')
    try: eid=uuid.UUID(str(event['event_id']))
    except (TypeError,ValueError,AttributeError):raise ValueError('invalid event id') from None
    if not isinstance(event['payload'],dict):raise ValueError('invalid event payload')
    payload=json.dumps(event['payload'],ensure_ascii=False,sort_keys=True,allow_nan=False)
    if len(payload.encode())>65536:raise ValueError('event too large')
    row=conn.execute("""INSERT INTO qa_inbox(event_id,subject,payload)
        VALUES (%s,%s,%s::jsonb) ON CONFLICT(event_id) DO NOTHING RETURNING event_id""",
        (eid,subject,payload)).fetchone()
    if row is not None:
        return True
    # A duplicate UUID alone does not prove a valid replay. The original
    # subject and JSONB payload must agree before it is treated as idempotent.
    previous=conn.execute('SELECT subject,payload FROM qa_inbox WHERE event_id=%s',
                          (eid,)).fetchone()
    if previous is None:
        # A conflicting row invisible to this transaction is not an ACK.
        raise ValueError('unverifiable duplicate event id')
    if previous[0]!=subject or previous[1]!=json.loads(payload):
        raise ValueError('event id collision')
    return False
