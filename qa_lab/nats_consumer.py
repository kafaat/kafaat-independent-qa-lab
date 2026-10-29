"""JetStream durable consumer: ack only after PostgreSQL transaction commits."""
import asyncio
import os
import psycopg
import nats
from .nats_inbox import INBOX_SCHEMA
from .nats_rejections import REJECTION_SCHEMA, handle_delivery

async def fetch_idle_safe(subscription, *, timeout=2):
    """Normal empty JetStream polls can raise asyncio.TimeoutError in nats-py."""
    try:
        return await subscription.fetch(batch=10, timeout=timeout)
    except (asyncio.TimeoutError, nats.errors.TimeoutError):
        return []


async def run():
    dsn=os.environ['QA_POSTGRES_DSN']
    nc=await nats.connect(os.environ.get('QA_NATS_URL','nats://nats:4222'))
    js=nc.jetstream()
    with psycopg.connect(dsn) as db:
        db.execute(INBOX_SCHEMA)
        db.execute(REJECTION_SCHEMA)
    from nats.js.api import StreamConfig, StorageType, RetentionPolicy
    from nats.js.errors import NotFoundError
    try:
        await js.stream_info('QA_JOBS')
    except NotFoundError:
        await js.add_stream(config=StreamConfig(name='QA_JOBS',subjects=['qa.jobs.*'],storage=StorageType.FILE,retention=RetentionPolicy.LIMITS))
    sub=await js.pull_subscribe('qa.jobs.*',durable='qa-audit-inbox',stream='QA_JOBS')
    try:
        while True:
            messages=await fetch_idle_safe(sub)
            for msg in messages:
                await handle_delivery(msg, lambda: psycopg.connect(dsn))
    finally:
        await nc.drain()

if __name__=='__main__':asyncio.run(run())
