"""Dry-run-first helper for an authorized admin to create a repository ruleset."""

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLICIES = {
    "ci-only": ROOT / "docs" / "governance" / "main-ruleset-ci-only.json",
    "independent-review": ROOT / "docs" / "governance" / "main-ruleset.json",
}
REPOSITORY = "kafaat/kafaat-independent-qa-lab"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--policy",
        choices=POLICIES,
        required=True,
        help="ruleset proposal to create",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="create the ruleset using the authenticated GitHub CLI",
    )
    parser.add_argument(
        "--reviewer-ready",
        action="store_true",
        help="confirm an eligible independent reviewer is available",
    )
    return parser.parse_args(argv)


def load_policy(policy_name):
    policy = json.loads(POLICIES[policy_name].read_text(encoding="utf-8"))
    if policy_name == "independent-review":
        parameters = next(
            rule["parameters"] for rule in policy["rules"]
            if rule["type"] == "pull_request"
        )
        if parameters["required_approving_review_count"] < 1:
            raise ValueError("the independent-review policy must require approval")
    return policy


def run_gh_api(arguments, payload=None):
    result = subprocess.run(
        ["gh", "api", *arguments],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "GitHub CLI request failed; check `gh auth status` and repository admin access."
        )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def check_gh_auth():
    result = subprocess.run(
        ["gh", "auth", "status", "--hostname", "github.com"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            "GitHub CLI authentication check failed; run `gh auth status`."
        )


def create_ruleset(policy):
    check_gh_auth()
    existing = run_gh_api(
        [f"repos/{REPOSITORY}/rulesets?per_page=100"]
    )
    if any(item.get("name") == policy["name"] for item in existing):
        raise RuntimeError(
            f"A ruleset named {policy['name']!r} already exists; inspect it in GitHub Settings."
        )
    created = run_gh_api(
        [
            "--method", "POST",
            f"repos/{REPOSITORY}/rulesets",
            "--input", "-",
        ],
        payload=json.dumps(policy),
    )
    return created


def main(argv=None):
    args = parse_args(argv)
    if args.reviewer_ready and args.policy != "independent-review":
        print("--reviewer-ready only applies to the independent-review policy.", file=sys.stderr)
        return 2
    if args.policy == "independent-review" and args.apply and not args.reviewer_ready:
        print(
            "Refusing to apply the approval gate without --reviewer-ready; "
            "confirm an eligible independent reviewer first.",
            file=sys.stderr,
        )
        return 2

    policy = load_policy(args.policy)
    if not args.apply:
        print(f"DRY RUN — no GitHub request made for {REPOSITORY}.")
        print(json.dumps(policy, indent=2, ensure_ascii=False))
        print("Review the proposal, then add --apply to create it.")
        if args.policy == "independent-review":
            print("Before applying, ensure an eligible independent reviewer is available.")
        return 0

    try:
        created = create_ruleset(policy)
    except (OSError, RuntimeError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1

    print(
        f"Created ruleset {created.get('id', '(ID not returned)')}. "
        "Verify its active state and effective branch rules in GitHub Settings."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
