"""Real PostgreSQL queue integration tests. Run with QA_TEST_POSTGRES_DSN.

Requires a dedicated disposable database; uses unique keys and removes own rows.
Never treats missing PostgreSQL as a successful integration run.
"""
import hashlib
import json
import os
import uuid
import unittest
from concurrent.futures import ThreadPoolExecutor

from qa_lab.pg_queue import PostgresQueue

DSN = os.environ.get("QA_TEST_POSTGRES_DSN")

@unittest.skipUnless(DSN, "requires real QA_TEST_POSTGRES_DSN")
class PostgreSQLIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue = PostgresQueue(DSN)
        cls.queue.init()

    def setUp(self):
        self.key = "qa-integration-" + uuid.uuid4().hex
        self.request = {"idempotency_key": self.key, "project": "integration"}
        self.digest = hashlib.sha256(json.dumps(self.request,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def tearDown(self):
        with self.queue._connect() as db:
            db.execute("DELETE FROM qa_jobs WHERE idempotency_key=%s", (self.key,))

    def test_submit_and_reuse(self):
        first = self.queue.submit(self.request, self.digest)
        second = self.queue.submit(self.request, self.digest)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertTrue(second["reused"])

    def test_claim_finish_and_reconcile(self):
        first = self.queue.submit(self.request, self.digest)
        # Claim may encounter another pending job in a shared database.
        # Run only on a dedicated, otherwise idle integration database.
        claimed = self.queue.claim("integration-worker")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["run_id"], first["run_id"])
        self.assertEqual(self.queue.reconcile(first["run_id"])["action"], "manual-review")
        with self.assertRaises(ValueError):
            self.queue.finish(first["run_id"], "other-worker", "PASSED", {})
        self.queue.finish(first["run_id"], "integration-worker", "PASSED", {"status":"PASSED"})
        self.assertEqual(self.queue.get(first["run_id"])["status"], "PASSED")
        self.assertIsNone(self.queue.claim("integration-worker"))

    def test_two_workers_single_claim(self):
        first = self.queue.submit(self.request, self.digest)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.queue.claim, ("worker-a", "worker-b")))
        claimed = [x for x in results if x]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["run_id"], first["run_id"])
