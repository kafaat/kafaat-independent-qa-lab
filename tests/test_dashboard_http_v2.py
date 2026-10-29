import http.client
import json
import os
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from qa_lab.pg_dashboard import Dashboard

TOKEN = 'unit-test-only-not-a-real-secret-123'
RID = str(uuid.uuid4())


class Store:
    options = None
    failed = False
    def list(self, options):
        self.options = options
        if self.failed:
            raise RuntimeError('SECRET database connection string')
        return {'runs': [{'run_id': RID, 'status': 'PASSED', 'project': 'lab', 'recipe': 'python-smoke'}], 'next_cursor': None}
    def detail(self, rid):
        if rid != RID:
            raise KeyError(rid)
        return {'run_id': rid, 'receipt': {'stdout_tail': '<script>bad</script>'}, 'production_accepted': False}


class DashboardHTTPTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'QA_DASHBOARD_TOKEN': TOKEN})
        self.env.start()
        self.store = Store()
        handler = type('TestDashboard', (Dashboard,), {'store': self.store})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.env.stop()
    def request(self, path='/', token=TOKEN, method='GET'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            headers = {'Authorization': 'Bearer ' + token} if token is not None else {}
            conn.request(method, path, headers=headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode()
        finally:
            conn.close()
    def test_root_and_headers(self):
        status, headers, body = self.request()
        self.assertEqual(status, 200)
        self.assertIn(RID, body)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
    def test_missing_and_wrong_auth(self):
        for token in (None, 'wrong'):
            self.assertEqual(self.request(token=token)[0], 401)
    def test_filters_passed_to_repository(self):
        status, _, body = self.request('/api/runs?project=lab&status=PASSED&limit=2')
        self.assertEqual(status, 200)
        self.assertEqual(self.store.options['project'], 'lab')
        self.assertEqual(json.loads(body)['runs'][0]['run_id'], RID)
    def test_invalid_filter_rejected(self):
        self.assertEqual(self.request('/?limit=10000')[0], 400)
    def test_details_escape_untrusted_logs(self):
        status, _, body = self.request('/runs/' + RID)
        self.assertEqual(status, 200)
        self.assertNotIn('<script>', body)
        self.assertIn('&lt;script&gt;', body)
    def test_detail_json_and_missing(self):
        self.assertEqual(self.request('/api/runs/' + RID)[0], 200)
        self.assertEqual(self.request('/runs/' + str(uuid.uuid4()))[0], 404)
        self.assertEqual(self.request('/runs/not-valid')[0], 400)
    def test_unknown_route_and_write_rejected(self):
        self.assertEqual(self.request('/other')[0], 404)
        self.assertEqual(self.request(method='POST')[0], 405)
    def test_database_error_is_sanitized(self):
        self.store.failed = True
        status, _, body = self.request()
        self.assertEqual(status, 503)
        self.assertNotIn('SECRET', body)
    def test_duplicate_authorization_rejected(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            conn.putrequest('GET', '/')
            conn.putheader('Authorization', 'Bearer ' + TOKEN)
            conn.putheader('Authorization', 'Bearer ' + TOKEN)
            conn.endheaders()
            response = conn.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
        finally:
            conn.close()
