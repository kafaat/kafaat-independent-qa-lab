"""Behavioural evidence-integrity and read-only service startup regressions."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from qa_lab.knowledge_index import FIELDS, KnowledgeIndex, digest
from qa_lab import knowledge_service


class DocumentCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'index.sqlite'
        self.index = KnowledgeIndex(self.path)
        self.document = dict(project='lab', document_id='manual', source_sha='a' * 40,
                             text='worker approval requires independent evidence. ' * 30)
        self.index.ingest(**self.document, chunk_size=256)
        self.scope = dict(project='lab', source_versions={'manual': 'a' * 40})

    def mutate(self, sql, params=(), rehash=False):
        with closing(self.index.connect()) as db, db:
            db.execute(sql, params)
            if rehash:
                for item in db.execute('SELECT * FROM qa_knowledge_chunks').fetchall():
                    fields = {key: item[key] for key in FIELDS}
                    db.execute('UPDATE qa_knowledge_chunks SET integrity_sha256=? '
                               'WHERE project=? AND document_id=? AND source_sha=? AND ordinal=?',
                               (digest(fields), item['project'], item['document_id'],
                                item['source_sha'], item['ordinal']))

    def assert_rejected(self):
        with self.assertRaisesRegex(ValueError, 'completeness'):
            self.index.answer('worker', **self.scope)

    def test_complete_document_remains_retrievable(self):
        self.assertEqual(self.index.answer('worker', **self.scope)['status'], 'EVIDENCE_FOUND')
        self.assertEqual(self.index.verify_scope(**self.scope)['documents'], 1)

    def test_deleted_first_chunk_rejected(self):
        self.mutate('DELETE FROM qa_knowledge_chunks WHERE ordinal=0')
        self.assert_rejected()

    def test_deleted_middle_chunk_rejected(self):
        self.mutate('DELETE FROM qa_knowledge_chunks WHERE ordinal=2')
        self.assert_rejected()

    def test_deleted_final_chunk_rejected(self):
        self.mutate('DELETE FROM qa_knowledge_chunks WHERE ordinal='
                    '(SELECT MAX(ordinal) FROM qa_knowledge_chunks)')
        self.assert_rejected()

    def test_ordinal_gap_even_with_valid_chunk_hash_rejected(self):
        self.mutate('UPDATE qa_knowledge_chunks SET ordinal=ordinal+10000', rehash=True)
        self.assert_rejected()

    def test_offset_gap_even_with_valid_chunk_hash_rejected(self):
        self.mutate('UPDATE qa_knowledge_chunks SET offset_start=offset_start+1, '
                    'offset_end=offset_end+1', rehash=True)
        self.assert_rejected()

    def test_different_document_hash_even_with_valid_chunk_hash_rejected(self):
        self.mutate("UPDATE qa_knowledge_chunks SET document_sha256=?", ('b' * 64,), rehash=True)
        self.assert_rejected()

    def test_partial_unmatched_document_is_still_checked(self):
        self.index.ingest(project='lab', document_id='rules', source_sha='a' * 40,
                          text='separate policy content ' * 30, chunk_size=256)
        self.mutate("DELETE FROM qa_knowledge_chunks WHERE document_id='rules' AND ordinal=1")
        with self.assertRaisesRegex(ValueError, 'completeness'):
            self.index.retrieve('worker', project='lab',
                                source_versions={'manual': 'a' * 40, 'rules': 'a' * 40})

    def test_corruption_outside_project_scope_is_not_loaded(self):
        self.index.ingest(project='other', document_id='secret', source_sha='b' * 40,
                          text='private worker information')
        self.mutate("UPDATE qa_knowledge_chunks SET text='changed' WHERE project='other'")
        self.assertEqual(self.index.answer('worker', **self.scope)['status'], 'EVIDENCE_FOUND')

    def test_completely_deleted_document_fails_startup_verification(self):
        self.mutate('DELETE FROM qa_knowledge_chunks')
        with self.assertRaisesRegex(ValueError, 'missing pinned'):
            self.index.verify_scope(**self.scope)

    def test_missing_version_is_not_substituted(self):
        with self.assertRaisesRegex(ValueError, 'missing pinned'):
            self.index.verify_scope(project='lab', source_versions={'manual': 'b' * 40})

    def test_readonly_index_retrieves_without_writing(self):
        index = KnowledgeIndex(self.path, read_only=True)
        self.assertEqual(index.answer('worker', **self.scope)['status'], 'EVIDENCE_FOUND')
        with self.assertRaisesRegex(ValueError, 'read-only'):
            index.ingest(**self.document)

    def test_sqlite_itself_denies_writes(self):
        index = KnowledgeIndex(self.path, read_only=True)
        with closing(index.connect()) as db, self.assertRaises(sqlite3.OperationalError):
            db.execute('DELETE FROM qa_knowledge_chunks')

    def test_readonly_missing_path_does_not_create_index(self):
        missing = self.path.parent / 'missing.sqlite'
        with self.assertRaisesRegex(ValueError, 'existing regular'):
            KnowledgeIndex(missing, read_only=True)
        self.assertFalse(missing.exists())

    def test_uri_characters_in_filename_are_encoded(self):
        special = self.path.parent / 'knowledge?# name.sqlite'
        special.write_bytes(self.path.read_bytes())
        index = KnowledgeIndex(special, read_only=True)
        self.assertEqual(index.verify_scope(**self.scope)['documents'], 1)

    def test_service_startup_rejects_missing_pinned_sources(self):
        env = {'QA_KNOWLEDGE_TOKEN': 'x' * 32, 'QA_KNOWLEDGE_DB': str(self.path),
               'QA_KNOWLEDGE_SCOPE': json.dumps({'project': 'lab', 'sources': {'manual': 'b' * 40}})}
        with patch.dict('os.environ', env), patch.object(knowledge_service, 'ThreadingHTTPServer') as server:
            with self.assertRaisesRegex(ValueError, 'missing pinned'):
                knowledge_service.main()
            server.assert_not_called()

    def test_duplicate_server_scope_keys_rejected(self):
        env = {'QA_KNOWLEDGE_TOKEN': 'x' * 32, 'QA_KNOWLEDGE_DB': str(self.path),
               'QA_KNOWLEDGE_SCOPE': '{"project":"lab","project":"other","sources":{}}'}
        with patch.dict('os.environ', env), self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            knowledge_service.main()

    def test_duplicate_query_keys_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
            json.loads('{"query":"worker","query":"override"}',
                       object_pairs_hook=knowledge_service.unique_object)

    def test_single_source_snapshot_transaction(self):
        statements = []
        connect = self.index.connect
        def tracked_connect():
            db = connect()
            db.set_trace_callback(statements.append)
            return db
        with patch.object(self.index, 'connect', tracked_connect):
            self.index.verify_scope(**self.scope)
        self.assertIn('BEGIN', statements)
        self.assertIn('COMMIT', statements)


class KnowledgeReadOnlyHTTPTests(unittest.TestCase):
    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'index.sqlite'
        self.writer = KnowledgeIndex(self.path)
        self.writer.ingest(project='lab', document_id='manual', source_sha='a' * 40,
                           text='worker safety rules ' * 40, chunk_size=256)
        self.environment = patch.dict('os.environ', {'QA_KNOWLEDGE_TOKEN': 'k' * 32})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        handler = type('ReviewKnowledge', (knowledge_service.KnowledgeHandler,),
                       {'index': KnowledgeIndex(self.path, read_only=True),
                        'scope': {'project': 'lab', 'sources': {'manual': 'a' * 40}}})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def post(self, body):
        import http.client
        with closing(http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)) as client:
            client.request('POST', '/v1/answer', body=body,
                           headers={'Authorization': 'Bearer ' + 'k' * 32,
                                    'Content-Type': 'application/json'})
            response = client.getresponse()
            return response.status, json.loads(response.read())

    def test_duplicate_query_is_http_400(self):
        code, body = self.post('{"query":"worker","query":"changed"}')
        self.assertEqual(code, 400)
        self.assertNotIn('answer', body)

    def test_missing_chunk_is_http_400_not_partial_answer(self):
        with closing(self.writer.connect()) as db, db:
            db.execute('DELETE FROM qa_knowledge_chunks WHERE ordinal='
                       '(SELECT max(ordinal) FROM qa_knowledge_chunks)')
        code, body = self.post('{"query":"worker"}')
        self.assertEqual(code, 400)
        self.assertNotIn('answer', body)

    def test_valid_readonly_request_does_not_change_database(self):
        before = self.path.read_bytes()
        code, body = self.post('{"query":"worker"}')
        self.assertEqual(code, 200)
        self.assertEqual(body['status'], 'EVIDENCE_FOUND')
        self.assertEqual(self.path.read_bytes(), before)
