"""Dry-run-first helper for an authorized admin to create a repository ruleset."""

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
POLICIES = {
    "ci-only": ROOT / "docs" / "governance" / "main-ruleset-ci-only.json",
    "independent-review": ROOT / "docs" / "governance" / "main-ruleset.json",
}
REPOSITORY = "kafaat/kafaat-independent-qa-lab"
GITHUB_HOST = "github.com"


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


def gh_error_detail(result):
    """Retain bounded CLI diagnostics without known credentials or header dumps."""
    detail = next((value.strip() for value in (result.stderr, result.stdout)
                   if value and value.strip()), "no diagnostic output")
    # Remove terminal/bidi controls before recognizing credential-bearing lines.
    detail = "".join(char for char in detail if char in "\n\t" or
                     unicodedata.category(char) not in {"Cc", "Cf"})
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        secret = os.environ.get(key)
        if secret:
            detail = detail.replace(secret, "[REDACTED TOKEN]")
    detail = re.sub(
        r"(?im)^.*\b(?:authorization|proxy-authorization|cookie|set-cookie|"
        r"x-auth-token|access_token|token|password|credential|secret)[\"']?\s*[:=].*$",
        "[REDACTED SENSITIVE LINE]", detail,
    )
    detail = re.sub(
        r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]{8,})\b",
        "[REDACTED TOKEN]",
        detail,
    )
    return detail[:4000]


def gh_environment():
    # Debug traces may contain raw request headers; never request them here.
    return {key: value for key, value in os.environ.items() if key not in {"GH_DEBUG", "DEBUG"}}


def run_gh_api(arguments, payload=None):
    result = subprocess.run(
        ["gh", "api", "--hostname", GITHUB_HOST, *arguments],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
        env=gh_environment(),
    )
    if result.returncode:
        raise RuntimeError(
            f"GitHub CLI API request failed (exit {result.returncode}): "
            f"{gh_error_detail(result)}"
        )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def check_gh_auth():
    result = subprocess.run(
        ["gh", "auth", "status", "--hostname", GITHUB_HOST],
        text=True,
        capture_output=True,
        check=False,
        env=gh_environment(),
    )
    if result.returncode:
        raise RuntimeError(
            f"GitHub CLI authentication check failed (exit {result.returncode}): "
            f"{gh_error_detail(result)}"
        )


def create_ruleset(policy):
    check_gh_auth()
    pages = run_gh_api(
        [
            "--paginate", "--slurp",
            f"repos/{REPOSITORY}/rulesets?per_page=100&includes_parents=true",
        ]
    )
    if not isinstance(pages, list) or not pages or any(
        not isinstance(page, list)
        or any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
               or not item["name"].strip() for item in page)
        for page in pages
    ):
        raise RuntimeError(
            "Unexpected paginated ruleset response; refusing to create a rule."
        )
    if any(item.get("name") == policy["name"] for page in pages for item in page):
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
