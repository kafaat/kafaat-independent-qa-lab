"""Real PostgreSQL 18 + HTTP operator review in a disposable isolated schema."""
import http.client
import json
import os
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from qa_lab.pg_dashboard import Dashboard
from qa_lab.rejection_review import FIELDS, ReadOnlyRejections

DSN = os.environ.get('QA_TEST_POSTGRES_DSN')
LIVE = os.environ.get('QA_REJECTION_REVIEW_LIVE') == '1'
TOKEN = 'local-disposable-review-live-token-123456'


@unittest.skipUnless(DSN and LIVE, 'requires dedicated real PostgreSQL review CI')
class LiveReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from qa_lab.nats_rejections import REJECTION_SCHEMA
        cls.psycopg, cls.sql = psycopg, sql
        cls.schema = 'qa_review_' + uuid.uuid4().hex
        with psycopg.connect(DSN) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
        try:
            with cls.connect() as db:
                db.execute(REJECTION_SCHEMA)
        except BaseException:
            cls.tearDownClass()
            raise
    @classmethod
    def connect(cls):
        return cls.psycopg.connect(DSN, options='-c search_path=' + cls.schema)
    @classmethod
    def tearDownClass(cls):
        with cls.psycopg.connect(DSN) as db:
            db.execute(cls.sql.SQL('DROP SCHEMA {} CASCADE').format(cls.sql.Identifier(cls.schema)))
    def setUp(self):
        from qa_lab.nats_rejections import rejection_record, store_rejection
        self.rows = []
        with self.connect() as db:
            db.execute('TRUNCATE qa_nats_rejections')
            for sequence, reason, stream in ((1,'invalid_json','QA_JOBS'),
                    (2,'invalid_event','QA_JOBS'), (3,'invalid_json','OTHER_STREAM')):
                msg = SimpleNamespace(data=b'PRIVATE-BODY-MUST-NOT-BE-SHOWN', subject='qa.jobs.created',
                    metadata=SimpleNamespace(stream=stream, consumer='qa-audit-inbox',
                    sequence=SimpleNamespace(stream=sequence), num_delivered=1))
                record = rejection_record(msg, reason)
                store_rejection(db, record)
                self.rows.append(record)
        self.store = ReadOnlyRejections(SimpleNamespace(_connect=self.connect))
    def test_real_filters_and_keyset_pagination(self):
        filtered = self.store.list({'stream':'QA_JOBS','reason':'invalid_json','limit':10})
        self.assertEqual(len(filtered['rejections']),1)
        self.assertEqual(filtered['rejections'][0]['rejection_id'],self.rows[0]['rejection_id'])
        ids = []
        page = self.store.list({'limit':1})
        while True:
            ids.extend(item['rejection_id'] for item in page['rejections'])
            if page['next_cursor'] is None:
                break
            page = self.store.list({'limit':1,'after':page['next_cursor']})
        self.assertEqual(ids, sorted((r['rejection_id'] for r in self.rows), reverse=True))
        self.assertEqual(len(set(ids)),3)
    def test_detail_metadata_matches_writer(self):
        item = self.store.detail(self.rows[0]['rejection_id'])['rejection']
        self.assertEqual(set(item),set(FIELDS))
        self.assertEqual(item['payload_sha256'],self.rows[0]['payload_sha256'])
        self.assertNotIn('PRIVATE-BODY',json.dumps(item))
        self.assertEqual(item['review_state'],'REVIEW_REQUIRED')
    def test_readonly_transaction_rejects_write(self):
        with self.assertRaises(self.psycopg.errors.ReadOnlySqlTransaction):
            with self.store.connection() as db:
                db.execute('DELETE FROM qa_nats_rejections')
        with self.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM qa_nats_rejections').fetchone()[0],3)
    def test_absent_table_is_error_not_empty_success(self):
        with self.connect() as db:
            db.execute('ALTER TABLE qa_nats_rejections RENAME TO hidden_rejections')
        try:
            with self.assertRaises(self.psycopg.errors.UndefinedTable):
                self.store.list({})
        finally:
            with self.connect() as db:
                db.execute('ALTER TABLE hidden_rejections RENAME TO qa_nats_rejections')
    def test_http_reads_actual_rows_and_leaves_audit_unchanged(self):
        def snapshot():
            with self.connect() as db:
                return db.execute('SELECT rejection_id, observations, last_seen FROM qa_nats_rejections ORDER BY rejection_id').fetchall()
        before = snapshot()
        handler=type('RealReviewHandler',(Dashboard,),{'rejections_store':self.store})
        with patch.dict(os.environ, {'QA_DASHBOARD_TOKEN':TOKEN}):
            with ThreadingHTTPServer(('127.0.0.1',0),handler) as server:
                thread=threading.Thread(target=server.serve_forever);thread.start()
                def call(path, auth=True, method='GET'):
                    conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
                    try:
                        headers={'Authorization':'Bearer '+TOKEN} if auth else {}
                        conn.request(method,path,headers=headers)
                        response=conn.getresponse()
                        return response.status,json.loads(response.read())
                    finally:
                        conn.close()
                try:
                    self.assertEqual(call('/api/rejections',False)[0],401)
                    status,body=call('/api/rejections?stream=QA_JOBS&reason=invalid_json')
                    self.assertEqual(status,200);self.assertEqual(body['page_count'],1)
                    status,body=call('/api/rejections/'+self.rows[0]['rejection_id'])
                    self.assertEqual(status,200);self.assertFalse(body['replay_available'])
                    self.assertEqual(call('/api/rejections',method='POST')[0],405)
                finally:
                    server.shutdown();thread.join()
        self.assertEqual(snapshot(),before)


if __name__ == '__main__':
    unittest.main()
