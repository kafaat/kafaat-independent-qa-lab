"""One-shot internal Railway E2E acceptance probe.

Uses a real PostgreSQL database and NATS JetStream. Creates one allowlisted
smoke run, waits for worker terminal receipt, and verifies durable events.
Does not expose secrets or claim host attestation.
"""
import asyncio
import hashlib
import json
import os
import time
import uuid

async def main():
    import nats
    from .pg_queue import PostgresQueue,canonical
    dsn=os.environ['QA_POSTGRES_DSN']
    nc=await nats.connect(os.environ['QA_NATS_URL'],connect_timeout=5)
    js=nc.jetstream()
    q=PostgresQueue(dsn)
    q.init()
    key='railway-probe-'+uuid.uuid4().hex
    req={'idempotency_key':key,'project':'railway-acceptance','source_sha':os.environ['QA_SOURCE_SHA'],'recipe':'python-smoke'}
    rid=q.submit(req,hashlib.sha256(canonical(req).encode()).hexdigest())['run_id']
    deadline=time.monotonic()+60
    try:
        while time.monotonic()<deadline:
            run=q.get(rid)
            if run['status'] in ('PASSED','FAILED','BLOCKED','TIMED_OUT'):break
            await asyncio.sleep(1)
        else:raise RuntimeError('worker did not finish within acceptance window')
        if run['status']!='PASSED':raise RuntimeError('worker receipt not PASSED')
        if run['receipt'].get('source_sha')!=req['source_sha']:raise RuntimeError('source SHA mismatch')
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            with q._connect() as db:
                events=db.execute("SELECT subject,published_at FROM qa_outbox WHERE payload->>'run_id'=%s",(rid,)).fetchall()
                inbox=db.execute("SELECT count(*) FROM qa_inbox WHERE payload->>'run_id'=%s",(rid,)).fetchone()[0]
            if len(events)==2 and all(x[1] is not None for x in events) and inbox>=2:break
            await asyncio.sleep(1)
        else:raise RuntimeError('outbox or inbox evidence incomplete: '+json.dumps({'outbox_count':len(events),'outbox_published':sum(x[1] is not None for x in events),'inbox_count':inbox}))
        if sorted(x[0] for x in events)!=['qa.jobs.created','qa.jobs.finished']:raise RuntimeError('unexpected event set')
        await js.stream_info('QA_JOBS')
        print(json.dumps({'status':'PASSED','run_id':rid,'source_sha':req['source_sha'],'outbox_events':2,'inbox_events':inbox,'authority':'execution-evidence-only'}))
    finally:
        await nc.drain()

if __name__=='__main__':asyncio.run(main())
