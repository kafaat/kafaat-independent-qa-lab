"""Real SQLite and HTTP regressions; Docker acceptance is a separate live job."""
from contextlib import closing
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from qa_lab.core import Lab, canonical, digest
from qa_lab.reader import ReadOnlyLab
from qa_lab import evidence_service, triage_service
from http.server import ThreadingHTTPServer

TOKEN = "test-only-not-production-credential"


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data?# بيانات"
        self.writer = Lab(self.root)
        self.req = dict(idempotency_key="one", project="synthetic", source_sha="a" * 40,
                        recipe="python-smoke")
        self.rid = self.writer.submit(self.req)["run_id"]
        self.reader = ReadOnlyLab(self.root)

    def test_read_matches_writer_and_pending_is_unverified(self):
        self.assertEqual(self.reader.get(self.rid), self.writer.get(self.rid))
        self.assertFalse(self.reader.verify(self.rid))

    def test_missing_directory_is_not_created(self):
        missing = self.root / "missing"
        with self.assertRaises(FileNotFoundError):
            ReadOnlyLab(missing)
        self.assertFalse(missing.exists())

    def test_missing_database_is_not_created(self):
        empty = self.root / "empty"; empty.mkdir()
        with self.assertRaises(FileNotFoundError):
            ReadOnlyLab(empty)
        self.assertEqual(list(empty.iterdir()), [])

    def test_missing_schema_is_not_initialized(self):
        empty = self.root / "empty"; empty.mkdir()
        with closing(sqlite3.connect(empty / "lab.sqlite3")):
            pass
        with self.assertRaises(sqlite3.DatabaseError):
            ReadOnlyLab(empty)
        with closing(sqlite3.connect(empty / "lab.sqlite3")) as db:
            self.assertEqual(db.execute('SELECT name FROM sqlite_master').fetchall(), [])

    def test_symlink_database_is_rejected(self):
        other = self.root / "other"; other.mkdir()
        (other / "lab.sqlite3").symlink_to(self.writer.db)
        with self.assertRaises(FileNotFoundError):
            ReadOnlyLab(other)

    def test_constructor_and_reads_never_mkdir_or_chmod(self):
        with patch('os.chmod', side_effect=OSError(errno.EROFS, 'read-only')):
            with patch.object(Path, 'mkdir', side_effect=AssertionError('mkdir forbidden')):
                reader = ReadOnlyLab(self.root)
                reader.ready()
                self.assertEqual(reader.get(self.rid)['status'], 'PENDING')

    def test_original_writer_initializer_fails_on_readonly_permissions(self):
        with patch('os.chmod', side_effect=OSError(errno.EROFS, 'read-only')):
            with self.assertRaises(OSError):
                Lab(self.root)

    def test_reading_preserves_database_bytes_and_modes(self):
        before = (hashlib.sha256(self.writer.db.read_bytes()).hexdigest(),
                  self.root.stat().st_mode, self.writer.db.stat().st_mode,
                  sorted(p.name for p in self.root.iterdir()))
        for _ in range(3):
            self.reader.ready(); self.reader.get(self.rid); self.reader.verify(self.rid)
        after = (hashlib.sha256(self.writer.db.read_bytes()).hexdigest(),
                 self.root.stat().st_mode, self.writer.db.stat().st_mode,
                 sorted(p.name for p in self.root.iterdir()))
        self.assertEqual(before, after)

    def test_connection_rejects_dml_and_ddl_even_without_query_only(self):
        with closing(self.reader.connect()) as db:
            db.execute('PRAGMA query_only=OFF')
            for sql in ("UPDATE runs SET status='FAILED'", "DELETE FROM runs",
                        "CREATE TABLE forbidden(x)", "DROP TABLE runs"):
                with self.subTest(sql=sql), self.assertRaises(sqlite3.OperationalError):
                    db.execute(sql)
        self.assertEqual(self.writer.get(self.rid)['status'], 'PENDING')

    def test_reader_has_no_execution_or_mutation_methods(self):
        for name in ('submit', 'execute', 'reject', '_finish_blocked_claim'):
            self.assertFalse(hasattr(self.reader, name))

    def test_reader_sees_later_commits_not_an_immutable_snapshot(self):
        self.assertEqual(self.reader.get(self.rid)['status'], 'PENDING')
        receipt = self.writer.reject(self.rid, 'fixture rejection')
        self.assertEqual(self.reader.get(self.rid)['receipt'], receipt)
        self.assertTrue(self.reader.verify(self.rid))

    def test_real_smoke_receipt_and_digest_tampering(self):
        with patch.dict(os.environ, {'QA_REQUIRE_GIT_SHA': '0'}):
            result = self.writer.execute(self.rid, self.tmp.name)
        self.assertEqual(result['status'], 'PASSED')
        self.assertIn('qa-smoke-ok', result['stdout_tail'])
        self.assertTrue(self.reader.verify(self.rid))
        result['status'] = 'FAILED'
        with closing(self.writer.connect()) as db, db:
            db.execute('UPDATE runs SET receipt=? WHERE id=?', (canonical(result), self.rid))
        self.assertFalse(self.reader.verify(self.rid))

    def test_missing_digest_is_not_success(self):
        with closing(self.writer.connect()) as db, db:
            db.execute('UPDATE runs SET receipt=? WHERE id=?', ('{"status":"PASSED"}', self.rid))
        self.assertFalse(self.reader.verify(self.rid))

    def test_unknown_run_and_sql_injection_are_not_found(self):
        for rid in ('missing', "' OR 1=1 --"):
            with self.assertRaises(KeyError):
                self.reader.get(rid)

    def test_deleted_database_is_not_recreated(self):
        self.writer.db.unlink()
        with self.assertRaises(sqlite3.OperationalError):
            self.reader.ready()
        self.assertFalse(self.writer.db.exists())

    def test_service_bootstrap_uses_reader_on_readonly_store(self):
        for module in (evidence_service, triage_service):
            with self.subTest(service=module.__name__):
                with patch.dict(os.environ, {'QA_API_TOKEN': TOKEN, 'QA_DATA_DIR': str(self.root)}):
                    with patch('os.chmod', side_effect=OSError(errno.EROFS, 'read-only')):
                        with patch.object(module, 'ThreadingHTTPServer') as server:
                            module.serve()
                            handler = server.call_args.args[1]
                            self.assertIsInstance(handler.lab, ReadOnlyLab)
                            self.assertEqual(handler.lab.get(self.rid)['status'], 'PENDING')


class ServiceHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.writer = Lab(Path(self.tmp.name) / 'data')
        self.rid = self.writer.submit(dict(idempotency_key='http', project='fixture',
                                          source_sha='b' * 40, recipe='python-smoke'))['run_id']
        self.writer.reject(self.rid, 'fixture only')
        self.env = patch.dict(os.environ, {'QA_API_TOKEN': TOKEN}); self.env.start()
        self.addCleanup(self.env.stop)
        self.servers = []
        for handler in (evidence_service.EvidenceHandler, triage_service.TriageHandler):
            bound = type('TestHandler', (handler,), {'lab': ReadOnlyLab(self.writer.root),
                                                    'log_message': lambda *args: None})
            server = ThreadingHTTPServer(('127.0.0.1', 0), bound)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            self.servers.append((server, thread))
        self.addCleanup(self.stop_servers)

    def stop_servers(self):
        for server, thread in self.servers:
            server.shutdown(); server.server_close(); thread.join(5)
            self.assertFalse(thread.is_alive())

    def request(self, index, path, token=TOKEN, method='GET'):
        conn = http.client.HTTPConnection('127.0.0.1', self.servers[index][0].server_port, timeout=3)
        try:
            conn.request(method, path, headers={'Authorization': 'Bearer ' + token} if token else {})
            response = conn.getresponse(); body = response.read()
            return response.status, body, dict(response.getheaders())
        finally:
            conn.close()

    def test_health_checks_real_store(self):
        for i in range(2):
            self.assertEqual(self.request(i, '/healthz', token=None)[0], 200)
        self.writer.db.unlink()
        for i in range(2):
            code, body, _ = self.request(i, '/healthz', token=None)
            self.assertEqual(code, 503); self.assertNotIn(self.tmp.name.encode(), body)
        self.assertFalse(self.writer.db.exists())

    def test_authenticated_evidence_and_advisory_no_write(self):
        before = hashlib.sha256(self.writer.db.read_bytes()).hexdigest()
        code, body, headers = self.request(0, '/v1/evidence/' + self.rid)
        self.assertEqual(code, 200); self.assertTrue(json.loads(body)['integrity_verified'])
        self.assertFalse(json.loads(body)['host_attested'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        code, body, _ = self.request(1, '/v1/triage/' + self.rid)
        self.assertEqual(code, 200); self.assertTrue(json.loads(body)['advisory_only'])
        self.assertEqual(before, hashlib.sha256(self.writer.db.read_bytes()).hexdigest())

    def test_missing_and_invalid_auth_rejected(self):
        for i, kind in enumerate(('evidence', 'triage')):
            for token in ('incorrect', None):
                with self.subTest(i=i, token=token):
                    self.assertEqual(self.request(i, '/v1/' + kind + '/' + self.rid, token)[0], 401)

    def test_unknown_run_rejected(self):
        for i, kind in enumerate(('evidence', 'triage')):
            self.assertEqual(self.request(i, '/v1/' + kind + '/missing')[0], 404)

    def test_corrupt_storage_is_sanitized(self):
        with closing(self.writer.connect()) as db, db:
            db.execute('UPDATE runs SET receipt=?', ('private-invalid-json',))
        for i, kind in enumerate(('evidence', 'triage')):
            code, body, _ = self.request(i, '/v1/' + kind + '/' + self.rid)
            self.assertEqual(code, 503); self.assertNotIn(b'private', body)


if __name__ == '__main__':
    unittest.main()
