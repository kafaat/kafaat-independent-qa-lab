"""Regressions for worker, receipt-verify, PostgreSQL-worker and auth hardening (2026-11-07)."""
from contextlib import closing
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qa_lab import worker
from qa_lab.core import Lab, WorkspaceUnavailable, bearer_matches
from qa_lab.pg_worker import execute_one

REQ = {"idempotency_key": "hardening", "project": "demo", "source_sha": "a" * 40,
       "recipe": "python-smoke"}


class StopLoop(Exception):
    pass


class WorkspaceTests(unittest.TestCase):
    def test_missing_workspace_raises_before_claim_and_run_stays_pending(self):
        with tempfile.TemporaryDirectory() as d:
            lab = Lab(d)
            rid = lab.submit(REQ)["run_id"]
            for workspace in (Path(d) / "missing", Path(d) / "file.txt"):
                (Path(d) / "file.txt").write_text("x", encoding="utf-8")
                with self.subTest(workspace=workspace.name):
                    with self.assertRaises(WorkspaceUnavailable):
                        lab.execute(rid, workspace)
                    self.assertEqual(lab.get(rid)["status"], "PENDING")
                    self.assertIsNone(lab.get(rid)["receipt"])

    def test_worker_does_not_burn_pending_runs_when_workspace_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            lab = Lab(d)
            rid = lab.submit(REQ)["run_id"]
            argv = ["worker", "--data-dir", d, "--workspace", str(Path(d) / "absent"),
                    "--interval", "0"]
            with patch.object(sys, "argv", argv), \
                    patch.object(worker.time, "sleep", side_effect=StopLoop), \
                    patch("builtins.print"):
                with self.assertRaises(StopLoop):
                    worker.main()
            run = Lab(d).get(rid)
            self.assertEqual(run["status"], "PENDING")
            self.assertIsNone(run["receipt"])

    def test_worker_still_rejects_a_genuinely_invalid_run(self):
        with tempfile.TemporaryDirectory() as d:
            lab = Lab(d)
            rid = lab.submit(REQ)["run_id"]
            argv = ["worker", "--data-dir", d, "--workspace", d, "--interval", "0"]
            with patch.object(sys, "argv", argv), \
                    patch.object(Lab, "execute", side_effect=ValueError("bad recipe")), \
                    patch.object(worker.time, "sleep", side_effect=StopLoop), \
                    patch("builtins.print"):
                with self.assertRaises(StopLoop):
                    worker.main()
            self.assertEqual(Lab(d).get(rid)["status"], "BLOCKED")


class VerifyTests(unittest.TestCase):
    def tamper(self, lab, rid, receipt):
        with closing(lab.connect()) as db, db:
            db.execute("UPDATE runs SET receipt=? WHERE id=?", (json.dumps(receipt), rid))

    def test_malformed_receipts_verify_false_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as d:
            lab = Lab(d)
            rid = lab.submit(REQ)["run_id"]
            with patch.dict(os.environ, {"QA_REQUIRE_GIT_SHA": "0"}):
                good = lab.execute(rid, Path(d))
            self.assertTrue(lab.verify(rid))
            missing = {k: v for k, v in good.items() if k != "sha256"}
            for receipt in (missing, {**good, "sha256": 7}, {**good, "status": "FAILED"},
                            ["not", "a", "dict"]):
                with self.subTest(receipt=str(receipt)[:40]):
                    self.tamper(lab, rid, receipt)
                    self.assertFalse(lab.verify(rid))


class BearerTests(unittest.TestCase):
    def test_bearer_matching(self):
        self.assertTrue(bearer_matches(["Bearer s3cret"], "s3cret"))
        self.assertFalse(bearer_matches(["Bearer wrong"], "s3cret"))
        self.assertFalse(bearer_matches(["Bearer s3cret", "Bearer s3cret"], "s3cret"))
        self.assertFalse(bearer_matches([], "s3cret"))
        self.assertFalse(bearer_matches(["Bearer "], ""))
        self.assertFalse(bearer_matches(["Bearer s3crét"], "s3cret"))
        self.assertFalse(bearer_matches(["Bearer \udcff"], "s3cret"))


class FakeQueue:
    def __init__(self, request):
        self.request = request

    def claim(self, worker_id):
        return {"run_id": "r", "request": self.request}

    def finish(self, rid, worker_id, status, receipt):
        return {"status": status, "receipt": receipt}


class PgWorkerTests(unittest.TestCase):
    def test_runner_oserror_closes_the_claim_as_blocked(self):
        with patch("qa_lab.pg_worker.subprocess.run", side_effect=OSError(11, "EAGAIN")):
            result = execute_one(FakeQueue({"recipe": "python-smoke", "source_sha": "a" * 40}), "w")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(result["receipt"]["reason"].startswith("runner unavailable: "))
        self.assertEqual(result["receipt"]["authority"], "execution-evidence-only")

    def test_audio_runner_exception_closes_the_claim_as_blocked(self):
        with patch("qa_lab.audio_jobs.enabled", return_value=True), \
                patch("qa_lab.audio_jobs.execute", side_effect=RuntimeError("decoder crashed")):
            result = execute_one(FakeQueue({"recipe": "audio-pcm-v1"}), "w")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("RuntimeError", result["receipt"]["reason"])


if __name__ == "__main__":
    unittest.main()
