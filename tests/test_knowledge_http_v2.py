import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from qa_lab.knowledge_index import KnowledgeIndex
from qa_lab.knowledge_service import KnowledgeHandler

TOKEN = 'unit-test-only-knowledge-token-123'


class KnowledgeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.index = KnowledgeIndex(Path(self.tmp.name) / 'db')
        self.index.ingest(project='lab', document_id='guide', source_sha='a' * 40, text='worker recovery instructions')
        self.index.ingest(project='private', document_id='guide', source_sha='a' * 40, text='SECRET worker')
        self.env = patch.dict(os.environ, {'QA_KNOWLEDGE_TOKEN': TOKEN})
        self.env.start()
        handler = type('TestKnowledge', (KnowledgeHandler,), {
            'index': self.index, 'scope': {'project': 'lab', 'sources': {'guide': 'a' * 40}}})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.env.stop()
        self.tmp.cleanup()
    def request(self, body=None, path='/v1/answer', token=TOKEN, content_type='application/json', method='POST'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            headers = {'Content-Type': content_type}
            if token is not None:
                headers['Authorization'] = 'Bearer ' + token
            conn.request(method, path, body=json.dumps(body).encode() if body is not None else b'', headers=headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()
    def test_scoped_answer(self):
        status, data = self.request({'query': 'worker'})
        self.assertEqual(status, 200)
        self.assertEqual(data['status'], 'EVIDENCE_FOUND')
        self.assertNotIn('SECRET', data['answer'])
    def test_no_evidence_abstention(self):
        status, data = self.request({'query': 'unicorn'})
        self.assertEqual(status, 200)
        self.assertEqual(data['status'], 'INSUFFICIENT_EVIDENCE')
    def test_caller_cannot_select_other_project_or_version(self):
        for extra in ({'project': 'private'}, {'source_versions': {'guide': 'b' * 40}}, {'url': 'http://example.test'}):
            self.assertEqual(self.request({'query': 'worker'} | extra)[0], 400)
    def test_auth_required(self):
        self.assertEqual(self.request({'query': 'worker'}, token=None)[0], 401)
        self.assertEqual(self.request({'query': 'worker'}, token='wrong')[0], 401)
    def test_no_http_ingestion(self):
        self.assertEqual(self.request({'text': 'new'}, path='/v1/ingest')[0], 404)
    def test_query_validation(self):
        for body in ({'query': ''}, {'query': 'worker', 'limit': True}, ['worker'], {'query': 'x' * 2001}):
            self.assertEqual(self.request(body)[0], 400)
    def test_media_type_and_size(self):
        self.assertEqual(self.request({'query': 'worker'}, content_type='text/plain')[0], 415)
        self.assertEqual(self.request({'query': 'x' * 9000})[0], 413)
    def test_health_requires_auth(self):
        self.assertEqual(self.request(path='/healthz', method='GET')[0], 200)
        self.assertEqual(self.request(path='/healthz', method='GET', token=None)[0], 401)
