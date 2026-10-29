"""Real JetStream redelivery test with PostgreSQL inbox deduplication."""
import asyncio
import json
import os
import unittest
import uuid

DSN=os.environ.get('QA_TEST_POSTGRES_DSN')
URL=os.environ.get('QA_NATS_URL')

@unittest.skipUnless(DSN and URL,'requires real NATS and PostgreSQL')
class NATSRedeliveryTests(unittest.TestCase):
    def test_nak_redelivery_does_not_duplicate_inbox(self):
        asyncio.run(self.scenario())
    async def scenario(self):
        import nats
        import psycopg
        from nats.js.api import StreamConfig,StorageType,ConsumerConfig,AckPolicy
        from qa_lab.nats_inbox import INBOX_SCHEMA,accept_event
        nc=await nats.connect(URL,connect_timeout=5)
        js=nc.jetstream()
        stream='QA_RED_'+uuid.uuid4().hex[:12].upper()
        subject='qa.redelivery.'+uuid.uuid4().hex
        eid=str(uuid.uuid4())
        try:
            await js.add_stream(config=StreamConfig(name=stream,subjects=[subject],storage=StorageType.MEMORY))
            sub=await js.pull_subscribe(subject,durable='redelivery-test',stream=stream,config=ConsumerConfig(durable_name='redelivery-test',ack_policy=AckPolicy.EXPLICIT,ack_wait=1))
            event={'event_id':eid,'payload':{'run_id':'r'}}
            await js.publish(subject,json.dumps(event).encode())
            first=(await sub.fetch(batch=1,timeout=5))[0]
            with psycopg.connect(DSN) as db:
                db.execute(INBOX_SCHEMA)
                with db.transaction():
                    self.assertTrue(accept_event(db,'qa.jobs.created',event))
            await first.nak()
            second=(await sub.fetch(batch=1,timeout=5))[0]
            with psycopg.connect(DSN) as db:
                with db.transaction():
                    self.assertFalse(accept_event(db,'qa.jobs.created',event))
            await second.ack()
        finally:
            with __import__('psycopg').connect(DSN) as db:
                db.execute('DELETE FROM qa_inbox WHERE event_id=%s',(uuid.UUID(eid),))
            try:await js.delete_stream(stream)
            finally:await nc.drain()
