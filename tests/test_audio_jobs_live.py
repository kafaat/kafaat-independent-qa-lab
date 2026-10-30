"""Real HTTP -> PostgreSQL -> worker -> Outbox -> JetStream -> Inbox acceptance.

Use an isolated PostgreSQL schema and dedicated disposable NATS server in CI.
No legacy source is executed; this proves transport/integrity, not full parity.
"""
import asyncio
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
import uuid
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from audio_fixtures import fixture
from qa_lab import audio_jobs as jobs
from qa_lab.pg_api import PostgreSQLHandler
from qa_lab.pg_queue import PostgresQueue
from test_audio_jobs import TOKEN, request_for

DSN = os.environ.get('QA_TEST_POSTGRES_DSN')
NATS = os.environ.get('QA_NATS_URL')
LIVE = os.environ.get('QA_AUDIO_LIVE') == '1'


@unittest.skipUnless(LIVE and DSN and NATS, 'requires isolated PostgreSQL and NATS')
class AudioLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        cls.schema = 'audio_live_' + uuid.uuid4().hex
        with psycopg.connect(DSN) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(cls.schema)))
        def drop():
            with psycopg.connect(DSN) as db:
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(cls.schema)))
        cls.addClassCleanup(drop)
        cls.dsn = make_conninfo(DSN, options='-c search_path=' + cls.schema,
                               connect_timeout=5)
        cls.queue = PostgresQueue(cls.dsn)
        cls.queue.init()
        from qa_lab.nats_outbox import OUTBOX_SCHEMA
        with cls.queue._connect() as db:
            db.execute(OUTBOX_SCHEMA)
        root = Path(__file__).resolve().parents[1]
        env = {**os.environ, 'QA_POSTGRES_DSN': cls.dsn, 'QA_ENABLE_AUDIO_PCM': '1',
               'QA_NATS_URL': NATS, 'PYTHONPATH': str(root)}
        cls.processes = []
        def stop():
            for proc in cls.processes:
                if proc.poll() is None:
                    proc.terminate()
            for proc in cls.processes:
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        cls.addClassCleanup(stop)
        for module in ('nats_consumer', 'nats_relay', 'pg_worker'):
            cls.processes.append(subprocess.Popen(
                [sys.executable, '-m', 'qa_lab.' + module], cwd=root, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            if module == 'nats_consumer':
                async def ready():
                    import nats
                    nc = await nats.connect(NATS, connect_timeout=5)
                    try:
                        deadline = time.monotonic() + 20
                        while time.monotonic() < deadline:
                            try:
                                await nc.jetstream().consumer_info('QA_JOBS', 'qa-audit-inbox')
                                return
                            except Exception:
                                await asyncio.sleep(0.1)
                        raise AssertionError('consumer did not initialize dedicated stream')
                    finally:
                        await nc.close()
                asyncio.run(ready())
        cls.env = patch.dict(os.environ, {'QA_ENABLE_AUDIO_PCM': '1', 'QA_API_TOKEN': TOKEN})
        cls.env.start()
        cls.addClassCleanup(cls.env.stop)
        handler = type('LiveAudioAPI', (PostgreSQLHandler,), {'queue': cls.queue})
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        def close_http():
            cls.server.shutdown()
            cls.thread.join(5)
            cls.server.server_close()
        cls.addClassCleanup(close_http)

    def call(self, payload=None, *, path='/v1/audio-runs', token=TOKEN, method='POST'):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['Authorization'] = 'Bearer ' + token
            body = jobs.canonical(payload).encode() if payload is not None else None
            conn.request(method, path, body, headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def wait_terminal(self, rid):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            self.assertTrue(all(p.poll() is None for p in self.processes), 'owned process stopped')
            status, item = self.call(path='/v1/runs/' + rid, method='GET')
            self.assertEqual(status, 200)
            if item['status'] in ('PASSED', 'FAILED', 'BLOCKED', 'TIMED_OUT'):
                return item
            time.sleep(0.1)
        self.fail('audio job did not reach terminal state')

    def wait_events(self, rid, expected_status):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            with self.queue._connect() as db:
                rows = db.execute("SELECT o.id,o.subject,o.payload,o.published_at,i.subject,i.payload "
                                  "FROM qa_outbox o LEFT JOIN qa_inbox i ON i.event_id=o.id "
                                  "WHERE o.payload->>'run_id'=%s", (rid,)).fetchall()
            if len(rows) == 2 and all(r[3] and r[1] == r[4] and r[2] == r[5] for r in rows):
                self.assertEqual(sorted(r[1] for r in rows), ['qa.jobs.created', 'qa.jobs.finished'])
                finished = next(r[2] for r in rows if r[1] == 'qa.jobs.finished')
                self.assertEqual(finished['status'], expected_status)
                return rows
            time.sleep(0.1)
        self.fail('exact two audio outbox/inbox events missing')

    def test_audio_pipeline_success_failure_and_idempotency(self):
        for silent, wanted in ((False, 'PASSED'), (True, 'FAILED')):
            with self.subTest(silent=silent):
                req = request_for(fixture(frames=128, silent=silent))
                status, submitted = self.call(req)
                self.assertEqual(status, 202)
                rid = submitted['run_id']
                self.assertEqual(self.call(req), (202, {'run_id': rid, 'reused': True}))
                changed = request_for(fixture(amplitude=24), idempotency_key=req['idempotency_key'])
                self.assertEqual(self.call(changed)[0], 409)
                item = self.wait_terminal(rid)
                self.assertEqual(item['status'], wanted)
                receipt = item['receipt']
                self.assertEqual(receipt['run_id'], rid)
                self.assertEqual(receipt['input_sha256'], req['audio_sha256'])
                self.assertEqual(receipt['request_sha256'], jobs.digest(req))
                self.assertEqual(receipt['parser_sha256'], jobs.PARSER_SHA256)
                self.assertEqual(receipt['receipt_sha256'], jobs.digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'}))
                self.assertNotIn('audio_base64', receipt)
                if wanted == 'PASSED':
                    self.assertEqual(receipt['audio_metrics']['frames'], 128)
                else:
                    self.assertEqual(receipt['reason'], 'silent_pcm')
                rows = self.wait_events(rid, wanted)
                for r in rows:
                    self.assertNotIn(req['audio_base64'], jobs.canonical(r[2]))

    def test_auth_gate_and_bad_digest_do_not_queue(self):
        with self.queue._connect() as db:
            before = db.execute('SELECT count(*) FROM qa_jobs').fetchone()[0]
        self.assertEqual(self.call(request_for(), token=None)[0], 401)
        with patch.dict(os.environ, {'QA_ENABLE_AUDIO_PCM': '0'}):
            self.assertEqual(self.call(request_for())[0], 404)
        self.assertEqual(self.call(request_for(audio_sha256='0' * 64))[0], 400)
        with self.queue._connect() as db:
            after = db.execute('SELECT count(*) FROM qa_jobs').fetchone()[0]
        self.assertEqual(before, after)

    def test_worker_rechecks_a_directly_inserted_bad_input(self):
        req = request_for(audio_sha256='0' * 64)
        rid = self.queue.submit(req, jobs.digest(req))['run_id']
        item = self.wait_terminal(rid)
        self.assertEqual(item['status'], 'BLOCKED')
        self.assertEqual(item['receipt']['reason'], 'audio_digest_mismatch')
        self.wait_events(rid, 'BLOCKED')
