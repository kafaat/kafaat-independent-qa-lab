"""Single required CI gate: wait until every pull-request check on a commit passes.

Human review is optional in this repository, so CI is the only merge gate.
Requiring dozens of check names in the GitHub Ruleset is impractical and
drifts whenever a workflow changes. Instead the Ruleset requires this one
job (``ci-gate``), and the job derives the expected check names from the
workflow files themselves:

* every job of a workflow that runs on *every* pull request (a
  ``pull_request`` trigger without ``paths``/``paths-ignore``/``branches``
  filters) must report a GitHub Actions check run that concluded
  ``success``; a missing check fails closed at the deadline;
* any other GitHub Actions check run present on the commit (for example a
  path-filtered workflow that was triggered) must also conclude ``success``;
* for re-run jobs only the latest attempt of each check name *within one
  check suite* counts: a failure in one suite (for example the
  ``pull_request`` run) is not hidden by a newer success of the same name in
  another suite (for example a ``push`` run on the same commit).

With ``--head-tree`` (the ``pull_request_target`` run) this script and its
workflow come from the base branch and the PR head is data only. The expected
checks are those of the PR head, parsed by this trusted script, and the PR
must not silently weaken the gate:

* a check that the base requires on every pull request but the PR head no
  longer does (job removed or renamed, workflow deleted or path-filtered)
  fails the gate unless the PR description has a line
  ``ci-gate-drop: <name>[, <name>...]`` naming it;
* a change to the gate itself (its workflow or ``tools/ci/required_checks_gate.py``)
  fails the gate unless the PR description has the line
  ``ci-gate-change: acknowledged``.

Both acknowledgements are explicit and stay on the PR; neither is approval.

Also with ``--head-tree``, a path-filtered workflow whose only filters are
``paths``/``paths-ignore`` (patterns using ``*``, ``**``, ``?`` and leading
``!``) becomes required when the PR's changed files trigger it, so a
triggered workflow that never starts fails closed at the deadline instead of
being ignored. Workflows with other filters or pattern syntax, and PRs with
300 or more changed files (GitHub's own diff limit for path filters), are
reported as not evaluated.

Standard library only, so it runs on a bare ``setup-python`` interpreter.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GITHUB_ACTIONS_APP_ID = 15368
GATE_CHECK_NAME = "ci-gate"
SUCCESS = "success"
PR_FILTER_KEYS = ("paths", "paths-ignore", "branches", "branches-ignore", "types")

GATE_FILES = (".github/workflows/required-ci-gate.yml",
              "tools/ci/required_checks_gate.py")
_ACK_DROP = re.compile(r"(?m)^[ \t]*ci-gate-drop:[ \t]*(.+?)[ \t]*$")
_ACK_CHANGE = re.compile(r"(?m)^[ \t]*ci-gate-change:[ \t]*acknowledged[ \t]*$")

_TOP_KEY = re.compile(r"^([A-Za-z_][\w-]*|\"on\"|'on'):(.*)$")
_CHILD = re.compile(r"^(\s+)([A-Za-z_][\w-]*):(.*)$")


class WorkflowFormatError(ValueError):
    """A workflow uses a layout this fail-closed parser does not understand."""


def _top_level_blocks(text):
    blocks, current = {}, None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        match = _TOP_KEY.match(line)
        if match:
            current = match.group(1).strip("'\"")
            blocks[current] = [match.group(2).strip()]
            continue
        if current is not None:
            blocks[current].append(line)
    return blocks


def _children(lines, indent):
    """Split block lines into {key: (inline_value, child_lines)} at one indent."""
    out, current = {}, None
    for line in lines:
        match = _CHILD.match(line)
        if match and len(match.group(1)) == indent:
            current = match.group(2)
            out[current] = (match.group(3).strip(), [])
        elif current is not None and (len(line) - len(line.lstrip())) > indent:
            out[current][1].append(line)
        elif line.strip():
            raise WorkflowFormatError(f"unexpected line: {line.strip()!r}")
    return out


def _inline_list(value):
    value = value.split("#", 1)[0].strip()
    if not (value.startswith("[") and value.endswith("]")):
        raise WorkflowFormatError(f"matrix values must be an inline list: {value!r}")
    items = [item.strip().strip("'\"") for item in value[1:-1].split(",")]
    if not items or any(not item for item in items):
        raise WorkflowFormatError(f"empty matrix value in {value!r}")
    return items


def parse_workflow(text):
    """Return (runs_on_every_pr, [check names]) for one workflow file."""
    blocks = _top_level_blocks(text)
    if "on" not in blocks or "jobs" not in blocks:
        raise WorkflowFormatError("workflow needs top-level 'on' and 'jobs'")
    inline_on, *on_lines = blocks["on"]
    if inline_on:
        raise WorkflowFormatError("use block-style 'on:' triggers")
    triggers = _children(on_lines, 2)
    every_pr = False
    if "pull_request" in triggers:
        inline, child_lines = triggers["pull_request"]
        if inline:
            raise WorkflowFormatError("use block-style pull_request filters")
        filters = _children(child_lines, 4) if child_lines else {}
        unknown = set(filters) - set(PR_FILTER_KEYS)
        if unknown:
            raise WorkflowFormatError(f"unknown pull_request keys: {sorted(unknown)}")
        every_pr = not filters

    inline_jobs, *job_lines = blocks["jobs"]
    if inline_jobs:
        raise WorkflowFormatError("use block-style jobs")
    names = []
    for job_id, (inline, body) in _children(job_lines, 2).items():
        if inline:
            raise WorkflowFormatError(f"job {job_id} must be a mapping")
        fields = _children(body, 4)
        if "name" in fields:
            raise WorkflowFormatError(
                f"job {job_id}: use the job id as the check name (no 'name:')"
            )
        values = None
        if "strategy" in fields:
            strategy = _children(fields["strategy"][1], 6)
            if "matrix" in strategy:
                matrix = _children(strategy["matrix"][1], 8)
                if len(matrix) != 1:
                    raise WorkflowFormatError(
                        f"job {job_id}: only a single-axis matrix is supported"
                    )
                ((_, (axis_inline, axis_lines)),) = matrix.items()
                if axis_lines:
                    raise WorkflowFormatError(f"job {job_id}: inline matrix list only")
                values = _inline_list(axis_inline)
        if values:
            names.extend(f"{job_id} ({value})" for value in values)
        else:
            names.append(job_id)
    return every_pr, names


PATH_ONLY_KEYS = {"paths", "paths-ignore"}
PATH_DIFF_LIMIT = 300
_GLOB_UNSUPPORTED = re.compile(r"[\[\]{}\\]")


def _block_list(lines):
    items = []
    for line in lines:
        text = line.strip()
        if not text.startswith("- "):
            raise WorkflowFormatError(f"paths must be a block list: {text!r}")
        items.append(text[2:].split(" #", 1)[0].strip().strip("'\""))
    if not items or any(not item for item in items):
        raise WorkflowFormatError("empty path pattern")
    return items


def pr_path_filters(text):
    """Return {"paths": [...], "paths-ignore": [...]} for a pull_request trigger
    filtered only by paths, or None when there is no such trigger or other
    filters apply."""
    blocks = _top_level_blocks(text)
    _, *on_lines = blocks.get("on", [""])
    triggers = _children(on_lines, 2)
    if "pull_request" not in triggers:
        return None
    _, child_lines = triggers["pull_request"]
    filters = _children(child_lines, 4) if child_lines else {}
    if not filters or not set(filters) <= PATH_ONLY_KEYS:
        return None
    return {key: _block_list(lines) for key, (_, lines) in filters.items()}


def glob_regex(pattern):
    """GitHub filter glob to regex: ``**`` any path, ``*`` no slash, ``?`` one char."""
    if _GLOB_UNSUPPORTED.search(pattern):
        raise ValueError(f"unsupported pattern syntax: {pattern}")
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def _matches(patterns, path):
    """Ordered patterns; a later ``!pattern`` excludes what earlier ones matched."""
    hit = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        if glob_regex(pattern[1:] if negate else pattern).match(path):
            hit = not negate
    return hit


def triggered_by(filters, changed_files):
    """Would GitHub run a paths/paths-ignore-filtered pull_request workflow?"""
    files = list(changed_files)
    run = True
    if "paths" in filters:
        run = any(_matches(filters["paths"], f) for f in files)
    if run and "paths-ignore" in filters:
        run = not files or not all(_matches(filters["paths-ignore"], f) for f in files)
    return run


def triggered_path_checks(workflows_dir, changed_files):
    """Return (required check names, notices) for path-filtered workflows."""
    changed_files = list(changed_files)
    required, notices = [], []
    if len(changed_files) >= PATH_DIFF_LIMIT:
        return [], [f"path filters not evaluated: {len(changed_files)} changed files"]
    for path in sorted(Path(workflows_dir).glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        every_pr, names = parse_workflow(text)
        if every_pr:
            continue
        try:
            filters = pr_path_filters(text)
            if filters is None:
                continue
            if triggered_by(filters, changed_files):
                required.extend(names)
        except (ValueError, WorkflowFormatError) as error:
            notices.append(f"path filters not evaluated for {path.name}: {error}")
    return sorted(required), notices


def collect_check_names(workflows_dir):
    """Return (expected_every_pr_checks, all_check_names) for a workflows dir."""
    expected, every_name = [], []
    files = sorted(Path(workflows_dir).glob("*.yml")) + sorted(
        Path(workflows_dir).glob("*.yaml")
    )
    if not files:
        raise WorkflowFormatError("no workflow files found")
    for path in files:
        try:
            every_pr, names = parse_workflow(path.read_text(encoding="utf-8"))
        except WorkflowFormatError as error:
            raise WorkflowFormatError(f"{path.name}: {error}") from None
        every_name.extend(names)
        if every_pr:
            expected.extend(name for name in names if name != GATE_CHECK_NAME)
    duplicates = sorted({name for name in every_name if every_name.count(name) > 1})
    if duplicates:
        raise WorkflowFormatError(
            f"check names must be unique across workflows: {duplicates}"
        )
    if GATE_CHECK_NAME not in every_name:
        raise WorkflowFormatError(f"no workflow defines the {GATE_CHECK_NAME} job")
    return sorted(expected), sorted(every_name)


def acknowledgements(body):
    """Return (names acknowledged as dropped, gate change acknowledged)."""
    body = (body or "").replace("\r\n", "\n")
    dropped = set()
    for match in _ACK_DROP.finditer(body):
        dropped.update(n.strip() for n in match.group(1).split(",") if n.strip())
    return dropped, bool(_ACK_CHANGE.search(body))


def _read_optional(path):
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def compare_trees(base_root, head_root, body):
    """Compare the trusted base with the PR head (data only).

    Returns (expected, notices, problems): the head's every-PR checks parsed by
    the trusted parser, informational notices, and blocking problems.
    """
    base_expected, _ = collect_check_names(Path(base_root) / ".github" / "workflows")
    try:
        head_expected, _ = collect_check_names(Path(head_root) / ".github" / "workflows")
    except WorkflowFormatError as error:
        raise WorkflowFormatError(f"PR head: {error}") from None
    ack_dropped, ack_change = acknowledgements(body)
    notices, problems = [], []
    for name in sorted(set(base_expected) - set(head_expected)):
        if name in ack_dropped:
            notices.append(f"acknowledged drop: {name}")
        else:
            problems.append(
                f"required on every pull request by the base but not by this PR: {name} "
                f"(if intended, add 'ci-gate-drop: {name}' to the PR description)")
    changed = [rel for rel in GATE_FILES
               if _read_optional(Path(base_root) / rel) != _read_optional(Path(head_root) / rel)]
    if changed and ack_change:
        notices.extend(f"acknowledged gate change: {rel}" for rel in changed)
    elif changed:
        problems.extend(
            f"this PR changes the gate: {rel} (takes effect after merge; if intended, "
            f"add 'ci-gate-change: acknowledged' to the PR description)" for rel in changed)
    return head_expected, notices, problems


def pr_body(event_path):
    """Read the PR description from the event payload (never from argv/shell)."""
    if not event_path:
        return ""
    with open(event_path, encoding="utf-8") as handle:
        event = json.load(handle)
    return (event.get("pull_request") or {}).get("body") or ""


def latest_runs(check_runs):
    """Keep the newest GitHub Actions attempt per (check suite, check name).

    Returns {name: [latest run of that name in each suite]}.
    """
    latest = {}
    for run in check_runs:
        app = run.get("app") or {}
        if app.get("id") != GITHUB_ACTIONS_APP_ID:
            continue
        name = run.get("name")
        if not isinstance(name, str) or name == GATE_CHECK_NAME:
            continue
        key = (name, (run.get("check_suite") or {}).get("id"))
        if key not in latest or run.get("id", 0) > latest[key].get("id", 0):
            latest[key] = run
    by_name = {}
    for (name, _), run in sorted(latest.items(), key=lambda item: str(item[0])):
        by_name.setdefault(name, []).append(run)
    return by_name


def evaluate(expected, check_runs):
    """Return ("pass" | "pending" | "fail", messages) for one snapshot."""
    latest = latest_runs(check_runs)
    failed = sorted(
        f"{name}: {run.get('conclusion')}"
        for name, runs in latest.items() for run in runs
        if run.get("status") == "completed" and run.get("conclusion") != SUCCESS
    )
    if failed:
        return "fail", sorted(set(failed))
    missing = sorted(set(expected) - set(latest))
    running = sorted({n for n, runs in latest.items() for r in runs
                      if r.get("status") != "completed"})
    if missing or running:
        return "pending", [f"missing: {n}" for n in missing] + [
            f"running: {n}" for n in running
        ]
    return "pass", sorted(latest)


def _get_json(url, token):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def fetch_changed_files(repository, number, token, api="https://api.github.com"):
    """Changed paths of a PR as GitHub lists them (new name for renames).

    Over-requiring a check GitHub would not start blocks a merge until the
    deadline, so only the listed filename is used. Capped by GitHub at 3000.
    """
    files, page = [], 1
    while True:
        batch = _get_json(f"{api}/repos/{repository}/pulls/{int(number)}/files"
                          f"?per_page=100&page={page}", token)
        files.extend(item["filename"] for item in batch)
        if len(batch) < 100 or page >= 30:
            return files
        page += 1


def pr_number(event_path):
    if not event_path:
        return None
    with open(event_path, encoding="utf-8") as handle:
        return ((json.load(handle).get("pull_request") or {}).get("number"))


def fetch_check_runs(repository, sha, token, api="https://api.github.com"):
    runs, page = [], 1
    while True:
        query = urllib.parse.urlencode({
            "per_page": 100, "page": page, "filter": "all",
            "app_id": GITHUB_ACTIONS_APP_ID,
        })
        request = urllib.request.Request(
            f"{api}/repos/{repository}/commits/{sha}/check-runs?{query}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
        batch = body.get("check_runs", [])
        runs.extend(batch)
        if len(batch) < 100:
            return runs
        page += 1


def wait_for_checks(expected, fetch, *, deadline_s, interval_s, settle_s,
                    clock=time.monotonic, sleep=time.sleep, log=print):
    """Poll until pass/fail; a pass must hold for settle_s to catch late runs."""
    end = clock() + deadline_s
    passed_at = None
    last = []
    while True:
        try:
            state, messages = evaluate(expected, fetch())
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            state, messages = "pending", [f"API error: {error}"]
        if state == "fail":
            return "fail", messages
        if state == "pass":
            passed_at = passed_at if passed_at is not None else clock()
            if clock() - passed_at >= settle_s:
                return "pass", messages
        else:
            passed_at = None
            if messages != last:
                log("waiting: " + "; ".join(messages[:10]))
                last = messages
        if clock() >= end:
            return "fail", ["deadline reached"] + messages
        sleep(interval_s)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workflows", default=".github/workflows")
    parser.add_argument("--sha", default=os.environ.get("GATE_SHA"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--deadline", type=int, default=40 * 60)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--settle", type=int, default=20)
    parser.add_argument("--base-tree", default=".",
                        help="trusted base checkout used with --head-tree")
    parser.add_argument("--head-tree",
                        help="PR head checkout (data only); the base is the current tree")
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH"))
    parser.add_argument("--list", action="store_true",
                        help="print the expected checks and exit (no API calls)")
    args = parser.parse_args(argv)

    try:
        if args.head_tree:
            expected, notices, problems = compare_trees(
                args.base_tree, args.head_tree, pr_body(args.event_path))
        else:
            (expected, _), notices, problems = collect_check_names(args.workflows), [], []
    except (WorkflowFormatError, OSError, ValueError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    for notice in notices:
        print(f"NOTICE: {notice}")
    if problems:
        print("FAIL:", file=sys.stderr)
        print("\n".join(f"  {problem}" for problem in problems), file=sys.stderr)
        return 1
    if args.list:
        print("\n".join(expected))
        return 0
    token = os.environ.get("GITHUB_TOKEN")
    if not (args.sha and args.repository and token):
        print("FAIL: GATE_SHA, GITHUB_REPOSITORY and GITHUB_TOKEN are required",
              file=sys.stderr)
        return 1

    if args.head_tree:
        number = pr_number(args.event_path)
        if not number:
            print("FAIL: pull request number missing from the event", file=sys.stderr)
            return 1
        try:
            changed = fetch_changed_files(args.repository, number, token)
            path_required, path_notices = triggered_path_checks(
                Path(args.head_tree) / ".github" / "workflows", changed)
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError,
                WorkflowFormatError, OSError) as error:
            print(f"FAIL: path filters: {error}", file=sys.stderr)
            return 1
        for notice in path_notices:
            print(f"NOTICE: {notice}")
        if path_required:
            print("triggered by changed paths: " + ", ".join(path_required))
        expected = sorted(set(expected) | set(path_required))
    print(f"ci-gate: {len(expected)} checks required for {args.sha}")
    state, messages = wait_for_checks(
        expected,
        lambda: fetch_check_runs(args.repository, args.sha, token),
        deadline_s=args.deadline, interval_s=args.interval, settle_s=args.settle,
    )
    if state == "pass":
        print(f"PASS: {len(messages)} GitHub Actions checks succeeded:")
        print("\n".join(f"  {name}" for name in messages))
        return 0
    print("FAIL:", file=sys.stderr)
    print("\n".join(f"  {message}" for message in messages), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
