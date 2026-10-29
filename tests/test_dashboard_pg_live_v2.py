"""Live dashboard checks use their own schema in a disposable test database."""
import json
import os
import unittest
import uuid
from qa_lab.pg_dashboard import ReadOnlyRuns

DSN = os.environ.get('QA_TEST_POSTGRES_DSN')


@unittest.skipUnless(DSN and os.environ.get('QA_DASHBOARD_LIVE') == '1', 'dedicated PostgreSQL dashboard test required')
class LiveDashboardTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from qa_lab.pg_queue import SCHEMA
        from qa_lab.nats_outbox import OUTBOX_SCHEMA
        from qa_lab.nats_inbox import INBOX_SCHEMA
        self.pg, self.sql = psycopg, sql
        self.schema = 'qa_dash_test_' + uuid.uuid4().hex
        with psycopg.connect(DSN) as db:
            name = db.execute('SELECT current_database()').fetchone()[0]
            if not name.endswith('_test'):
                self.fail('live test requires a dedicated *_test database')
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        outer = self
        self.queue = type('ScopedQueue', (), {'_connect': lambda _: outer.pg.connect(DSN, options='-csearch_path=' + outer.schema)})()
        with self.queue._connect() as db:
            db.execute(SCHEMA)
            db.execute(OUTBOX_SCHEMA)
            db.execute(INBOX_SCHEMA)
            self.ids = [uuid.UUID(int=1), uuid.UUID(int=2), uuid.UUID(int=3)]
            for i, rid in enumerate(self.ids):
                request = {'project': 'one' if i < 2 else 'two', 'recipe': 'python-smoke', 'source_sha': 'a' * 40}
                receipt = {'status': 'PASSED', 'stdout_tail': '<script>untrusted</script>', 'secret_not_displayed': 'sensitive'}
                db.execute('INSERT INTO qa_jobs(id,idempotency_key,request_hash,request,status,receipt) VALUES(%s,%s,%s,%s::jsonb,%s,%s::jsonb)',
                           (rid, str(rid), 'a' * 64, json.dumps(request), 'PASSED', json.dumps(receipt)))
        self.repository = ReadOnlyRuns(self.queue)

    def drop_schema(self):
        with self.pg.connect(DSN) as db:
            db.execute(self.sql.SQL('DROP SCHEMA {} CASCADE').format(self.sql.Identifier(self.schema)))

    def test_filters_and_cursor_on_real_postgres(self):
        page = self.repository.list({'limit': 1, 'project': 'one', 'status': 'PASSED'})
        self.assertEqual(page['runs'][0]['run_id'], str(self.ids[1]))
        next_page = self.repository.list({'limit': 1, 'project': 'one', 'after': page['next_cursor']})
        self.assertEqual(next_page['runs'][0]['run_id'], str(self.ids[0]))
        self.assertIsNone(next_page['next_cursor'])
        self.assertEqual(self.repository.list({'limit': 5, 'project': "' OR 1=1 --"})['runs'], [])

    def test_details_check_matching_and_mismatched_events(self):
        rid = str(self.ids[0])
        eid = uuid.uuid4()
        payload = {'run_id': rid, 'status': 'PASSED'}
        with self.queue._connect() as db:
            db.execute('INSERT INTO qa_outbox(id,subject,payload,published_at) VALUES(%s,%s,%s::jsonb,now())',
                       (eid, 'qa.jobs.finished', json.dumps(payload)))
            db.execute('INSERT INTO qa_inbox(event_id,subject,payload) VALUES(%s,%s,%s::jsonb)',
                       (eid, 'qa.jobs.finished', json.dumps(payload)))
        detail = self.repository.detail(rid)
        self.assertTrue(detail['events'][0]['payload_matches'])
        self.assertNotIn('secret_not_displayed', detail['receipt'])
        with self.queue._connect() as db:
            db.execute('UPDATE qa_inbox SET payload=%s::jsonb', (json.dumps({'run_id': rid, 'status': 'FAILED'}),))
        self.assertFalse(self.repository.detail(rid)['events'][0]['payload_matches'])

    def test_read_only_transaction_rejects_a_write(self):
        with self.assertRaises(self.pg.errors.ReadOnlySqlTransaction):
            with self.repository.connection() as db:
                db.execute('DELETE FROM qa_jobs')
        self.assertEqual(len(self.repository.list({'limit': 10})['runs']), 3)
