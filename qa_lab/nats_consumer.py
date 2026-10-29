"""JetStream durable consumer: ack only after PostgreSQL transaction commits."""
import asyncio
import json
import os
import psycopg
import nats
from .nats_inbox import INBOX_SCHEMA, accept_event

async def run():
    dsn=os.environ['QA_POSTGRES_DSN']
    nc=await nats.connect(os.environ.get('QA_NATS_URL','nats://nats:4222'))
    js=nc.jetstream()
    with psycopg.connect(dsn) as db:db.execute(INBOX_SCHEMA)
    from nats.js.api import StreamConfig, StorageType, RetentionPolicy
    from nats.js.errors import NotFoundError
    try:
        await js.stream_info('QA_JOBS')
    except NotFoundError:
        await js.add_stream(config=StreamConfig(name='QA_JOBS',subjects=['qa.jobs.*'],storage=StorageType.FILE,retention=RetentionPolicy.LIMITS))
    sub=await js.pull_subscribe('qa.jobs.*',durable='qa-audit-inbox',stream='QA_JOBS')
    try:
        while True:
            try: messages=await sub.fetch(batch=10,timeout=2)
            except nats.errors.TimeoutError:continue
            for msg in messages:
                try:
                    event=json.loads(msg.data)
                    with psycopg.connect(dsn) as db:
                        with db.transaction():
                            accept_event(db,msg.subject,event)
                    await msg.ack()
                except (ValueError,TypeError,json.JSONDecodeError):
                    # Invalid payloads require explicit review, not an ACK-as-success.
                    await msg.term()
                except Exception:
                    await msg.nak()
    finally:
        await nc.drain()

if __name__=='__main__':asyncio.run(run())
