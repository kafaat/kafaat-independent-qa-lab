"""Real PostgreSQL + JetStream acceptance; isolated schema, no production data."""
import asyncio
import hashlib
import json
import os
import unittest
import uuid

from qa_lab.nats_inbox import INBOX_SCHEMA
from qa_lab.nats_rejections import REJECTION_SCHEMA, handle_delivery


@unittest.skipUnless(os.environ.get('QA_REJECTION_AUDIT_LIVE') == '1',
                     'requires dedicated real PostgreSQL and NATS acceptance')
class LiveRejectionAuditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import nats
        import psycopg
        from psycopg import sql
        from nats.js.api import AckPolicy, ConsumerConfig, StorageType, StreamConfig
        self.pg, self.sql = psycopg, sql
        self.dsn = os.environ['QA_TEST_POSTGRES_DSN']
        self.schema = 'qa_reject_' + uuid.uuid4().hex
        self.nc = None
        self.stream = None
        self.addAsyncCleanup(self.cleanup)
        with psycopg.connect(self.dsn) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        with self.connect() as db:
            db.execute(INBOX_SCHEMA)
            db.execute(REJECTION_SCHEMA)
        self.nc = await nats.connect(os.environ['QA_NATS_URL'], connect_timeout=3)
        self.js = self.nc.jetstream()
        stream = 'QA_REJECT_' + uuid.uuid4().hex[:12].upper()
        await self.js.add_stream(config=StreamConfig(
            name=stream, subjects=['qa.jobs.*'], storage=StorageType.MEMORY))
        self.stream = stream
        self.sub = await self.js.pull_subscribe('qa.jobs.*', stream=stream, durable='audit-test',
            config=ConsumerConfig(durable_name='audit-test', ack_policy=AckPolicy.EXPLICIT, ack_wait=5))

    def connect(self, *, read_only=False):
        options = '-c search_path=' + self.schema
        if read_only:
            options += ' -c default_transaction_read_only=on'
        return self.pg.connect(self.dsn, options=options)

    async def cleanup(self):
        try:
            if self.nc:
                if self.stream:
                    await self.js.delete_stream(self.stream)
                await self.nc.close()
        finally:
            if self.nc and not self.nc.is_closed:
                await self.nc.close()
            with self.pg.connect(self.dsn) as db:
                db.execute(self.sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(
                    self.sql.Identifier(self.schema)))

    async def next(self):
        return (await self.sub.fetch(batch=1, timeout=6))[0]

    async def publish(self, raw):
        await self.js.publish('qa.jobs.created', raw)
        return await self.next()

    async def test_collision_and_invalid_json_recorded_not_silently_dropped(self):
        event_id = str(uuid.uuid4())
        original = {'event_id': event_id, 'payload': {'run_id': 'original'}}
        msg = await self.publish(json.dumps(original).encode())
        self.assertEqual(await handle_delivery(msg, self.connect), 'ACCEPTED')
        collision = {**original, 'payload': {'run_id': 'changed'}}
        msg = await self.publish(json.dumps(collision).encode())
        self.assertEqual(await handle_delivery(msg, self.connect), 'REJECTED_RECORDED')
        sentinel = b'Bearer private-sentinel-never-store'
        msg = await self.publish(sentinel)
        self.assertEqual(await handle_delivery(msg, self.connect), 'REJECTED_RECORDED')
        await self.nc.flush(timeout=3)
        with self.connect() as db:
            rows = db.execute('SELECT reason_code,payload_sha256,review_state FROM qa_nats_rejections').fetchall()
            self.assertEqual({r[0] for r in rows}, {'event_id_collision', 'invalid_json'})
            self.assertIn(hashlib.sha256(sentinel).hexdigest(), {r[1] for r in rows})
            self.assertEqual({r[2] for r in rows}, {'REVIEW_REQUIRED'})
            inbox = db.execute('SELECT payload FROM qa_inbox').fetchall()
            self.assertEqual(inbox, [(original['payload'],)])
            columns = db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s "
                                 "AND table_name='qa_nats_rejections'", (self.schema,)).fetchall()
            self.assertNotIn('payload', {r[0] for r in columns})
        info = await self.js.consumer_info(self.stream, 'audit-test')
        self.assertEqual(info.num_ack_pending, 0)

    async def test_storage_write_failure_redelivers_instead_of_term(self):
        msg = await self.publish(b'invalid-data')
        sequence = msg.metadata.sequence.stream
        result = await handle_delivery(msg, lambda: self.connect(read_only=True))
        self.assertEqual(result, 'RETRY')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM qa_nats_rejections').fetchone()[0], 0)
        again = await self.next()
        self.assertEqual(again.metadata.sequence.stream, sequence)
        self.assertGreaterEqual(again.metadata.num_delivered, 2)
        self.assertEqual(await handle_delivery(again, self.connect), 'REJECTED_RECORDED')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM qa_nats_rejections').fetchone()[0], 1)

    async def test_term_failure_after_commit_reuses_durable_review_item(self):
        msg = await self.publish(b'malformed')
        class TermFailure:
            data, subject, metadata = msg.data, msg.subject, msg.metadata
            async def term(self): raise OSError('simulated transport failure')
            async def nak(self, **kwargs): await msg.nak(**kwargs)
        self.assertEqual(await handle_delivery(TermFailure(), self.connect), 'RETRY')
        with self.connect() as db:
            first = db.execute('SELECT rejection_id,observations FROM qa_nats_rejections').fetchone()
            self.assertEqual(first[1], 1)
        again = await self.next()
        self.assertEqual(again.metadata.sequence.stream, msg.metadata.sequence.stream)
        self.assertEqual(await handle_delivery(again, self.connect), 'REJECTED_RECORDED')
        with self.connect() as db:
            rows = db.execute('SELECT rejection_id,observations,max_delivery_count FROM qa_nats_rejections').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][:2], (first[0], 2))
            self.assertGreaterEqual(rows[0][2], 2)


if __name__ == '__main__': unittest.main()
