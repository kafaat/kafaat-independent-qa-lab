"""Experimental outbox relay using JetStream publish acknowledgements."""
import asyncio
import json
import os
import sys
from .nats_outbox import OUTBOX_SCHEMA, publish_batch

MAX_BACKOFF_S = 60


def _log(message):
    print(message, file=sys.stderr, flush=True)


async def relay_once(connect, js):
    """Publish up to 20 unpublished outbox rows; return how many were sent."""
    with connect() as db:
        with db.transaction():
            rows = db.execute("""SELECT id,subject,payload FROM qa_outbox
              WHERE published_at IS NULL ORDER BY created_at,id
              FOR UPDATE SKIP LOCKED LIMIT 20""").fetchall()
            for eid, subject, payload in rows:
                body = json.dumps({'event_id': str(eid), 'payload': payload}, sort_keys=True).encode()
                await js.publish(subject, body, headers={'Nats-Msg-Id': str(eid)}, timeout=5)
                db.execute('UPDATE qa_outbox SET published_at=now() WHERE id=%s AND published_at IS NULL', (eid,))
    return len(rows)


async def relay_loop(connect, js, *, transient, sleep=asyncio.sleep, idle_s=1, iterations=None):
    """Create the schema, then relay forever, waiting out transient database errors.

    A connection-level error (DNS, refused, dropped) rolls back the open
    transaction, so rows stay unpublished and are retried; a row published to
    JetStream but not yet marked is deduplicated by its Nats-Msg-Id. Backoff
    is 2, 4, 8 ... seconds capped at 60 and reset after a successful pass.
    Only the exception type is logged, never the DSN. On Railway the relay
    previously exited on such errors and spent its restart budget.
    """
    initialised = False
    failures = 0
    count = 0
    while iterations is None or count < iterations:
        count += 1
        try:
            if not initialised:
                with connect() as db:
                    db.execute(OUTBOX_SCHEMA)
                initialised = True
            await relay_once(connect, js)
            failures = 0
        except transient as exc:
            failures += 1
            delay = min(MAX_BACKOFF_S, 2 * (2 ** min(failures - 1, 6)))
            _log(f'nats_relay: database unavailable ({type(exc).__name__}); retry {failures} in {delay}s')
            await sleep(delay)
            continue
        await sleep(idle_s)


async def run():
    import nats
    import psycopg
    dsn = os.environ['QA_POSTGRES_DSN']
    url = os.environ.get('QA_NATS_URL', 'nats://nats:4222')
    nc = await nats.connect(url, connect_timeout=5, max_reconnect_attempts=-1)
    try:
        await relay_loop(lambda: psycopg.connect(dsn), nc.jetstream(),
                         transient=(psycopg.OperationalError,))
    finally:
        await nc.drain()

if __name__ == '__main__': asyncio.run(run())
