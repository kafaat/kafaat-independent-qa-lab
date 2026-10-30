"""Actual PostgreSQL, JetStream, consumer and dashboard; no synthetic DB rows."""
import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from qa_lab.rejection_live_probe import Client, run_probe, read_record

DSN = os.environ.get('QA_TEST_POSTGRES_DSN')
URL = os.environ.get('QA_NATS_URL')
LIVE = os.environ.get('QA_REJECTION_PROBE_LIVE') == '1'


@unittest.skipUnless(LIVE and DSN and URL, 'requires isolated PostgreSQL and JetStream')
class LiveRejectionProbeTests(unittest.TestCase):
    def test_actual_consumer_audit_and_http_review(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo
        schema = 'review_probe_' + uuid.uuid4().hex
        processes = []
        logs = []
        root = Path(__file__).resolve().parents[1]
        token = 'ci-local-only-' + uuid.uuid4().hex
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        with psycopg.connect(DSN) as db:
            db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        scoped = make_conninfo(DSN, options='-c search_path=' + schema)
        env = {**os.environ, 'QA_POSTGRES_DSN':scoped, 'QA_NATS_URL':URL,
               'QA_DASHBOARD_TOKEN':token, 'QA_BIND_HOST':'127.0.0.1',
               'PORT':str(port), 'PYTHONDONTWRITEBYTECODE':'1', 'PYTHONPATH':str(root)}
        try:
            with tempfile.TemporaryDirectory() as directory:
                try:
                    for module in ('qa_lab.nats_consumer', 'qa_lab.pg_dashboard'):
                        log = open(Path(directory) / (module + '.log'), 'wb')
                        logs.append(log)
                        processes.append(subprocess.Popen([sys.executable, '-m', module],
                            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT))
                    client = Client(f'http://127.0.0.1:{port}', token, allow_loopback=True)
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline:
                        self.assertTrue(all(p.poll() is None for p in processes),
                                        'consumer or dashboard exited during startup')
                        try:
                            if client.call('/api/rejections?limit=1')[0] == 200:
                                break
                        except ValueError:
                            pass
                        time.sleep(0.1)
                    else:
                        self.fail('review storage readiness timed out')
                    async def scenario():
                        import nats
                        nc = await nats.connect(URL, connect_timeout=5)
                        try:
                            from nats.js.errors import NotFoundError
                            js = nc.jetstream()
                            end = time.monotonic() + 15
                            while True:
                                try:
                                    await js.consumer_info('QA_JOBS', 'qa-audit-inbox')
                                    break
                                except NotFoundError:
                                    if time.monotonic() >= end:
                                        raise AssertionError('consumer not ready')
                                    await asyncio.sleep(0.1)
                        finally:
                            await nc.close()
                        return await run_probe(client, scoped, URL, timeout=20,
                            identity={'scope':'isolated-ci-not-Railway'})
                    result = asyncio.run(scenario())
                    self.assertEqual(result['status'], 'PASSED', result)
                    self.assertEqual(result['checks_passed'], 20)
                    self.assertEqual(len(result['checks']), 20)
                    self.assertEqual(result['synthetic_messages_published'], 1)
                    self.assertEqual(result['sql_write_operations'], 0)
                    self.assertEqual(result['audit_records_deleted'], 0)
                    row = read_record(scoped, result['rejection_id'])
                    self.assertEqual(row['reason_code'], 'invalid_json')
                    with psycopg.connect(scoped) as db:
                        self.assertEqual(db.execute('SELECT count(*) FROM qa_inbox').fetchone()[0], 0)
                        self.assertEqual(db.execute('SELECT count(*) FROM qa_nats_rejections').fetchone()[0], 1)
                    print('REJECTION_LIVE_ACCEPTANCE ' + __import__('json').dumps(result), flush=True)
                finally:
                    for process in processes:
                        if process.poll() is None:
                            process.terminate()
                        try:
                            process.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                    for log in logs:
                        log.close()
        finally:
            with psycopg.connect(DSN) as db:
                db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


if __name__ == '__main__':
    unittest.main()
