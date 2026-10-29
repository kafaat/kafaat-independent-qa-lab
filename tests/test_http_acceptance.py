import hashlib
import json
import threading
import unittest
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from qa_lab.http_acceptance import (AcceptanceFailure, Config, canonical, exact_events,
                                    private_origin, request, run_checks, validate_token)

TOKEN = 'local-unit-test-only-' + 'a' * 32
SOURCE = 'a' * 40


@contextmanager
def server(handler):
    service = ThreadingHTTPServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(service.server_port)
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=3)
        if thread.is_alive():
            raise RuntimeError('server thread leaked')


class ProbeHTTPTests(unittest.TestCase):
    def scenario(self, *, insecure=False, observer_error=False):
        rid = str(uuid.uuid4())
        state = {'request': None, 'posts': 0}

        class FakeAPI(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, obj, html=False):
                data = obj.encode() if html else json.dumps(obj).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'text/html' if html else 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(data)

            def auth(self):
                return insecure or self.headers.get('Authorization') == 'Bearer ' + TOKEN

            def do_GET(self):
                if self.path == '/healthz':
                    return self.reply(200, {'backend': 'postgres'})
                if not self.auth():
                    return self.reply(401, {})
                if self.path == '/':
                    return self.reply(200, '<html>' + rid + ' PASSED</html>', html=True)
                return self.reply(200, {'run_id': rid, 'status': 'PASSED', 'receipt': {
                    'status': 'PASSED', 'exit_code': 0, 'stdout_tail': 'qa-smoke-ok\n',
                    'source_sha': SOURCE, 'authority': 'execution-evidence-only'}})

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                if not self.auth():
                    return self.reply(401, {})
                req = json.loads(body)
                if req['recipe'] != 'python-smoke':
                    return self.reply(400, {'error': 'recipe not allowed'})
                state['posts'] += 1
                if state['request'] is not None and req != state['request']:
                    return self.reply(400, {'error': 'idempotency key conflict'})
                reuse = state['request'] is not None
                state['request'] = req
                return self.reply(202, {'run_id': rid, 'reused': reuse})

        def observe(run_id):
            self.assertEqual(run_id, rid)
            if observer_error:
                raise RuntimeError('sensitive upstream data must not be copied')
            return [(str(uuid.uuid4()), subject, {'run_id': rid}, 'published', subject, {'run_id': rid})
                    for subject in ('qa.jobs.created', 'qa.jobs.finished')]

        stored = []
        with server(FakeAPI) as url:
            cfg = Config(url, url, TOKEN, TOKEN, SOURCE, timeout=1)
            result = run_checks(cfg, observe, lambda value: stored.append(json.loads(canonical(value))))
        return result, stored

    def test_complete_http_acceptance_and_digest(self):
        result, stored = self.scenario()
        self.assertEqual(result['status'], 'PASSED')
        self.assertEqual(len(result['checks']), 15)
        self.assertTrue(all(check['passed'] for check in result['checks']))
        self.assertEqual(stored[-1], result)
        unsigned = {key: value for key, value in result.items() if key != 'receipt_sha256'}
        self.assertEqual(hashlib.sha256(canonical(unsigned).encode()).hexdigest(), result['receipt_sha256'])
        self.assertNotIn(TOKEN, canonical(stored))
        self.assertEqual(result['source_attestation'], 'request-reference-only')

    def test_auth_bypass_fails_before_submission(self):
        result, stored = self.scenario(insecure=True)
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['failure'], 'api_get_no_token_rejected')
        self.assertNotIn('run_id', result)
        self.assertEqual(stored[-1]['status'], 'FAILED')

    def test_failure_is_recorded_without_exception_secret(self):
        result, stored = self.scenario(observer_error=True)
        self.assertEqual(result['status'], 'FAILED')
        self.assertEqual(result['failure'], 'RuntimeError')
        self.assertNotIn('sensitive upstream', canonical(stored))
        self.assertIn('run_id', stored[-1])

    def test_redirect_is_not_followed(self):
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(302)
                self.send_header('Location', 'https://example.com/')
                self.end_headers()
        with server(Redirect) as url:
            with self.assertRaisesRegex(AcceptanceFailure, 'redirect_refused'):
                request(url, '/', token=TOKEN)

    def test_response_size_is_bounded(self):
        class Large(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '200')
                self.end_headers()
                self.wfile.write(b'x' * 200)
        with server(Large) as url, patch('qa_lab.http_acceptance.MAX_RESPONSE', 100):
            with self.assertRaisesRegex(AcceptanceFailure, 'response_size_limit'):
                request(url, '/')


class ProbeEvidenceTests(unittest.TestCase):
    def rows(self):
        return [('id-' + s, s, {'run_id': 'r'}, 'timestamp', s, {'run_id': 'r'})
                for s in ('qa.jobs.created', 'qa.jobs.finished')]

    def test_valid_event_pair(self):
        self.assertTrue(exact_events(self.rows()))

    def test_mismatched_inbox_is_rejected(self):
        rows = self.rows()
        rows[0] = (*rows[0][:5], {'run_id': 'other'})
        self.assertFalse(exact_events(rows))

    def test_missing_publish_or_duplicate_subject_is_rejected(self):
        rows = self.rows()
        self.assertFalse(exact_events([rows[0], rows[0]]))
        self.assertFalse(exact_events([(*rows[0][:3], None, *rows[0][4:]), rows[1]]))
        self.assertFalse(exact_events([]))

    def test_public_targets_credentials_and_paths_rejected(self):
        for url in ('https://example.com', 'http://u:p@qa-api.railway.internal',
                    'file:///tmp/a', 'http://localhost/?x=1', 'http://localhost/path',
                    'http://127.0.0.1:99999'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                private_origin(url)

    def test_private_origins_allowed(self):
        self.assertEqual(private_origin('http://qa-api.railway.internal:8765'),
                         ('http', 'qa-api.railway.internal', 8765))
        self.assertEqual(private_origin('http://[::1]:8080'), ('http', '::1', 8080))

    def test_unresolved_placeholder_secret_rejected(self):
        for token in ('short', '${{secret(48, "abc")}}', 'replace-' + 'a' * 30,
                      'a' * 30 + '\n', '\u0623' * 30):
            with self.subTest(token=token), self.assertRaises(ValueError):
                validate_token(token)

    def test_reference_and_timeout_validation(self):
        for source, timeout in [('main', 5), (SOURCE, 0), (SOURCE, float('inf'))]:
            with self.subTest(source=source, timeout=timeout), self.assertRaises(ValueError):
                Config('http://localhost', 'http://localhost', TOKEN, TOKEN, source, timeout)
