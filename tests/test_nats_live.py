"""Live NATS JetStream integration with PostgreSQL-backed deduplication."""
import asyncio
import json
import os
import unittest
import uuid
from qa_lab.nats_inbox import INBOX_SCHEMA, accept_event

DSN=os.environ.get('QA_TEST_POSTGRES_DSN')
URL=os.environ.get('QA_NATS_URL')

@unittest.skipUnless(DSN and URL,'requires real NATS and PostgreSQL')
class NATSJetStreamLiveTests(unittest.TestCase):
    def test_publish_receive_and_duplicate_inbox(self):
        asyncio.run(self._scenario())
    async def _scenario(self):
        import nats
        import psycopg
        from nats.js.api import StreamConfig, StorageType
        nc=await nats.connect(URL,connect_timeout=5)
        js=nc.jetstream()
        stream='QA_TEST_'+uuid.uuid4().hex[:12].upper()
        subject='qa.test.'+uuid.uuid4().hex
        eid=str(uuid.uuid4())
        try:
            await js.add_stream(config=StreamConfig(name=stream,subjects=[subject],storage=StorageType.MEMORY))
            sub=await js.pull_subscribe(subject,durable='test-consumer',stream=stream)
            payload={'event_id':eid,'payload':{'run_id':'test'}}
            await js.publish(subject,json.dumps(payload).encode(),headers={'Nats-Msg-Id':eid})
            messages=await sub.fetch(batch=1,timeout=5)
            self.assertEqual(len(messages),1)
            with psycopg.connect(DSN) as db:
                db.execute(INBOX_SCHEMA)
                with db.transaction():
                    self.assertTrue(accept_event(db,'qa.jobs.created',payload))
            # Redelivery or replay of the same event must not repeat the effect.
            with psycopg.connect(DSN) as db:
                with db.transaction():
                    self.assertFalse(accept_event(db,'qa.jobs.created',payload))
            await messages[0].ack()
        finally:
            with psycopg.connect(DSN) as db:
                db.execute('DELETE FROM qa_inbox WHERE event_id=%s',(uuid.UUID(eid),))
            try:await js.delete_stream(stream)
            finally:await nc.drain()

    def test_event_id_collision_rejected_without_mutating_inbox(self):
        import psycopg
        eid=str(uuid.uuid4())
        original={'event_id':eid,'payload':{'run_id':'identity','status':'PASSED'}}
        try:
            with psycopg.connect(DSN) as db:
                db.execute(INBOX_SCHEMA)
                self.assertTrue(accept_event(db,'qa.jobs.finished',original))
            with psycopg.connect(DSN) as db:
                self.assertFalse(accept_event(db,'qa.jobs.finished',original))
                with self.assertRaisesRegex(ValueError,'event id collision'):
                    accept_event(db,'qa.jobs.finished',{
                        'event_id':eid,'payload':{'run_id':'forged','status':'PASSED'}})
                with self.assertRaisesRegex(ValueError,'event id collision'):
                    accept_event(db,'qa.jobs.created',original)
                row=db.execute('SELECT subject,payload FROM qa_inbox WHERE event_id=%s',
                               (uuid.UUID(eid),)).fetchone()
                self.assertEqual(row,('qa.jobs.finished',original['payload']))
        finally:
            with psycopg.connect(DSN) as db:
                db.execute('DELETE FROM qa_inbox WHERE event_id=%s',(uuid.UUID(eid),))

    def test_idle_timeout_then_next_message(self):
        asyncio.run(self._idle_timeout_then_next_message())

    async def _idle_timeout_then_next_message(self):
        import nats
        from nats.js.api import StreamConfig, StorageType
        from qa_lab.nats_consumer import fetch_idle_safe
        nc=await nats.connect(URL,connect_timeout=5)
        js=nc.jetstream()
        stream='QA_IDLE_'+uuid.uuid4().hex[:12].upper()
        subject='qa.idle.'+uuid.uuid4().hex
        try:
            await js.add_stream(config=StreamConfig(
                name=stream,subjects=[subject],storage=StorageType.MEMORY))
            sub=await js.pull_subscribe(subject,durable='idle-test',stream=stream)
            self.assertEqual(await fetch_idle_safe(sub,timeout=0.25),[])
            event={'event_id':str(uuid.uuid4()),'payload':{'run_id':'idle-test'}}
            await js.publish(subject,json.dumps(event).encode())
            batch=await fetch_idle_safe(sub,timeout=4)
            self.assertEqual(len(batch),1)
            self.assertEqual(json.loads(batch[0].data),event)
            await batch[0].ack()
        finally:
            try:
                await js.delete_stream(stream)
            finally:
                await nc.drain()
