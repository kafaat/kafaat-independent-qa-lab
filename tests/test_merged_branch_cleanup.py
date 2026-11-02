"""Contract tests for tools/ci/merged_branch_cleanup.py (no network)."""
import contextlib
import io
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.ci import merged_branch_cleanup as cleanup

ROOT = Path(__file__).resolve().parents[1]
KEEP = {"main"}


def branch(name, sha="a" * 40, protected=False):
    return {"name": name, "commit": {"sha": sha}, "protected": protected}


def pr(number, sha="a" * 40, state="closed", merged=True):
    return {"number": number, "state": state, "head": {"sha": sha},
            "merged_at": "2026-11-01T00:00:00Z" if merged else None}


class ClassifyTests(unittest.TestCase):
    def test_merged_pr_with_same_head_is_deleted(self):
        self.assertEqual(cleanup.classify(branch("f"), [pr(7)], KEEP),
                         (True, "MERGED_PR_7"))

    def test_closed_unmerged_pr_with_same_head_is_deleted(self):
        self.assertEqual(cleanup.classify(branch("f"), [pr(9, merged=False)], KEEP),
                         (True, "CLOSED_UNMERGED_PR_9"))

    def test_default_and_protected_branches_are_kept(self):
        self.assertEqual(cleanup.classify(branch("main"), [pr(1)], KEEP),
                         (False, "DEFAULT_BRANCH"))
        self.assertEqual(cleanup.classify(branch("f", protected=True), [pr(1)], KEEP),
                         (False, "PROTECTED"))

    def test_open_pull_request_keeps_branch(self):
        prs = [pr(3), pr(4, state="open", merged=False)]
        self.assertEqual(cleanup.classify(branch("f"), prs, KEEP),
                         (False, "OPEN_PULL_REQUEST"))

    def test_head_moved_after_pull_request_keeps_branch(self):
        self.assertEqual(cleanup.classify(branch("f", sha="b" * 40), [pr(3)], KEEP),
                         (False, "HEAD_MOVED_AFTER_PULL_REQUEST"))

    def test_branch_without_pull_request_is_kept(self):
        self.assertEqual(cleanup.classify(branch("f"), [], KEEP),
                         (False, "NO_PULL_REQUEST"))


class FakeGitHub:
    repository = "o/r"

    def __init__(self, branches, pulls):
        self._branches, self._pulls, self.deleted = branches, pulls, []

    def repository_info(self):
        return {"default_branch": "main"}

    def branches(self):
        return self._branches

    def pulls_for(self, name):
        return self._pulls.get(name, [])

    def delete_branch(self, name):
        self.deleted.append(name)


class MainTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub(
            [branch("main"), branch("done"), branch("wip"), branch("moved", "c" * 40)],
            {"done": [pr(1)], "wip": [pr(2, state="open", merged=False)],
             "moved": [pr(3)]},
        )
        self.env = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r"}

    def run_main(self, args, env_extra=None):
        env = {**self.env, **(env_extra or {})}
        with patch.dict("os.environ", env, clear=True), \
                patch.object(cleanup, "GitHub", return_value=self.fake), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = cleanup.main(args)
        return code, out.getvalue()

    def test_dry_run_is_default_and_deletes_nothing(self):
        code, out = self.run_main([])
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.deleted, [])
        self.assertIn("DELETE done", out)
        self.assertIn("keep   wip", out)
        self.assertIn("mode=DRY RUN", out)

    def test_apply_deletes_only_eligible_branches(self):
        code, out = self.run_main(["--apply"])
        self.assertEqual(code, 0)
        self.assertEqual(self.fake.deleted, ["done"])
        self.assertIn("deleted 1, failed 0", out)

    def test_workflow_input_string_enables_apply(self):
        self.run_main([], {"CLEANUP_APPLY": "true"})
        self.assertEqual(self.fake.deleted, ["done"])
        self.fake.deleted.clear()
        self.run_main([], {"CLEANUP_APPLY": "false"})
        self.assertEqual(self.fake.deleted, [])

    def test_missing_credentials_fail_closed(self):
        with patch.dict("os.environ", {}, clear=True), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cleanup.main([]), 1)


class WorkflowTests(unittest.TestCase):
    def test_cleanup_workflow_is_manual_dry_run_by_default(self):
        source = (ROOT / ".github/workflows/branch-cleanup.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertNotIn("pull_request", source)
        self.assertNotIn("push:", source)
        self.assertRegex(source, r"default: false")
        self.assertIn("contents: write", source)


if __name__ == "__main__":
    unittest.main()
