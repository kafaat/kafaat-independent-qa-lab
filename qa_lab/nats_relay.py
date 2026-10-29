"""Experimental outbox relay using JetStream publish acknowledgements."""
import asyncio
import json
import os
from .nats_outbox import OUTBOX_SCHEMA, publish_batch

async def run():
    import nats
    import psycopg
    dsn=os.environ['QA_POSTGRES_DSN']
    url=os.environ.get('QA_NATS_URL','nats://nats:4222')
    nc=await nats.connect(url,connect_timeout=5,max_reconnect_attempts=-1)
    js=nc.jetstream()
    with psycopg.connect(dsn) as db:
        db.execute(OUTBOX_SCHEMA)
    try:
        while True:
            with psycopg.connect(dsn) as db:
                with db.transaction():
                    rows=db.execute("""SELECT id,subject,payload FROM qa_outbox
                      WHERE published_at IS NULL ORDER BY created_at,id
                      FOR UPDATE SKIP LOCKED LIMIT 20""").fetchall()
                    for eid,subject,payload in rows:
                        body=json.dumps({'event_id':str(eid),'payload':payload},sort_keys=True).encode()
                        await js.publish(subject,body,headers={'Nats-Msg-Id':str(eid)},timeout=5)
                        db.execute('UPDATE qa_outbox SET published_at=now() WHERE id=%s AND published_at IS NULL',(eid,))
            await asyncio.sleep(1)
    finally:
        await nc.drain()

if __name__=='__main__':asyncio.run(run())
