"""Real PostgreSQL + JetStream + five separate Python service processes.

Runs only on the disposable CI environment selected by QA_HTTP_ACCEPTANCE_LIVE.
No static source assertions or fake database callbacks are used in this test.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from qa_lab.http_acceptance import request


@unittest.skipUnless(os.environ.get('QA_HTTP_ACCEPTANCE_LIVE') == '1', 'requires disposable live HTTP stack')
class LiveHTTPAcceptanceTests(unittest.TestCase):
    def test_actual_api_dashboard_worker_and_events(self):
        import psycopg
        from qa_lab.pg_queue import PostgresQueue
        from qa_lab.nats_inbox import INBOX_SCHEMA
        from qa_lab.nats_outbox import OUTBOX_SCHEMA

        dsn = os.environ['QA_TEST_POSTGRES_DSN']
        nats_url = os.environ['QA_NATS_URL']
        PostgresQueue(dsn).init()
        with psycopg.connect(dsn) as db:
            db.execute(INBOX_SCHEMA)
            db.execute(OUTBOX_SCHEMA)
        subprocess.run([sys.executable, '-m', 'qa_lab.nats_init'], check=True,
                       env={**os.environ, 'QA_NATS_URL': nats_url}, timeout=20)
        listeners = []
        try:
            for _ in range(2):
                listener = socket.socket()
                listener.bind(('127.0.0.1', 0))
                listeners.append(listener)
            api_port, dash_port = [s.getsockname()[1] for s in listeners]
        finally:
            for listener in listeners:
                listener.close()
        env = {**os.environ, 'QA_POSTGRES_DSN': dsn, 'QA_BIND_HOST': '127.0.0.1',
               'PYTHONUNBUFFERED': '1', 'QA_API_TOKEN': 'isolated-ci-api-token-' + 'a' * 32,
               'QA_DASHBOARD_TOKEN': 'isolated-ci-dashboard-token-' + 'b' * 32,
               'QA_SOURCE_SHA': os.environ.get('GITHUB_SHA', 'a' * 40),
               'QA_API_URL': f'http://127.0.0.1:{api_port}',
               'QA_DASHBOARD_URL': f'http://127.0.0.1:{dash_port}'}
        processes = []
        files = []
        with tempfile.TemporaryDirectory() as directory:
            try:
                for module, port in [('pg_api', api_port), ('pg_dashboard', dash_port),
                                     ('pg_worker', 0), ('nats_relay', 0), ('nats_consumer', 0)]:
                    log = open(Path(directory) / (module + '.log'), 'w+')
                    files.append(log)
                    child = subprocess.Popen([sys.executable, '-m', 'qa_lab.' + module],
                                             env={**env, 'PORT': str(port)}, stdout=log, stderr=log)
                    processes.append(child)
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    try:
                        if (request(env['QA_API_URL'], '/healthz')[0] == 200
                                and request(env['QA_DASHBOARD_URL'], '/')[0] == 401):
                            break
                    except OSError:
                        time.sleep(0.2)
                else:
                    self.fail('HTTP services did not become ready')
                probe = subprocess.run([sys.executable, '-m', 'qa_lab.http_acceptance'],
                                       env=env, capture_output=True, text=True, timeout=200)
                self.assertEqual(probe.returncode, 0, probe.stdout + probe.stderr)
                result = json.loads(probe.stdout.strip().splitlines()[-1])
                self.assertEqual(result['status'], 'PASSED')
                self.assertEqual(len(result['checks']), 23)
                self.assertTrue(all(item['passed'] for item in result['checks']))
                self.assertEqual(len(result['event_ids']), 2)
                with psycopg.connect(dsn) as db:
                    stored = db.execute('SELECT result FROM qa_acceptance_receipts WHERE probe_id=%s',
                                        (result['probe_id'],)).fetchone()[0]
                self.assertEqual(stored, result)
                print(json.dumps({'http_acceptance': result}), flush=True)
            finally:
                for child in processes:
                    if child.poll() is None:
                        child.terminate()
                for child in processes:
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
                for log in files:
                    log.flush()
                    log.seek(0)
                    content = log.read()
                    if 'Traceback' in content:
                        print(content[-6000:])
                    log.close()
