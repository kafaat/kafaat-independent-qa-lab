"""python-compile must measure its target: missing or empty -> BLOCKED, syntax error -> FAILED.

Reproduces the independent parity audit of 2026-11-09: on a repository with no qa_lab/
and a syntactically invalid app.py, the recipe returned PASSED (compileall exits 0 on
"Can't list 'qa_lab'") with a verified source and a verified receipt digest.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qa_lab import core
from qa_lab.core import Lab, file_catalogue

GIT = ["git", "-c", "user.email=qa@example.invalid", "-c", "user.name=qa"]


def git_repo(root, files):
    for name, text in files.items():
        path = Path(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", root], check=True)
    subprocess.run(GIT + ["-C", root, "add", "-A"], check=True)
    subprocess.run(GIT + ["-C", root, "commit", "-q", "-m", "fixture"], check=True)
    return subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


class CompileTargetTests(unittest.TestCase):
    def run_compile(self, files, require_sha="1"):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as ws:
            sha = git_repo(ws, files) if files is not None else "a" * 40
            lab = Lab(data)
            rid = lab.submit({"idempotency_key": "k", "project": "p", "source_sha": sha,
                              "recipe": "python-compile"})["run_id"]
            with patch.dict(os.environ, {"QA_REQUIRE_GIT_SHA": require_sha}):
                receipt = lab.execute(rid, Path(ws))
            stored = lab.get(rid)
            self.assertTrue(lab.verify(rid))
            leftovers = [p for p in Path(ws).rglob("__pycache__")]
        return receipt, stored, leftovers

    def test_the_audit_fixture_is_blocked_not_passed(self):
        receipt, stored, _ = self.run_compile({"app.py": "def broken(:\n"})
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(stored["status"], "BLOCKED")
        self.assertIn("does not exist", receipt["reason"])
        self.assertNotIn("catalogue", receipt)

    def test_an_empty_target_is_blocked(self):
        receipt, _, _ = self.run_compile({"qa_lab/README.md": "no python here\n"})
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("no Python files", receipt["reason"])

    def test_a_syntax_error_in_the_target_fails(self):
        receipt, _, _ = self.run_compile({"qa_lab/__init__.py": "", "qa_lab/bad.py": "def x(:\n"})
        self.assertEqual(receipt["status"], "FAILED")
        self.assertEqual(receipt["exit_code"], 1)
        self.assertEqual(receipt["catalogue"]["files"], 2)

    def test_a_valid_target_passes_with_a_count_and_a_catalogue_digest(self):
        files = {"qa_lab/__init__.py": "", "qa_lab/a.py": "X = 1\n", "qa_lab/sub/b.py": "Y = 2\n"}
        receipt, _, leftovers = self.run_compile(files)
        self.assertEqual(receipt["status"], "PASSED")
        self.assertIs(receipt["source_sha_verified"], True)
        self.assertEqual(receipt["catalogue"]["target"], "qa_lab")
        self.assertEqual(receipt["catalogue"]["files"], 3)
        self.assertRegex(receipt["catalogue"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(leftovers, [], "measuring must not write bytecode into the workspace")

    def test_the_catalogue_digest_changes_with_file_bytes(self):
        with tempfile.TemporaryDirectory() as ws:
            Path(ws, "qa_lab").mkdir()
            Path(ws, "qa_lab", "a.py").write_text("X = 1\n", encoding="utf-8")
            first = file_catalogue(ws, "qa_lab")
            Path(ws, "qa_lab", "a.py").write_text("X = 2\n", encoding="utf-8")
            self.assertNotEqual(first["sha256"], file_catalogue(ws, "qa_lab")["sha256"])

    def test_a_symlinked_target_is_blocked(self):
        with tempfile.TemporaryDirectory() as outside:
            Path(outside, "a.py").write_text("X = 1\n", encoding="utf-8")
            with tempfile.TemporaryDirectory() as ws:
                os.symlink(outside, Path(ws, "qa_lab"))
                with self.assertRaisesRegex(core.TargetUnavailable, "escapes"):
                    file_catalogue(ws, "qa_lab")

    def test_a_target_changed_during_execution_is_blocked(self):
        real_run = subprocess.run

        def run_and_tamper(cmd, **kwargs):
            result = real_run(cmd, **kwargs)
            if cmd == core.RECIPES["python-compile"]:
                Path(kwargs["cwd"], "qa_lab", "a.py").write_text("X = 99\n", encoding="utf-8")
            return result

        with patch.object(core.subprocess, "run", side_effect=run_and_tamper):
            receipt, _, _ = self.run_compile({"qa_lab/a.py": "X = 1\n"}, require_sha="0")
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("changed during execution", receipt["stderr_tail"])

    def test_smoke_recipe_carries_no_catalogue(self):
        with tempfile.TemporaryDirectory() as data, tempfile.TemporaryDirectory() as ws:
            lab = Lab(data)
            rid = lab.submit({"idempotency_key": "s", "project": "p", "source_sha": "a" * 40,
                              "recipe": "python-smoke"})["run_id"]
            with patch.dict(os.environ, {"QA_REQUIRE_GIT_SHA": "0"}):
                receipt = lab.execute(rid, Path(ws))
        self.assertEqual(receipt["status"], "PASSED")
        self.assertNotIn("catalogue", receipt)


if __name__ == "__main__":
    unittest.main()
