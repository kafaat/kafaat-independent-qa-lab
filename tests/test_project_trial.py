"""Offline regression probes for reviewed-source experiment, no Docker daemon required."""
import copy
import hashlib
import hmac
import os
import sys
from unittest.mock import patch
from pathlib import Path
import subprocess
import tempfile
import unittest

from qa_lab.project_trial import (TrialRejected, capture, canonical, docker_argv,
                                  execute, validate_binding, verify, _read_tracked_nofollow)

IMAGE = "python:3.12-slim-bookworm@sha256:" + "9" * 64


def binding(commit):
    return {"tenant_id": "demo-tenant", "project_id": "demo-project",
            "case_id": "demo-case", "cycle_no": 1, "snapshot_id": "snapshot-a",
            "attempt_no": 1, "criteria_digest": "f" * 64, "source_sha": commit}


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.DEVNULL).decode().strip()


class SourceTrials(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.invalid")
        git(self.root, "config", "user.name", "Fixture")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_example.py").write_text(
            "import unittest\nclass Sample(unittest.TestCase):\n"
            "    def test_a(self): self.assertEqual(2 + 2, 4)\n"
            "    def test_b(self): self.assertTrue(True)\n")
        git(self.root, "add", "tests/test_example.py")
        git(self.root, "commit", "-qm", "fixed reviewed fixture")
        self.commit = git(self.root, "rev-parse", "HEAD")
        self.b = binding(self.commit)
        self.manifest, self.entries = capture(self.root, self.commit)
        self.key = b"q" * 32

    def signed(self):
        payload = {"format": "qa.reviewed-project-trial.v1", "binding": self.b,
                   "source_manifest": self.manifest, "sandbox": "docker-no-network-read-only-capdrop",
                   "image": IMAGE, "recipe": "python-unittest-discover-v1",
                   "result": "PASSED", "tests_observed": 2, "exit_code": 0,
                   "skipped_detected": False, "output_sha256": "a" * 64,
                   "source_verified_by_runner": True,
                   "source_attested_independently": False,
                   "production_accepted": False, "authority": "execution-evidence-only"}
        return {"payload": payload, "signature": {
            "algorithm": "hmac-sha256", "value": hmac.new(self.key, canonical(payload), hashlib.sha256).hexdigest()}}

    def checked(self, receipt, **kwargs):
        return verify(receipt, self.b, self.key,
                      expected_manifest_sha256=self.manifest["manifest_sha256"],
                      expected_image=IMAGE, minimum_tests=2, **kwargs)

    def test_capture_is_bound_to_real_git_commit_and_bytes(self):
        self.assertEqual(self.manifest["source_sha"], self.commit)
        self.assertEqual(self.manifest["file_count"], 1)
        self.assertEqual(self.manifest["git_tree_sha"], git(self.root, "rev-parse", "HEAD^{tree}"))
        self.assertEqual(self.manifest["files"][0]["sha256"],
                         hashlib.sha256(self.entries[0][1]).hexdigest())

    def test_dirty_tracked_rejected(self):
        (self.root / "tests" / "test_example.py").write_text("changed")
        with self.assertRaisesRegex(TrialRejected, "SOURCE_DIRTY"):
            capture(self.root, self.commit)

    def test_assume_unchanged_cannot_hide_changed_git_blob(self):
        path = self.root / "tests" / "test_example.py"
        git(self.root, "update-index", "--assume-unchanged", "tests/test_example.py")
        raw = path.read_bytes()
        self.assertIn(b"2 + 2", raw)
        path.write_bytes(raw.replace(b"2 + 2", b"3 + 3"))
        self.assertEqual(git(self.root, "status", "--porcelain=v1"), "")
        with self.assertRaisesRegex(TrialRejected, "TRACKED_BLOB_MISMATCH"):
            capture(self.root, self.commit)

    def test_symlinked_ancestor_refused_by_descriptor_walk(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("private")
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(TrialRejected, "UNSAFE_TRACKED_ENTRY"):
            _read_tracked_nofollow(self.root, "link/secret.txt", 20)

    def test_large_tracked_file_is_rejected_before_read(self):
        with self.assertRaisesRegex(TrialRejected, "SOURCE_LIMIT"):
            _read_tracked_nofollow(self.root, "tests/test_example.py", 1)

    def test_untracked_file_rejected(self):
        (self.root / "tests" / "evil.py").write_text("print('evil')")
        with self.assertRaisesRegex(TrialRejected, "SOURCE_DIRTY"):
            capture(self.root, self.commit)

    def test_wrong_commit_rejected(self):
        with self.assertRaisesRegex(TrialRejected, "SOURCE_COMMIT_MISMATCH"):
            capture(self.root, "a" * 40)

    def test_symlink_rejected(self):
        os.symlink("test_example.py", self.root / "tests" / "alias.py")
        git(self.root, "add", "tests/alias.py")
        git(self.root, "commit", "-qm", "symlink tracked")
        with self.assertRaisesRegex(TrialRejected, "UNSAFE_TRACKED_ENTRY"):
            capture(self.root, git(self.root, "rev-parse", "HEAD"))

    def test_submodule_entry_rejected(self):
        git(self.root, "update-index", "--add", "--cacheinfo", "160000," + self.commit + ",module")
        git(self.root, "commit", "-qm", "gitlink")
        with self.assertRaisesRegex(TrialRejected, "SOURCE_DIRTY|UNSAFE_TRACKED_ENTRY"):
            capture(self.root, git(self.root, "rev-parse", "HEAD"))

    def test_bool_attempt_not_integer(self):
        bad = {**self.b, "attempt_no": True}
        with self.assertRaisesRegex(TrialRejected, "INVALID_BINDING"):
            validate_binding(bad)

    def test_schema_rejects_extra_field_and_bad_digest(self):
        for bad in ({**self.b, "grant": "all"}, {**self.b, "criteria_digest": "x" * 64}):
            with self.assertRaises(TrialRejected):
                validate_binding(bad)

    def test_docker_contract_is_os_restricted(self):
        argv = docker_argv(IMAGE, self.root, "qa-trial-" + "a" * 20)
        for flag in ("--network=none", "--read-only", "--cap-drop=ALL",
                     "--security-opt=no-new-privileges", "--pull=never",
                     "--pids-limit=64", "--memory=256m", "--cpus=1"):
            self.assertIn(flag, argv)
        self.assertIn("type=bind,source=" + str(self.root) + ",target=/src,readonly", argv)
        self.assertEqual(argv[-8:], ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"])

    def test_unpinned_image_rejected(self):
        with self.assertRaisesRegex(TrialRejected, "IMAGE_DIGEST_REQUIRED"):
            docker_argv("python:latest", self.root, "qa-trial-" + "a" * 20)

    def test_signed_review_result_never_grants_hospital_authority(self):
        result = self.checked(self.signed())
        self.assertEqual(result["status"], "MATCHED")
        self.assertFalse(result["hospital_admission"])
        self.assertFalse(result["production_accepted"])
        self.assertFalse(result["source_independently_attested"])

    def test_trusted_fixture_launcher_does_not_shadow_signing_key(self):
        # This is a subprocess-contract unit test, not a Docker isolation claim.
        fake = [sys.executable, "-c", "import sys; sys.stderr.write('Ran 2 tests in 0.001s\\n\\nOK\\n')"]
        with patch("qa_lab.project_trial.docker_argv", return_value=fake):
            receipt = execute(self.root, self.b, IMAGE, self.key)
        self.assertEqual(receipt["payload"]["result"], "PASSED")
        self.assertEqual(self.checked(receipt)["status"], "MATCHED")

    def test_signed_privileged_or_extra_fields_rejected(self):
        for name, value in (("close_case", True), ("grant", "owner"), ("cmd", "sh")):
            with self.subTest(name=name):
                receipt = self.signed()
                receipt["payload"][name] = value
                receipt["signature"]["value"] = hmac.new(
                    self.key, canonical(receipt["payload"]), hashlib.sha256).hexdigest()
                self.assertIn("UNEXPECTED_RECEIPT_FIELDS", self.checked(receipt)["reasons"])

    def test_unittest_fabricated_duplicate_summary_rejected(self):
        # A malicious test module could print a fabricated summary before
        # the actual unittest summary. Neither may be trusted as a count.
        fake = [sys.executable, "-c", "import sys; sys.stderr.write("
                "'Ran 10 tests in 0.001s\\n\\nOK\\n' + "
                "'Ran 2 tests in 0.001s\\n\\nOK\\n')"]
        with patch("qa_lab.project_trial.docker_argv", return_value=fake):
            receipt = execute(self.root, self.b, IMAGE, self.key)
        self.assertEqual(receipt["payload"]["result"], "FAILED")
        self.assertEqual(receipt["payload"]["tests_observed"], 0)

    def test_tampered_receipt_rejected(self):
        receipt = self.signed()
        receipt["payload"]["tests_observed"] = 100
        self.assertEqual(self.checked(receipt)["status"], "REJECTED")

    def test_signed_but_internally_invalid_manifest_rejected(self):
        for field, value in (("file_count", 2), ("total_bytes", 99)):
            with self.subTest(field=field):
                receipt = self.signed()
                manifest = receipt["payload"]["source_manifest"]
                manifest[field] = value
                manifest["manifest_sha256"] = hashlib.sha256(canonical({
                    k: v for k, v in manifest.items() if k != "manifest_sha256"
                })).hexdigest()
                receipt["signature"]["value"] = hmac.new(
                    self.key, canonical(receipt["payload"]), hashlib.sha256).hexdigest()
                result = verify(receipt, self.b, self.key,
                    expected_manifest_sha256=manifest["manifest_sha256"],
                    expected_image=IMAGE, minimum_tests=2)
                self.assertIn("INVALID_SOURCE_MANIFEST", result["reasons"])

    def test_wrong_expected_manifest_rejected(self):
        receipt = self.signed()
        result = verify(receipt, self.b, self.key,
                        expected_manifest_sha256="c" * 64, expected_image=IMAGE, minimum_tests=2)
        self.assertIn("SOURCE_MANIFEST_NOT_EXPECTED", result["reasons"])

    def test_wrong_expected_image_rejected(self):
        receipt = self.signed()
        result = verify(receipt, self.b, self.key,
                        expected_manifest_sha256=self.manifest["manifest_sha256"],
                        expected_image="python:3.12@sha256:" + "a" * 64, minimum_tests=2)
        self.assertIn("IMAGE_MISMATCH", result["reasons"])

    def test_signer_cannot_override_expected_case(self):
        envelope = self.signed()
        expected = {**self.b, "case_id": "other-case"}
        result = verify(envelope, expected, self.key,
                        expected_manifest_sha256=self.manifest["manifest_sha256"],
                        expected_image=IMAGE, minimum_tests=2)
        self.assertIn("BINDING_MISMATCH", result["reasons"])

    def test_partial_tests_rejected(self):
        receipt = self.signed()
        receipt["payload"]["tests_observed"] = 1
        receipt["signature"]["value"] = hmac.new(self.key, canonical(receipt["payload"]), hashlib.sha256).hexdigest()
        self.assertIn("INCOMPLETE_TEST_EVIDENCE", self.checked(receipt)["reasons"])

    def test_skips_and_failed_exit_rejected_even_if_signed(self):
        for field, value in (("skipped_detected", True), ("exit_code", 1), ("result", "FAILED")):
            with self.subTest(field=field):
                receipt = self.signed()
                receipt["payload"][field] = value
                receipt["signature"]["value"] = hmac.new(self.key, canonical(receipt["payload"]), hashlib.sha256).hexdigest()
                self.assertEqual(self.checked(receipt)["status"], "REJECTED")

    def test_bool_is_not_valid_payload_cycle(self):
        receipt = self.signed()
        receipt["payload"]["binding"]["cycle_no"] = True
        receipt["signature"]["value"] = hmac.new(self.key, canonical(receipt["payload"]), hashlib.sha256).hexdigest()
        self.assertEqual(self.checked(receipt)["status"], "REJECTED")

    def test_wrong_key_rejected(self):
        receipt = self.signed()
        r = verify(receipt, self.b, b"z" * 32,
                   expected_manifest_sha256=self.manifest["manifest_sha256"], expected_image=IMAGE, minimum_tests=2)
        self.assertIn("SIGNATURE_MISMATCH", r["reasons"])


if __name__ == "__main__":
    unittest.main()
