import contextlib
import io
import json
import unittest
from unittest.mock import patch

from tools.governance import activate_ruleset


class RulesetActivationTests(unittest.TestCase):
    def test_default_is_dry_run_without_github_calls(self):
        output = io.StringIO()
        with patch.object(activate_ruleset, "run_gh_api") as api:
            with contextlib.redirect_stdout(output):
                result = activate_ruleset.main(["--policy", "ci-only"])

        self.assertEqual(result, 0)
        self.assertIn("DRY RUN", output.getvalue())
        self.assertIn("QA Lab main — CI only (temporary)", output.getvalue())
        api.assert_not_called()

    def test_approval_policy_requires_explicit_reviewer_confirmation(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            result = activate_ruleset.main(
                ["--policy", "independent-review", "--apply"]
            )

        self.assertEqual(result, 2)
        self.assertIn("--reviewer-ready", errors.getvalue())

    def test_reviewer_confirmation_is_only_valid_for_approval_policy(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors):
            result = activate_ruleset.main(
                ["--policy", "ci-only", "--reviewer-ready"]
            )

        self.assertEqual(result, 2)
        self.assertIn("only applies", errors.getvalue())

    def test_apply_checks_admin_auth_and_creates_selected_policy(self):
        policy = activate_ruleset.load_policy("ci-only")
        with patch.object(
            activate_ruleset,
            "run_gh_api",
            side_effect=[[], {"id": 42}],
        ) as api, patch.object(activate_ruleset, "check_gh_auth") as auth_check:
            result = activate_ruleset.main(["--policy", "ci-only", "--apply"])

        self.assertEqual(result, 0)
        auth_check.assert_called_once_with()
        self.assertEqual(api.call_count, 2)
        api.assert_any_call(
            ["repos/kafaat/kafaat-independent-qa-lab/rulesets?per_page=100"]
        )
        api.assert_called_with(
            [
                "--method", "POST",
                "repos/kafaat/kafaat-independent-qa-lab/rulesets",
                "--input", "-",
            ],
            payload=json.dumps(policy),
        )

    def test_duplicate_ruleset_is_not_created(self):
        policy = activate_ruleset.load_policy("ci-only")
        with patch.object(
            activate_ruleset,
            "run_gh_api",
            return_value=[{"name": policy["name"]}],
        ) as api, patch.object(activate_ruleset, "check_gh_auth") as auth_check:
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                activate_ruleset.create_ruleset(policy)

        auth_check.assert_called_once_with()
        api.assert_called_once_with(
            ["repos/kafaat/kafaat-independent-qa-lab/rulesets?per_page=100"]
        )

    def test_full_policy_requires_at_least_one_approval(self):
        policy = activate_ruleset.load_policy("independent-review")
        review_rule = next(
            rule for rule in policy["rules"] if rule["type"] == "pull_request"
        )
        self.assertGreaterEqual(
            review_rule["parameters"]["required_approving_review_count"], 1
        )


if __name__ == "__main__":
    unittest.main()
