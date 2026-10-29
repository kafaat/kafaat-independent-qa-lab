import gc
import sqlite3
import tempfile
import unittest
import warnings
from contextlib import closing
from qa_lab.core import Lab

class SQLiteConnectionLifecycleTests(unittest.TestCase):
    def test_transaction_context_alone_does_not_close_connection(self):
        with tempfile.TemporaryDirectory() as d:
            lab = Lab(d)
            with closing(lab.connect()) as db, db:
                db.execute("SELECT 1")
            self.assertRaises(sqlite3.ProgrammingError, db.execute, "SELECT 1")

    def test_core_operations_release_connections(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            with tempfile.TemporaryDirectory() as d:
                lab = Lab(d)
                req = {"idempotency_key": "lifecycle", "project": "demo", "source_sha": "a"*40, "recipe": "python-smoke"}
                rid = lab.submit(req)["run_id"]
                self.assertEqual(lab.get(rid)["status"], "PENDING")
                self.assertEqual(lab.reconcile(rid)["action"], "none")
                gc.collect()
