import contextlib
import copy
import io
import json
import unittest
from unittest.mock import patch

from tools.governance import activate_ruleset, verify_live_ruleset


def live_ruleset(**review_overrides):
    """A GitHub API-shaped Ruleset built from the canonical policy file."""
    policy = activate_ruleset.load_policy("main")
    live = copy.deepcopy(policy)
    live.update({
        "id": 26292547,
        "source_type": "Repository",
        "updated_at": "2026-11-01T19:45:00+03:00",
        "current_user_can_bypass": "never",
    })
    review = next(r["parameters"] for r in live["rules"] if r["type"] == "pull_request")
    review["allowed_merge_methods"] = ["merge", "squash", "rebase"]
    review.update(review_overrides)
    return live


# Exact pull_request parameters read from Ruleset #26292547 on 2026-11-01
# before the owner's optional-review decision was applied.
PRE_DECISION_REVIEW = {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews_on_push": True,
    "required_reviewers": [],
    "require_code_owner_review": False,
    "require_last_push_approval": True,
    "required_review_thread_resolution": True,
    "require_extra_approval_for_unattributed_changes": True,
}


class LiveRulesetVerificationTests(unittest.TestCase):
    PROTECTED = {"name": "main", "protected": True}

    def test_aligned_live_state_passes(self):
        self.assertEqual(
            verify_live_ruleset.check_live_state(live_ruleset(), self.PROTECTED), []
        )

    def test_pre_decision_live_state_fails(self):
        failures = verify_live_ruleset.check_live_state(
            live_ruleset(**PRE_DECISION_REVIEW), self.PROTECTED
        )
        self.assertTrue(any("mandatory human review" in f for f in failures))

    def test_unattributed_change_approval_alone_fails(self):
        failures = verify_live_ruleset.check_live_state(
            live_ruleset(require_extra_approval_for_unattributed_changes=True),
            self.PROTECTED,
        )
        self.assertTrue(any("mandatory human review" in f for f in failures))

    def test_omitted_review_gate_fails_closed(self):
        ruleset = live_ruleset()
        review = next(r["parameters"] for r in ruleset["rules"]
                      if r["type"] == "pull_request")
        del review["require_extra_approval_for_unattributed_changes"]
        failures = verify_live_ruleset.check_live_state(ruleset, self.PROTECTED)
        self.assertTrue(any("mandatory human review" in f for f in failures))

    def test_required_reviewer_teams_fail(self):
        failures = verify_live_ruleset.check_live_state(
            live_ruleset(required_reviewers=[{"reviewer": {"id": 1, "type": "Team"}}]),
            self.PROTECTED,
        )
        self.assertTrue(any("mandatory human review" in f for f in failures))

    def test_anonymous_read_without_bypass_field_fails_closed(self):
        ruleset = live_ruleset()
        del ruleset["bypass_actors"]
        failures = verify_live_ruleset.check_live_state(ruleset, self.PROTECTED)
        self.assertTrue(any("admin token" in f for f in failures))

    def test_bypass_actor_fails(self):
        ruleset = live_ruleset()
        ruleset["bypass_actors"] = [{"actor_id": 5, "actor_type": "RepositoryRole"}]
        failures = verify_live_ruleset.check_live_state(ruleset, self.PROTECTED)
        self.assertIn("bypass actors are not allowed", failures)

    def test_weakened_ci_gate_fails(self):
        ruleset = live_ruleset()
        checks = next(r["parameters"] for r in ruleset["rules"]
                      if r["type"] == "required_status_checks")
        checks["do_not_enforce_on_create"] = True
        failures = verify_live_ruleset.check_live_state(ruleset, self.PROTECTED)
        self.assertTrue(any("four GitHub Actions" in f for f in failures))

    def test_unprotected_main_fails(self):
        failures = verify_live_ruleset.check_live_state(
            live_ruleset(), {"name": "main", "protected": False}
        )
        self.assertIn("main is not reported as protected", failures)

    def test_main_reads_only_and_reports_pass(self):
        output = io.StringIO()
        with patch.object(verify_live_ruleset.activate_ruleset, "check_gh_auth"), \
                patch.object(verify_live_ruleset.activate_ruleset, "run_gh_api",
                             side_effect=[live_ruleset(), self.PROTECTED]) as api, \
                contextlib.redirect_stdout(output):
            result = verify_live_ruleset.main([])
        self.assertEqual(result, 0)
        self.assertIn("PASS", output.getvalue())
        calls = [call.args[0] for call in api.call_args_list]
        self.assertEqual(calls, [
            ["repos/kafaat/kafaat-independent-qa-lab/rulesets/26292547"],
            ["repos/kafaat/kafaat-independent-qa-lab/branches/main"],
        ])
        self.assertFalse(any("--method" in call for call in calls))

    def test_main_returns_failure_for_pre_decision_state(self):
        with patch.object(verify_live_ruleset.activate_ruleset, "check_gh_auth"), \
                patch.object(verify_live_ruleset.activate_ruleset, "run_gh_api",
                             side_effect=[live_ruleset(**PRE_DECISION_REVIEW),
                                          self.PROTECTED]), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            result = verify_live_ruleset.main([])
        self.assertEqual(result, 1)
        self.assertIn("FAIL", errors.getvalue())

    def test_policy_file_is_a_valid_put_payload_for_existing_ruleset(self):
        policy = json.loads(
            (activate_ruleset.POLICIES["main"]).read_text(encoding="utf-8")
        )
        self.assertEqual(policy["bypass_actors"], [])
        review = next(r["parameters"] for r in policy["rules"]
                      if r["type"] == "pull_request")
        for gate in activate_ruleset.HUMAN_REVIEW_GATES:
            with self.subTest(gate=gate):
                self.assertIs(review[gate], False)
        self.assertEqual(review["required_reviewers"], [])


if __name__ == "__main__":
    unittest.main()
