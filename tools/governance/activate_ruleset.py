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
    "main": ROOT / "docs" / "governance" / "main-ruleset.json",
    "ci-only": ROOT / "docs" / "governance" / "main-ruleset-ci-only.json",
}
REPOSITORY = "kafaat/kafaat-independent-qa-lab"
GITHUB_HOST = "github.com"
LEGACY_MAIN_RULESET_NAMES = {
    "QA Lab main — CI only (temporary)",
    "QA Lab main — independent review and verified CI",
}


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
    return parser.parse_args(argv)


def load_policy(policy_name):
    """Validate the no-human-review policy without relaxing required CI."""
    policy = json.loads(POLICIES[policy_name].read_text(encoding="utf-8"))
    if policy.get("target") != "branch" or policy.get("enforcement") != "active":
        raise ValueError("ruleset must target an active branch")
    if policy.get("conditions", {}).get("ref_name") != {
        "include": ["refs/heads/main"], "exclude": [],
    }:
        raise ValueError("ruleset must target main only")
    if policy.get("bypass_actors"):
        raise ValueError("bypass actors are not allowed")
    rules = {rule["type"]: rule.get("parameters", {}) for rule in policy["rules"]}
    if set(rules) != {
        "pull_request", "required_status_checks", "deletion", "non_fast_forward"
    }:
        raise ValueError("mandatory CI and branch protection rules are missing")
    review = rules["pull_request"]
    if review.get("required_approving_review_count") != 0 or any(
        review.get(flag) for flag in (
            "dismiss_stale_reviews_on_push", "require_code_owner_review",
            "require_last_push_approval", "required_review_thread_resolution",
        )
    ):
        raise ValueError("mandatory human review is not part of QA Lab policy")
    checks = rules["required_status_checks"]
    expected = {"test", "operator-ui", "root-stack", "http-stack"}
    required = checks.get("required_status_checks", [])
    if (not checks.get("strict_required_status_checks_policy")
            or len(required) != 4
            or {item.get("context") for item in required} != expected
            or any(item.get("integration_id") != 15368 for item in required)):
        raise ValueError("all four GitHub Actions checks remain mandatory")
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
    existing_names = {item.get("name") for page in pages for item in page}
    if policy["name"] in existing_names or existing_names.intersection(
        LEGACY_MAIN_RULESET_NAMES
    ):
        raise RuntimeError(
            "A QA Lab main ruleset already exists; update the existing rule "
            "in GitHub Settings instead of creating an overlapping rule."
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
    policy = load_policy(args.policy)
    if not args.apply:
        print(f"DRY RUN — no GitHub request made for {REPOSITORY}.")
        print(json.dumps(policy, indent=2, ensure_ascii=False))
        print("Review the proposal, then add --apply to create it.")
        print("Four CI checks are mandatory; human reviews are optional.")
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
