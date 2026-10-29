from contextlib import closing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from qa_lab.core import Lab

REQ = {"idempotency_key":"unique", "project":"demo", "source_sha":"a"*40, "recipe":"python-smoke"}

class ExecutionHardeningTests(unittest.TestCase):
    def test_claimed_run_cannot_be_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            lab=Lab(d); rid=lab.submit(REQ)["run_id"]
            with closing(lab.connect()) as db, db:
                db.execute("UPDATE runs SET status='RUNNING' WHERE id=?",(rid,))
            with self.assertRaisesRegex(ValueError, "manual reconciliation"):
                lab.reject(rid,"another worker failed")
            self.assertEqual(lab.get(rid)["status"],"RUNNING")
    def test_receipt_digest_and_no_release_authority(self):
        with tempfile.TemporaryDirectory() as d:
            lab=Lab(d); rid=lab.submit(REQ)["run_id"]
            with patch.dict(os.environ, {"QA_REQUIRE_GIT_SHA":"0"}):
                receipt=lab.execute(rid,Path(d))
            self.assertTrue(lab.verify(rid))
            self.assertEqual(receipt["authority"],"execution-evidence-only")
            self.assertNotIn("merge_approved",receipt)
            self.assertNotIn("case_closed",receipt)
    def test_duplicate_execution_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            lab=Lab(d); rid=lab.submit(REQ)["run_id"]
            with patch.dict(os.environ, {"QA_REQUIRE_GIT_SHA":"0"}):
                lab.execute(rid,Path(d))
                with self.assertRaises(ValueError): lab.execute(rid,Path(d))
    def test_git_attestation_failure_requires_manual_reconcile(self):
        with tempfile.TemporaryDirectory() as d:
            lab=Lab(d); rid=lab.submit(REQ)["run_id"]
            with patch.dict(os.environ,{"QA_REQUIRE_GIT_SHA":"1"}):
                receipt=lab.execute(rid,Path(d))
            self.assertEqual(receipt["status"], "BLOCKED")
            self.assertTrue(lab.verify(rid))
            self.assertEqual(lab.reconcile(rid)["action"],"none")
