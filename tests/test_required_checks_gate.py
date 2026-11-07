"""Contract tests for the single required CI gate (tools/ci/required_checks_gate.py)."""
import contextlib
import io
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.ci import required_checks_gate as gate

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
RULESET_REQUIRED = {"test", "operator-ui", "root-stack", "http-stack"}


def run(name, conclusion="success", status="completed", run_id=1,
        app_id=gate.GITHUB_ACTIONS_APP_ID):
    return {"id": run_id, "name": name, "status": status,
            "conclusion": conclusion if status == "completed" else None,
            "app": {"id": app_id}}


def write_workflows(directory, files):
    for name, text in files.items():
        (Path(directory) / name).write_text(textwrap.dedent(text), encoding="utf-8")


GATE_WORKFLOW = """\
name: gate
on:
  pull_request:
jobs:
  ci-gate:
    runs-on: ubuntu-latest
"""


class RepositoryWorkflowTests(unittest.TestCase):
    """The real workflow tree must parse and keep check names unique."""

    @classmethod
    def setUpClass(cls):
        cls.expected, cls.every_name = gate.collect_check_names(WORKFLOWS)

    def test_every_workflow_parses_and_names_are_unique(self):
        self.assertEqual(len(self.every_name), len(set(self.every_name)))
        self.assertIn(gate.GATE_CHECK_NAME, self.every_name)

    def test_gate_covers_the_ruleset_checks_but_not_itself(self):
        self.assertTrue(RULESET_REQUIRED <= set(self.expected))
        self.assertNotIn(gate.GATE_CHECK_NAME, self.expected)

    def test_matrix_jobs_expand_to_github_check_names(self):
        for name in ("parity (3.12)", "parity (3.13)", "audio (3.12)",
                     "audio-unit-http (3.13)", "unit (3.12)"):
            with self.subTest(name=name):
                self.assertIn(name, self.expected)
        self.assertNotIn("parity", self.expected)

    def test_live_infrastructure_checks_are_gated(self):
        for name in ("postgres", "jetstream", "live", "dashboard-postgres",
                     "postgres-review", "private-docker", "docker-build"):
            with self.subTest(name=name):
                self.assertIn(name, self.expected)

    def test_path_filtered_workflows_are_not_always_required(self):
        for name in ("receipt-cost", "guard", "nats-start-verify"):
            with self.subTest(name=name):
                self.assertIn(name, self.every_name)
                self.assertNotIn(name, self.expected)

    def test_gate_workflow_has_read_only_checks_permission(self):
        source = (WORKFLOWS / "required-ci-gate.yml").read_text(encoding="utf-8")
        self.assertIn("checks: read", source)
        self.assertNotIn("write", source)
        self.assertIn("github.event.pull_request.head.sha", source)
        self.assertRegex(source, r"(?m)^  pull_request_target:\s*$")

    def test_all_actions_are_pinned_to_commit_shas(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if "uses:" in line:
                    with self.subTest(file=path.name, line=line.strip()):
                        self.assertRegex(line, r"uses: [\w.-]+/[\w.-]+@[0-9a-f]{40}\b")

    def test_every_workflow_declares_top_level_permissions(self):
        for path in sorted(WORKFLOWS.glob("*.yml")):
            with self.subTest(file=path.name):
                self.assertRegex(path.read_text(encoding="utf-8"),
                                 r"(?m)^permissions:\s*$")


class ParserTests(unittest.TestCase):
    def collect(self, files):
        with tempfile.TemporaryDirectory() as directory:
            write_workflows(directory, {"gate.yml": GATE_WORKFLOW, **files})
            return gate.collect_check_names(directory)

    def test_filtered_and_unfiltered_triggers(self):
        expected, every = self.collect({
            "a.yml": """\
                name: a
                on:
                  push:
                    branches: [main]
                  pull_request:
                permissions:
                  contents: read
                jobs:
                  alpha:
                    runs-on: ubuntu-latest
                    strategy:
                      matrix:
                        python: ['3.12', '3.13']
                    steps:
                      - run: |
                          echo hi
                          # not a key
                  beta:
                    runs-on: ubuntu-latest
                """,
            "b.yml": """\
                name: b
                on:
                  pull_request:
                    paths:
                      - 'x/**'
                  workflow_dispatch:
                jobs:
                  gamma:
                    runs-on: ubuntu-latest
                """,
            "c.yml": """\
                name: c
                on:
                  workflow_dispatch:
                jobs:
                  delta:
                    runs-on: ubuntu-latest
                """,
        })
        self.assertEqual(expected, ["alpha (3.12)", "alpha (3.13)", "beta"])
        self.assertEqual(set(every),
                         {"alpha (3.12)", "alpha (3.13)", "beta", "gamma",
                          "delta", "ci-gate"})

    def test_duplicate_names_fail_closed(self):
        dup = """\
            name: d
            on:
              pull_request:
            jobs:
              image:
                runs-on: ubuntu-latest
            """
        with self.assertRaisesRegex(gate.WorkflowFormatError, "unique"):
            self.collect({"d1.yml": dup, "d2.yml": dup})

    def test_unsupported_layouts_fail_closed(self):
        cases = {
            "inline on": "name: x\non: [pull_request]\njobs:\n  a:\n    runs-on: u\n",
            "job name": ("name: x\non:\n  pull_request:\njobs:\n  a:\n"
                         "    name: Pretty\n    runs-on: u\n"),
            "two axes": ("name: x\non:\n  pull_request:\njobs:\n  a:\n    strategy:\n"
                         "      matrix:\n        python: ['3.12']\n        os: [a]\n"),
            "matrix include": ("name: x\non:\n  pull_request:\njobs:\n  a:\n"
                               "    strategy:\n      matrix:\n        include:\n"
                               "          - python: '3.12'\n"),
            "unknown pr key": ("name: x\non:\n  pull_request:\n    foo: bar\njobs:\n"
                               "  a:\n    runs-on: u\n"),
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(gate.WorkflowFormatError):
                    self.collect({"x.yml": text})

    def test_missing_gate_job_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            write_workflows(directory, {"a.yml": "name: a\non:\n  pull_request:\n"
                                                  "jobs:\n  a:\n    runs-on: u\n"})
            with self.assertRaisesRegex(gate.WorkflowFormatError, "ci-gate"):
                gate.collect_check_names(directory)


class EvaluateTests(unittest.TestCase):
    EXPECTED = ["a", "b (3.12)"]

    def test_all_expected_success_passes(self):
        state, names = gate.evaluate(self.EXPECTED, [run("a"), run("b (3.12)", run_id=2)])
        self.assertEqual(state, "pass")
        self.assertEqual(names, ["a", "b (3.12)"])

    def test_missing_expected_is_pending_not_pass(self):
        state, messages = gate.evaluate(self.EXPECTED, [run("a")])
        self.assertEqual(state, "pending")
        self.assertIn("missing: b (3.12)", messages)

    def test_failed_expected_fails(self):
        state, messages = gate.evaluate(
            self.EXPECTED, [run("a", "failure"), run("b (3.12)", run_id=2)])
        self.assertEqual(state, "fail")
        self.assertEqual(messages, ["a: failure"])

    def test_skipped_or_cancelled_is_not_success(self):
        for conclusion in ("skipped", "cancelled", "neutral", "timed_out"):
            with self.subTest(conclusion=conclusion):
                state, _ = gate.evaluate(["a"], [run("a", conclusion)])
                self.assertEqual(state, "fail")

    def test_latest_rerun_attempt_wins(self):
        runs = [run("a", "failure", run_id=1), run("a", "success", run_id=5),
                run("b (3.12)", run_id=2)]
        self.assertEqual(gate.evaluate(self.EXPECTED, runs)[0], "pass")
        runs = [run("a", "success", run_id=1), run("a", "failure", run_id=5),
                run("b (3.12)", run_id=2)]
        self.assertEqual(gate.evaluate(self.EXPECTED, runs)[0], "fail")

    def test_triggered_path_filtered_check_must_also_pass(self):
        base = [run("a"), run("b (3.12)", run_id=2)]
        self.assertEqual(
            gate.evaluate(self.EXPECTED, base + [run("extra", status="in_progress",
                                                     run_id=3)])[0], "pending")
        self.assertEqual(
            gate.evaluate(self.EXPECTED, base + [run("extra", "failure", run_id=3)])[0],
            "fail")

    def test_other_apps_and_the_gate_itself_are_ignored(self):
        runs = [run("a"), run("b (3.12)", run_id=2),
                run("a", "failure", run_id=9, app_id=999),
                run(gate.GATE_CHECK_NAME, status="in_progress", run_id=10)]
        self.assertEqual(gate.evaluate(self.EXPECTED, runs)[0], "pass")

    def test_same_name_from_other_app_cannot_satisfy_expected(self):
        state, _ = gate.evaluate(["a"], [run("a", app_id=999)])
        self.assertEqual(state, "pending")


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class WaitTests(unittest.TestCase):
    def wait(self, snapshots, **kwargs):
        clock = FakeClock()
        feed = iter(snapshots)
        last = {"value": []}

        def fetch():
            try:
                last["value"] = next(feed)
            except StopIteration:
                pass
            value = last["value"]
            if isinstance(value, Exception):
                raise value
            return value

        options = {"deadline_s": 300, "interval_s": 10, "settle_s": 20}
        options.update(kwargs)
        return gate.wait_for_checks(["a"], fetch, clock=clock, sleep=clock.sleep,
                                    log=lambda _: None, **options)

    def test_pass_requires_settle_period(self):
        state, _ = self.wait([[], [run("a")]])
        self.assertEqual(state, "pass")

    def test_late_failure_during_settle_fails(self):
        late = [run("a"), run("late", "failure", run_id=2)]
        state, messages = self.wait([[run("a")], [run("a")], late])
        self.assertEqual(state, "fail")
        self.assertEqual(messages, ["late: failure"])

    def test_missing_check_fails_at_deadline(self):
        state, messages = self.wait([[]], deadline_s=60)
        self.assertEqual(state, "fail")
        self.assertEqual(messages[0], "deadline reached")
        self.assertIn("missing: a", messages)

    def test_api_errors_are_retried_then_fail_closed(self):
        state, _ = self.wait([gate.urllib.error.URLError("down"), [run("a")]])
        self.assertEqual(state, "pass")
        state, messages = self.wait([gate.urllib.error.URLError("down")],
                                    deadline_s=30)
        self.assertEqual(state, "fail")
        self.assertTrue(any("API error" in m for m in messages))


class MainTests(unittest.TestCase):
    def test_list_mode_makes_no_api_calls(self):
        with patch.object(gate, "fetch_check_runs",
                          side_effect=AssertionError("no API in --list")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(gate.main(["--workflows", str(WORKFLOWS), "--list"]), 0)
        self.assertIn("test", out.getvalue().split())

    def test_missing_environment_fails_closed(self):
        with patch.dict("os.environ", {}, clear=True), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(gate.main(["--workflows", str(WORKFLOWS)]), 1)
        self.assertIn("required", err.getvalue())

    def test_pass_and_fail_exit_codes(self):
        expected, _ = gate.collect_check_names(WORKFLOWS)
        green = [run(name, run_id=i) for i, name in enumerate(expected, 1)]
        env = {"GITHUB_TOKEN": "t", "GITHUB_REPOSITORY": "o/r", "GATE_SHA": "abc"}
        args = ["--workflows", str(WORKFLOWS), "--settle", "0", "--interval", "0"]
        with patch.dict("os.environ", env, clear=True), \
                patch.object(gate, "fetch_check_runs", return_value=green), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(args), 0)
        red = green[:-1] + [run(expected[-1], "failure", run_id=999)]
        with patch.dict("os.environ", env, clear=True), \
                patch.object(gate, "fetch_check_runs", return_value=red), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(gate.main(args), 1)
        self.assertIn("failure", err.getvalue())


if __name__ == "__main__":
    unittest.main()


def make_tree(root, workflows, gate_script="script v1\n", gate_workflow=None):
    wf = Path(root) / ".github" / "workflows"
    wf.mkdir(parents=True)
    files = {"required-ci-gate.yml": gate_workflow or GATE_WORKFLOW, **workflows}
    write_workflows(wf, files)
    ci = Path(root) / "tools" / "ci"
    ci.mkdir(parents=True)
    (ci / "required_checks_gate.py").write_text(gate_script, encoding="utf-8")
    return Path(root)


TESTS_WORKFLOW = """\
name: tests
on:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
  lint:
    runs-on: ubuntu-latest
"""


class CompareTreesTests(unittest.TestCase):
    """--head-tree: the trusted base judges the PR head, which cannot weaken it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = make_tree(Path(self.tmp.name) / "base", {"tests.yml": TESTS_WORKFLOW})

    def head(self, workflows, **kwargs):
        return make_tree(Path(self.tmp.name) / "head", workflows, **kwargs)

    def test_identical_trees_pass_with_head_checks(self):
        head = self.head({"tests.yml": TESTS_WORKFLOW})
        expected, notices, problems = gate.compare_trees(self.base, head, "")
        self.assertEqual(expected, ["lint", "test"])
        self.assertEqual((notices, problems), ([], []))

    def test_new_head_workflow_is_required(self):
        extra = TESTS_WORKFLOW.replace("name: tests", "name: more").replace(
            "  test:", "  extra:").replace("  lint:", "  extra2:")
        head = self.head({"tests.yml": TESTS_WORKFLOW, "more.yml": extra})
        expected, _, problems = gate.compare_trees(self.base, head, "")
        self.assertEqual(problems, [])
        self.assertIn("extra", expected)

    def test_path_filter_added_in_pr_is_blocked(self):
        filtered = TESTS_WORKFLOW.replace("  pull_request:\n",
                                          "  pull_request:\n    paths:\n      - 'src/**'\n")
        head = self.head({"tests.yml": filtered})
        _, _, problems = gate.compare_trees(self.base, head, "")
        self.assertEqual(len(problems), 2)
        self.assertTrue(all("ci-gate-drop" in p for p in problems))

    def test_deleted_workflow_and_removed_job_are_blocked(self):
        _, _, problems = gate.compare_trees(self.base, self.head({}), "")
        self.assertEqual(len(problems), 2)
        only_test = TESTS_WORKFLOW.replace("  lint:\n    runs-on: ubuntu-latest\n", "")
        tmp2 = tempfile.TemporaryDirectory()
        self.addCleanup(tmp2.cleanup)
        head = make_tree(Path(tmp2.name) / "h", {"tests.yml": only_test})
        _, _, problems = gate.compare_trees(self.base, head, "")
        self.assertEqual(len(problems), 1)
        self.assertIn(": lint", problems[0])

    def test_explicit_drop_acknowledgement_allows_named_checks_only(self):
        head = self.head({})
        _, notices, problems = gate.compare_trees(
            self.base, head, "Remove tests.\r\nci-gate-drop: lint\n")
        self.assertEqual(notices, ["acknowledged drop: lint"])
        self.assertEqual(len(problems), 1)
        self.assertIn(": test", problems[0])
        _, _, problems = gate.compare_trees(self.base, head, "ci-gate-drop: lint, test")
        self.assertEqual(problems, [])

    def test_acknowledgement_must_be_its_own_line(self):
        head = self.head({})
        _, _, problems = gate.compare_trees(
            self.base, head, "see `ci-gate-drop: lint, test` in the docs")
        self.assertEqual(len(problems), 2)

    def test_gate_changes_need_explicit_acknowledgement(self):
        for kwargs in ({"gate_script": "script v2 always passes\n"},
                       {"gate_workflow": GATE_WORKFLOW + "# edited\n"}):
            with self.subTest(**{k: True for k in kwargs}):
                tmp2 = tempfile.TemporaryDirectory()
                self.addCleanup(tmp2.cleanup)
                head = make_tree(Path(tmp2.name) / "h", {"tests.yml": TESTS_WORKFLOW}, **kwargs)
                _, _, problems = gate.compare_trees(self.base, head, "")
                self.assertEqual(len(problems), 1)
                self.assertIn("changes the gate", problems[0])
                _, notices, problems = gate.compare_trees(
                    self.base, head, "ci-gate-change: acknowledged")
                self.assertEqual(problems, [])
                self.assertTrue(notices[0].startswith("acknowledged gate change"))

    def test_deleted_gate_script_counts_as_a_change(self):
        head = self.head({"tests.yml": TESTS_WORKFLOW})
        (head / "tools" / "ci" / "required_checks_gate.py").unlink()
        _, _, problems = gate.compare_trees(self.base, head, "")
        self.assertIn("changes the gate", problems[0])

    def test_unparseable_head_fails_closed(self):
        head = self.head({"tests.yml": "on: [pull_request]\njobs:\n  x:\n    a: 1\n"})
        with self.assertRaisesRegex(gate.WorkflowFormatError, "PR head"):
            gate.compare_trees(self.base, head, "")

    def test_main_blocks_before_any_api_call_and_reads_body_from_event(self):
        head = self.head({})
        event = Path(self.tmp.name) / "event.json"
        event.write_text('{"pull_request": {"body": null}}', encoding="utf-8")
        argv = ["--base-tree", str(self.base), "--head-tree", str(head),
                "--event-path", str(event), "--sha", "abc", "--repository", "o/r"]
        stderr = io.StringIO()
        with patch.object(gate, "fetch_check_runs") as fetch, \
                patch.dict("os.environ", {"GITHUB_TOKEN": "t"}), \
                contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(argv), 1)
        fetch.assert_not_called()
        self.assertIn("ci-gate-drop: lint", stderr.getvalue())


class GateWorkflowTrustTests(unittest.TestCase):
    """The pull_request_target run must never execute PR-controlled content."""

    source = (WORKFLOWS / "required-ci-gate.yml").read_text(encoding="utf-8")

    def test_runs_from_the_base_on_pull_request_target(self):
        self.assertRegex(self.source, r"(?m)^  pull_request_target:\s*$")
        self.assertNotRegex(self.source, r"(?m)^  pull_request:\s*$")
        self.assertNotIn("event_name", self.source)
        self.assertIn("edited", self.source)
        self.assertIn("--head-tree", self.source)

    def test_pr_head_is_never_checked_out_or_interpolated_into_shell(self):
        self.assertNotRegex(self.source, r"(?m)^\s*ref:")
        self.assertIn("persist-credentials: false", self.source)
        self.assertNotIn("secrets.", self.source)
        for line in self.source.splitlines():
            if "${{" in line:
                with self.subTest(line=line.strip()):
                    self.assertNotRegex(line, r"^\s*(run:|git |python |test |mkdir )")
                    self.assertNotIn("pull_request.body", line)
                    self.assertNotIn("head.ref", line)
                    self.assertNotIn("pull_request.title", line)

    def test_head_tree_is_only_archived_not_executed(self):
        self.assertIn("git archive refs/remotes/pr/head .github/workflows tools/ci", self.source)
        self.assertNotIn("$RUNNER_TEMP/pr-head/", self.source.replace(
            '"$RUNNER_TEMP/pr-head"', ""))


def suite_run(name, conclusion="success", run_id=1, suite=1, status="completed"):
    item = run(name, conclusion, status, run_id)
    item["check_suite"] = {"id": suite}
    return item


class SuiteGroupingTests(unittest.TestCase):
    def test_newer_success_in_another_suite_does_not_hide_a_failure(self):
        runs = [suite_run("test", "failure", run_id=1, suite=10),
                suite_run("test", "success", run_id=2, suite=20)]
        state, messages = gate.evaluate(["test"], runs)
        self.assertEqual(state, "fail")
        self.assertEqual(messages, ["test: failure"])

    def test_rerun_within_the_same_suite_still_wins(self):
        runs = [suite_run("test", "failure", run_id=1, suite=10),
                suite_run("test", "success", run_id=2, suite=10)]
        self.assertEqual(gate.evaluate(["test"], runs)[0], "pass")

    def test_running_attempt_in_any_suite_is_pending(self):
        runs = [suite_run("test", "success", run_id=1, suite=10),
                suite_run("test", None, run_id=2, suite=20, status="in_progress")]
        self.assertEqual(gate.evaluate(["test"], runs)[0], "pending")


PATHS_WORKFLOW = """\
name: receipt
on:
  pull_request:
    paths:
      - 'qa_lab/receipt_cost.py'
      - 'experiments/rea/**'
      - '!experiments/rea/README.md'
  workflow_dispatch:
jobs:
  receipt-cost:
    runs-on: ubuntu-latest
"""


class PathFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, files):
        write_workflows(self.dir, {"required-ci-gate.yml": GATE_WORKFLOW, **files})

    def test_glob_semantics(self):
        rx = gate.glob_regex
        self.assertTrue(rx("tools/**").match("tools/a/b.py"))
        self.assertTrue(rx("*.md").match("README.md"))
        self.assertFalse(rx("*.md").match("docs/README.md"))
        self.assertTrue(rx("a?c").match("abc"))
        self.assertFalse(rx("qa_lab/api.py").match("qa_lab/api.pyc"))
        for bad in ("src/[ab].py", "src/{a,b}.py"):
            with self.assertRaises(ValueError):
                rx(bad)

    def test_paths_trigger_and_negation(self):
        self.write({"receipt.yml": PATHS_WORKFLOW})
        for changed, expected in ((["qa_lab/receipt_cost.py"], ["receipt-cost"]),
                                  (["experiments/rea/x/y.js"], ["receipt-cost"]),
                                  (["experiments/rea/README.md"], []),
                                  (["qa_lab/core.py"], []),
                                  ([], [])):
            with self.subTest(changed=changed):
                required, notices = gate.triggered_path_checks(self.dir, changed)
                self.assertEqual((required, notices), (expected, []))

    def test_paths_ignore(self):
        ignore = PATHS_WORKFLOW.replace("    paths:\n", "    paths-ignore:\n")
        self.write({"receipt.yml": ignore})
        self.assertEqual(gate.triggered_path_checks(self.dir, ["qa_lab/receipt_cost.py"])[0], [])
        self.assertEqual(gate.triggered_path_checks(
            self.dir, ["qa_lab/receipt_cost.py", "qa_lab/core.py"])[0], ["receipt-cost"])

    def test_other_filters_and_unsupported_syntax_are_not_evaluated(self):
        branches = PATHS_WORKFLOW.replace("    paths:\n", "    branches: [main]\n    paths:\n")
        braces = PATHS_WORKFLOW.replace("'experiments/rea/**'", "'src/{a,b}.py'")
        self.write({"receipt.yml": branches})
        self.assertEqual(gate.triggered_path_checks(self.dir, ["qa_lab/receipt_cost.py"]), ([], []))
        self.write({"receipt.yml": braces})
        required, notices = gate.triggered_path_checks(self.dir, ["qa_lab/receipt_cost.py"])
        self.assertEqual(required, [])
        self.assertIn("not evaluated", notices[0])

    def test_large_diffs_are_not_evaluated(self):
        self.write({"receipt.yml": PATHS_WORKFLOW})
        files = ["qa_lab/receipt_cost.py"] + [f"f{i}" for i in range(299)]
        required, notices = gate.triggered_path_checks(self.dir, files)
        self.assertEqual(required, [])
        self.assertIn("300 changed files", notices[0])

    def test_repository_path_filters_all_parse(self):
        required, notices = gate.triggered_path_checks(WORKFLOWS, ["qa_lab/receipt_cost.py"])
        self.assertEqual(notices, [])
        self.assertIn("receipt-cost", required)
        required, _ = gate.triggered_path_checks(WORKFLOWS, ["qa_lab/api.py"])
        self.assertIn("guard", required)

    def test_main_requires_triggered_path_checks_in_head_tree_mode(self):
        base = make_tree(self.dir / "base", {"tests.yml": TESTS_WORKFLOW, "receipt.yml": PATHS_WORKFLOW})
        head = make_tree(self.dir / "head", {"tests.yml": TESTS_WORKFLOW, "receipt.yml": PATHS_WORKFLOW})
        event = self.dir / "event.json"
        event.write_text('{"pull_request": {"number": 7, "body": ""}}', encoding="utf-8")
        seen = {}

        def wait(expected, fetch, **kwargs):
            seen["expected"] = expected
            return "pass", expected

        argv = ["--base-tree", str(base), "--head-tree", str(head), "--event-path", str(event),
                "--sha", "abc", "--repository", "o/r"]
        with patch.object(gate, "fetch_changed_files", return_value=["qa_lab/receipt_cost.py"]) as files, \
                patch.object(gate, "wait_for_checks", side_effect=wait), \
                patch.dict("os.environ", {"GITHUB_TOKEN": "t"}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(argv), 0)
        files.assert_called_once_with("o/r", 7, "t")
        self.assertEqual(seen["expected"], ["lint", "receipt-cost", "test"])
