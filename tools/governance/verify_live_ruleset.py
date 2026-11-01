"""Read-only check that the live main Ruleset matches the optional-review policy.

Run it with an admin-authenticated GitHub CLI after editing Ruleset #26292547.
It only issues GET requests; it never creates, updates or deletes settings.
Anonymous reads omit ``bypass_actors``, so an absent field fails closed.
"""

import argparse
import json
import sys

try:  # Allow both `python tools/governance/...` and package imports.
    from tools.governance import activate_ruleset
except ImportError:  # pragma: no cover - exercised only when run as a script
    import activate_ruleset  # type: ignore[no-redef]


LIVE_RULESET_ID = 26292547


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ruleset-id",
        type=int,
        default=LIVE_RULESET_ID,
        help="existing main Ruleset to read (default: %(default)s)",
    )
    return parser.parse_args(argv)


def check_live_state(ruleset, branch):
    """Return a list of failures; an empty list means the live state matches."""
    failures = []
    if not isinstance(ruleset, dict):
        return ["ruleset response is not an object"]
    if "bypass_actors" not in ruleset:
        failures.append(
            "bypass_actors not returned; read the Ruleset with an admin token"
        )
    elif ruleset["bypass_actors"] != []:
        failures.append("bypass actors are not allowed")
    try:
        activate_ruleset.validate_policy(ruleset)
    except ValueError as error:
        failures.append(str(error))
    if not isinstance(branch, dict) or branch.get("protected") is not True:
        failures.append("main is not reported as protected")
    return failures


def main(argv=None):
    args = parse_args(argv)
    repo = activate_ruleset.REPOSITORY
    try:
        activate_ruleset.check_gh_auth()
        ruleset = activate_ruleset.run_gh_api(
            [f"repos/{repo}/rulesets/{args.ruleset_id}"]
        )
        branch = activate_ruleset.run_gh_api([f"repos/{repo}/branches/main"])
    except (OSError, RuntimeError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 2

    failures = check_live_state(ruleset, branch)
    review = next(
        (rule.get("parameters", {}) for rule in ruleset.get("rules", [])
         if isinstance(rule, dict) and rule.get("type") == "pull_request"),
        {},
    )
    print(json.dumps({
        "ruleset_id": ruleset.get("id"),
        "name": ruleset.get("name"),
        "enforcement": ruleset.get("enforcement"),
        "updated_at": ruleset.get("updated_at"),
        "bypass_actors": ruleset.get("bypass_actors", "NOT RETURNED"),
        "pull_request": review,
        "main_protected": branch.get("protected") if isinstance(branch, dict) else None,
    }, indent=2, ensure_ascii=False))
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("PASS: live Ruleset requires the four CI checks and no human approval.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
