"""Validate the importable GitHub ruleset proposal without claiming live enforcement.

The live GitHub settings must be verified separately after a repository admin imports it.
"""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "governance" / "main-ruleset.json"
CI_ONLY_POLICY = ROOT / "docs" / "governance" / "main-ruleset-ci-only.json"
REQUIRED = {
    "test": ".github/workflows/python-tests.yml",
    "operator-ui": ".github/workflows/operator-compose.yml",
    "root-stack": ".github/workflows/root-stack.yml",
    "http-stack": ".github/workflows/http-acceptance.yml",
}
GITHUB_ACTIONS_APP_ID = 15368  # Verified from PR #44 check runs.


class RulesetPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ruleset = json.loads(POLICY.read_text(encoding="utf-8"))
        cls.rules = {rule["type"]: rule.get("parameters", {}) for rule in cls.ruleset["rules"]}

    def test_main_only_active_and_no_bypass(self):
        policy = self.ruleset
        self.assertEqual(policy["target"], "branch")
        self.assertEqual(policy["enforcement"], "active")
        self.assertEqual(policy["conditions"]["ref_name"],
                         {"include": ["refs/heads/main"], "exclude": []})
        self.assertEqual(policy.get("bypass_actors", []), [])
        self.assertEqual(set(self.rules), {
            "pull_request", "required_status_checks", "deletion", "non_fast_forward"
        })

    def test_requires_independent_current_approval_and_resolved_threads(self):
        params = self.rules["pull_request"]
        self.assertEqual(params["required_approving_review_count"], 1)
        self.assertTrue(params["require_last_push_approval"])
        self.assertTrue(params["dismiss_stale_reviews_on_push"])
        self.assertTrue(params["required_review_thread_resolution"])
        self.assertFalse(params["require_code_owner_review"])

    def test_temporary_ci_only_proposal_has_no_approval_gate(self):
        policy = json.loads(CI_ONLY_POLICY.read_text(encoding="utf-8"))
        review_rule = next(
            rule["parameters"] for rule in policy["rules"]
            if rule["type"] == "pull_request"
        )
        self.assertIn("CI only (temporary)", policy["name"])
        self.assertEqual(review_rule["required_approving_review_count"], 0)
        self.assertEqual(policy["conditions"]["ref_name"]["include"], ["refs/heads/main"])
        checks = next(
            rule["parameters"]["required_status_checks"] for rule in policy["rules"]
            if rule["type"] == "required_status_checks"
        )
        self.assertEqual({check["context"] for check in checks}, set(REQUIRED))

    def test_only_unique_github_actions_checks_required(self):
        params = self.rules["required_status_checks"]
        self.assertTrue(params["strict_required_status_checks_policy"])
        checks = params["required_status_checks"]
        self.assertEqual({x["context"] for x in checks}, set(REQUIRED))
        self.assertEqual(len(checks), len(REQUIRED))
        self.assertTrue(all(x["integration_id"] == GITHUB_ACTIONS_APP_ID for x in checks))

    def test_every_required_check_has_unfiltered_pr_trigger_and_expected_job(self):
        for job_name, file_path in REQUIRED.items():
            with self.subTest(job=job_name):
                source = (ROOT / file_path).read_text(encoding="utf-8")
                self.assertRegex(source, r"(?m)^  pull_request:\s*$")
                self.assertRegex(source, r"(?m)^jobs:\s*$")
                self.assertRegex(source, rf"(?m)^  {re.escape(job_name)}:\s*$")
                # A missing conditional job must not silently satisfy required checks.
                block = source.split("jobs:\n", 1)[1].split(f"  {job_name}:\n", 1)[1]
                self.assertNotIn("\n    if:", block.split("\n  ", 1)[0])

    def test_governance_handoff_explicitly_disclaims_enforcement_and_review(self):
        guide = (ROOT / "docs" / "governance" / "RULESET_ACTIVATION_AR.md").read_text(encoding="utf-8")
        self.assertIn("لم تُفعّل", guide)
        self.assertIn("مراجعة بشرية مستقلة", guide)
        self.assertIn("Settings", guide)


if __name__ == "__main__":
    unittest.main()
