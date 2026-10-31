"""PR #48 safety regressions. Every CLI process is mocked; no live activation."""
import contextlib
import io
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from tools.governance import activate_ruleset as helper


class ActivationReviewTests(unittest.TestCase):
    def result(self, *, code=0, stdout="", stderr=""):
        return subprocess.CompletedProcess([], code, stdout, stderr)

    def test_unprefixed_authorization_is_not_echoed(self):
        for scheme in ("Bearer", "Basic", "token"):
            with self.subTest(scheme=scheme):
                text = helper.gh_error_detail(self.result(code=1, stderr=(
                    "gh: HTTP 422 Validation Failed\n"
                    f"> Authorization: {scheme} synthetic-opaque-secret\n")))
                self.assertIn("422 Validation Failed", text)
                self.assertNotIn("synthetic-opaque-secret", text)

    def test_cookie_and_proxy_credentials_are_not_echoed(self):
        for header in ("Cookie", "Set-Cookie", "Proxy-Authorization", "X-Auth-Token"):
            with self.subTest(header=header):
                text = helper.gh_error_detail(self.result(
                    code=1, stderr=f"HTTP 403\n{header}: synthetic-secret-value\n"))
                self.assertIn("HTTP 403", text)
                self.assertNotIn("synthetic-secret-value", text)

    def test_json_sensitive_fields_are_not_echoed(self):
        text = helper.gh_error_detail(self.result(code=1, stderr=(
            'HTTP 422\n{"authorization": "Bearer synthetic-json-secret"}\n')))
        self.assertIn("HTTP 422", text)
        self.assertNotIn("synthetic-json-secret", text)

    def test_environment_token_is_redacted_without_a_known_prefix(self):
        with patch.dict(os.environ, {"GH_TOKEN": "legacy-opaque-test-token"}):
            text = helper.gh_error_detail(self.result(
                code=1, stderr="HTTP 401 rejected legacy-opaque-test-token"))
        self.assertIn("HTTP 401", text)
        self.assertNotIn("legacy-opaque-test-token", text)

    def test_stdout_fallback_receives_the_same_redaction(self):
        text = helper.gh_error_detail(self.result(
            code=1, stdout="HTTP 403\nAuthorization: Basic synthetic-stdout-secret"))
        self.assertIn("HTTP 403", text)
        self.assertNotIn("synthetic-stdout-secret", text)

    def test_whitespace_stderr_uses_actionable_stdout(self):
        text = helper.gh_error_detail(self.result(
            code=1, stdout="HTTP 422 Validation Failed", stderr=" \n\t"))
        self.assertEqual(text, "HTTP 422 Validation Failed")

    def test_terminal_controls_cannot_spoof_diagnostics(self):
        text = helper.gh_error_detail(self.result(
            code=1, stderr="\x1b[31mHTTP 422\x1b[0m\r\x00\u202e"))
        self.assertIn("HTTP 422", text)
        for control in ("\x1b", "\r", "\x00", "\u202e"):
            self.assertNotIn(control, text)

    def test_common_tokens_and_output_length_remain_bounded(self):
        text = helper.gh_error_detail(self.result(code=1, stderr=(
            "HTTP 422 ghp_SYNTHETIC12345 github_pat_SYNTHETIC98765 " + "x" * 5000)))
        self.assertLessEqual(len(text), 4000)
        self.assertIn("HTTP 422", text)
        self.assertNotIn("ghp_SYNTHETIC12345", text)
        self.assertNotIn("github_pat_SYNTHETIC98765", text)

    def test_cli_child_disables_debug_without_changing_parent_environment(self):
        with patch.dict(os.environ, {"GH_DEBUG": "api", "DEBUG": "1",
                                    "GH_HOST": "enterprise.example.invalid"}):
            with patch.object(helper.subprocess, "run", return_value=self.result(stdout="{}")) as run:
                helper.run_gh_api(["repos/test/repo/rulesets"])
                helper.check_gh_auth()
            for call in run.call_args_list:
                child = call.kwargs.get("env", os.environ)
                self.assertNotIn("GH_DEBUG", child)
                self.assertNotIn("DEBUG", child)
                command = call.args[0]
                self.assertEqual(command[command.index("--hostname") + 1], "github.com")
            self.assertEqual(os.environ["GH_DEBUG"], "api")
            self.assertEqual(os.environ["DEBUG"], "1")

    def test_create_reads_all_pages_and_posts_once_to_the_same_host(self):
        policy = helper.load_policy("ci-only")
        responses = [self.result(), self.result(stdout=json.dumps([
            [{"name": "other-1"}], [{"name": "other-2"}]])), self.result(stdout='{"id":42}')]
        with patch.dict(os.environ, {"GH_HOST": "enterprise.example.invalid"}):
            with patch.object(helper.subprocess, "run", side_effect=responses) as run:
                self.assertEqual(helper.create_ruleset(policy), {"id": 42})
        self.assertEqual(run.call_count, 3)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(all(command[command.index("--hostname") + 1] == "github.com"
                            for command in commands))
        self.assertIn("--paginate", commands[1])
        self.assertIn("--slurp", commands[1])
        self.assertIn("includes_parents=true", commands[1][-1])
        self.assertEqual(commands[2][commands[2].index("--method") + 1], "POST")
        self.assertEqual(json.loads(run.call_args_list[2].kwargs["input"]), policy)

    def test_duplicate_on_third_page_prevents_post(self):
        policy = helper.load_policy("ci-only")
        pages = [[{"name": f"other-{i}"} for i in range(100)],
                 [{"name": f"next-{i}"} for i in range(100)], [{"name": policy["name"]}]]
        with patch.object(helper.subprocess, "run", side_effect=[
            self.result(), self.result(stdout=json.dumps(pages))]) as run:
            with self.assertRaisesRegex(RuntimeError, "already exists"):
                helper.create_ruleset(policy)
        self.assertEqual(run.call_count, 2)
        self.assertFalse(any("POST" in call.args[0] for call in run.call_args_list))

    def test_failed_later_page_never_posts_despite_partial_stdout(self):
        with patch.object(helper.subprocess, "run", side_effect=[
            self.result(), self.result(code=1, stdout='[[{"name":"first-page"}]]',
                                       stderr="HTTP 503 page retrieval failed")]) as run:
            with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                helper.create_ruleset(helper.load_policy("ci-only"))
        self.assertEqual(run.call_count, 2)

    def test_zero_pages_is_not_a_confirmed_empty_listing(self):
        with patch.object(helper, "check_gh_auth"), patch.object(
                helper, "run_gh_api", side_effect=[[], {"id":42}]) as api:
            with self.assertRaisesRegex(RuntimeError, "Unexpected paginated"):
                helper.create_ruleset(helper.load_policy("ci-only"))
        self.assertEqual(api.call_count, 1)

    def test_a_returned_empty_page_can_proceed(self):
        with patch.object(helper, "check_gh_auth"), patch.object(
                helper, "run_gh_api", side_effect=[[[]], {"id":42}]) as api:
            self.assertEqual(helper.create_ruleset(helper.load_policy("ci-only")), {"id":42})
        self.assertEqual(api.call_count, 2)

    def test_missing_or_unusable_ruleset_name_never_posts(self):
        for item in ({}, {"name":None}, {"name":123}, {"name":""}, {"name":"  "}):
            with self.subTest(item=item), patch.object(helper, "check_gh_auth"), patch.object(
                    helper, "run_gh_api", side_effect=[[[item]], {"id":42}]) as api:
                with self.assertRaisesRegex(RuntimeError, "Unexpected paginated"):
                    helper.create_ruleset(helper.load_policy("ci-only"))
                self.assertEqual(api.call_count, 1)

    def test_malformed_nested_page_shapes_never_post(self):
        for pages in ({}, [None], [[None]], [{"name":"flat"}]):
            with self.subTest(pages=pages), patch.object(helper, "check_gh_auth"), patch.object(
                    helper, "run_gh_api", side_effect=[pages, {"id":42}]) as api:
                with self.assertRaisesRegex(RuntimeError, "Unexpected paginated"):
                    helper.create_ruleset(helper.load_policy("ci-only"))
                self.assertEqual(api.call_count, 1)

    def test_invalid_json_does_not_reach_create(self):
        with patch.object(helper.subprocess, "run", side_effect=[
            self.result(), self.result(stdout="not JSON")]) as run:
            with self.assertRaises(json.JSONDecodeError):
                helper.create_ruleset(helper.load_policy("ci-only"))
        self.assertEqual(run.call_count, 2)

    def test_both_dry_runs_do_not_start_any_process(self):
        with patch.object(helper.subprocess, "run", side_effect=AssertionError("no process allowed")) as run:
            for policy in ("ci-only", "independent-review"):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(helper.main(["--policy", policy]), 0)
        run.assert_not_called()

    def test_independent_review_without_confirmation_never_starts_process(self):
        with patch.object(helper.subprocess, "run") as run, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(helper.main(["--policy", "independent-review", "--apply"]), 2)
        run.assert_not_called()

    def test_error_exit_does_not_announce_creation(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(helper.subprocess, "run", side_effect=[
            self.result(), self.result(code=1, stderr="HTTP 422 Validation Failed")]) as run:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(helper.main(["--policy", "ci-only", "--apply"]), 1)
        self.assertNotIn("Created ruleset", out.getvalue())
        self.assertIn("HTTP 422", err.getvalue())
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
