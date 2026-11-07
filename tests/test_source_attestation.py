"""Receipts say whether source_sha was compared with the checkout (source_sha_verified)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qa_lab.core import Lab


def submit(lab, sha):
    return lab.submit({"idempotency_key": sha[:8], "project": "demo", "source_sha": sha,
                       "recipe": "python-smoke"})["run_id"]


class SourceAttestationTests(unittest.TestCase):
    def test_unchecked_source_is_marked_unverified(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"QA_REQUIRE_GIT_SHA": "0"}):
            lab = Lab(d)
            rid = submit(lab, "a" * 40)
            receipt = lab.execute(rid, Path(d))
            self.assertTrue(lab.verify(rid))
        self.assertEqual(receipt["status"], "PASSED")
        self.assertIs(receipt["source_sha_verified"], False)

    def test_matching_clean_checkout_is_marked_verified(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as ws:
            git = ["git", "-C", ws, "-c", "user.email=qa@example.invalid", "-c", "user.name=qa"]
            subprocess.run(["git", "init", "-q", ws], check=True)
            Path(ws, "f.txt").write_text("x", encoding="utf-8")
            subprocess.run(git + ["add", "f.txt"], check=True)
            subprocess.run(git + ["commit", "-q", "-m", "init"], check=True)
            sha = subprocess.run(["git", "-C", ws, "rev-parse", "HEAD"], check=True,
                                 capture_output=True, text=True).stdout.strip()
            lab = Lab(data)
            with patch.dict(os.environ, {"QA_REQUIRE_GIT_SHA": "1"}):
                receipt = lab.execute(submit(lab, sha), Path(ws))
                wrong = lab.execute(submit(lab, "b" * 40), Path(ws))
        self.assertIs(receipt["source_sha_verified"], True)
        self.assertEqual(wrong["status"], "BLOCKED")
        self.assertIs(wrong["source_sha_verified"], False)


if __name__ == "__main__":
    unittest.main()
