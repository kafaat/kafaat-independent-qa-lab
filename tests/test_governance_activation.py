import contextlib
import io
import json
import os
import subprocess
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

    def test_no_mandatory_human_review_in_main_policy(self):
        policy = activate_ruleset.load_policy("main")
        review = next(r["parameters"] for r in policy["rules"] if r["type"] == "pull_request")
        self.assertEqual(review["required_approving_review_count"], 0)
        self.assertFalse(review["require_last_push_approval"])
        self.assertFalse(review["required_review_thread_resolution"])

    def test_obsolete_review_policy_and_flag_are_rejected(self):
        for args in (["--policy", "independent-review"],
                     ["--policy", "main", "--reviewer-ready"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    activate_ruleset.parse_args(args)
                self.assertEqual(result.exception.code, 2)

    def test_apply_checks_admin_auth_and_creates_selected_policy(self):
        policy = activate_ruleset.load_policy("ci-only")
        with patch.object(
            activate_ruleset,
            "run_gh_api",
            side_effect=[[[]], {"id": 42}],
        ) as api, patch.object(activate_ruleset, "check_gh_auth") as auth_check:
            result = activate_ruleset.main(["--policy", "ci-only", "--apply"])

        self.assertEqual(result, 0)
        auth_check.assert_called_once_with()
        self.assertEqual(api.call_count, 2)
        api.assert_any_call(
            [
                "--paginate", "--slurp",
                "repos/kafaat/kafaat-independent-qa-lab/rulesets?per_page=100&includes_parents=true",
            ]
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
            return_value=[[{"name": policy["name"]}]],
        ) as api, patch.object(activate_ruleset, "check_gh_auth") as auth_check:
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                activate_ruleset.create_ruleset(policy)

        auth_check.assert_called_once_with()
        api.assert_called_once_with(
            [
                "--paginate", "--slurp",
                "repos/kafaat/kafaat-independent-qa-lab/rulesets?per_page=100&includes_parents=true",
            ]
        )

    def test_main_and_ci_only_policies_match(self):
        self.assertEqual(activate_ruleset.load_policy("main"),
                         activate_ruleset.load_policy("ci-only"))

    def test_loader_rejects_reintroduced_human_approval(self):
        policy = activate_ruleset.load_policy("main")
        review = next(r["parameters"] for r in policy["rules"] if r["type"] == "pull_request")
        review["required_approving_review_count"] = 1
        with patch("pathlib.Path.read_text", return_value=json.dumps(policy)):
            with self.assertRaisesRegex(ValueError, "mandatory human review"):
                activate_ruleset.load_policy("main")

    def test_loader_rejects_missing_ci_check(self):
        policy = activate_ruleset.load_policy("main")
        checks = next(r["parameters"] for r in policy["rules"] if r["type"] == "required_status_checks")
        checks["required_status_checks"].pop()
        with patch("pathlib.Path.read_text", return_value=json.dumps(policy)):
            with self.assertRaisesRegex(ValueError, "four GitHub Actions"):
                activate_ruleset.load_policy("main")

    def test_api_pins_github_host_even_with_enterprise_environment(self):
        response = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )
        with patch.dict(os.environ, {"GH_HOST": "enterprise.example.invalid"}):
            with patch.object(
                activate_ruleset.subprocess, "run", return_value=response
            ) as command:
                self.assertEqual(
                    activate_ruleset.run_gh_api(["repos/test/repo/rulesets"]),
                    {},
                )
        self.assertEqual(
            command.call_args.args[0][:4],
            ["gh", "api", "--hostname", "github.com"],
        )

    def test_authentication_uses_same_explicit_github_host(self):
        response = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        with patch.object(
            activate_ruleset.subprocess, "run", return_value=response
        ) as command:
            activate_ruleset.check_gh_auth()
        self.assertEqual(
            command.call_args.args[0],
            ["gh", "auth", "status", "--hostname", "github.com"],
        )

    def test_cli_validation_error_is_actionable_and_redacts_tokens(self):
        response = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="gh: HTTP 422 Validation Failed; github_pat_TESTSECRETVALUE123456",
        )
        with patch.object(
            activate_ruleset.subprocess, "run", return_value=response
        ):
            with self.assertRaises(RuntimeError) as failure:
                activate_ruleset.run_gh_api(["repos/test/repo/rulesets"])
        message = str(failure.exception)
        self.assertIn("422 Validation Failed", message)
        self.assertIn("exit 1", message)
        self.assertNotIn("github_pat_TESTSECRETVALUE123456", message)
        self.assertIn("[REDACTED TOKEN]", message)

    def test_cli_error_uses_stdout_when_stderr_is_empty(self):
        response = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="HTTP 403: Resource not accessible",
            stderr="",
        )
        with patch.object(
            activate_ruleset.subprocess, "run", return_value=response
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                activate_ruleset.run_gh_api(["repos/test/repo/rulesets"])

    def test_authentication_failure_retains_diagnostic(self):
        response = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="gh: no token configured for github.com",
        )
        with patch.object(
            activate_ruleset.subprocess, "run", return_value=response
        ):
            with self.assertRaisesRegex(RuntimeError, "no token configured"):
                activate_ruleset.check_gh_auth()

    def test_duplicate_on_later_page_fails_closed_without_post(self):
        policy = activate_ruleset.load_policy("ci-only")
        first_page = [{"name": f"unrelated-{i}"} for i in range(100)]
        next_page = [{"name": policy["name"]}]
        with patch.object(
            activate_ruleset, "run_gh_api",
            return_value=[first_page, next_page],
        ) as api, patch.object(activate_ruleset, "check_gh_auth"):
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                activate_ruleset.create_ruleset(policy)
        api.assert_called_once_with(
            [
                "--paginate", "--slurp",
                "repos/kafaat/kafaat-independent-qa-lab/rulesets?per_page=100&includes_parents=true",
            ]
        )

    def test_invalid_pagination_response_never_creates_ruleset(self):
        policy = activate_ruleset.load_policy("ci-only")
        with patch.object(
            activate_ruleset, "run_gh_api", return_value=[{"name": "bad shape"}]
        ) as api, patch.object(activate_ruleset, "check_gh_auth"):
            with self.assertRaisesRegex(RuntimeError, "Unexpected paginated"):
                activate_ruleset.create_ruleset(policy)
        api.assert_called_once()

    def test_main_remains_dry_run_without_apply(self):
        with patch.object(activate_ruleset, "check_gh_auth") as auth:
            with patch.object(activate_ruleset, "run_gh_api") as api:
                with contextlib.redirect_stdout(io.StringIO()):
                    result = activate_ruleset.main(
                        ["--policy", "main"]
                    )
        self.assertEqual(result, 0)
        auth.assert_not_called()
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
