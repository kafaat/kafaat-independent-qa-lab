"""The mutation-control harness counts only assertion failures as kills."""
import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from tools.mutation import verify_mutations as vm

ROOT = Path(__file__).resolve().parents[1]

MODULE = "def add(a, b):\n    # sum\n    return a + b\n"
TEST = textwrap.dedent("""\
    import unittest
    from mod import add

    class AddTests(unittest.TestCase):
        def test_add(self):
            self.assertEqual(add(2, 3), 5)
    """)


def control(cid, old, new, path="mod.py"):
    return {"id": cid, "path": path, "old": old, "new": new, "test": "test_mod",
            "guards": "example"}


class HarnessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "mod.py").write_text(MODULE, encoding="utf-8")
        (self.root / "test_mod.py").write_text(TEST, encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)

    def statuses(self, controls):
        result = vm.verify(self.root, controls, timeout=60)
        return result, {row["id"]: row["status"] for row in result["controls"]}

    def test_killed_survived_and_invalid_mutants(self):
        result, statuses = self.statuses([
            control("wrong-sum", "return a + b", "return a - b"),
            control("comment-only", "# sum", "# total"),
            control("import-error", "def add(a, b):", "import missing_module\ndef add(a, b):"),
        ])
        self.assertEqual(statuses, {"wrong-sum": "KILLED", "comment-only": "SURVIVED_OR_INVALID",
                                    "import-error": "SURVIVED_OR_INVALID"})
        self.assertEqual(result["status"], "FAILED")
        self.assertTrue(result["source_unchanged"])
        self.assertEqual((self.root / "mod.py").read_text(encoding="utf-8"), MODULE)

    def test_all_killed_passes(self):
        result, _ = self.statuses([control("wrong-sum", "return a + b", "return a * b")])
        self.assertEqual(result["status"], "PASSED")

    def test_failing_baseline_launches_no_mutants(self):
        (self.root / "mod.py").write_text(MODULE.replace("a + b", "a - b"), encoding="utf-8")
        result, statuses = self.statuses([control("wrong-sum", "return a - b", "return a * b")])
        self.assertEqual((result["status"], statuses), ("FAILED", {}))

    def test_non_unique_anchor_is_not_a_kill(self):
        result, statuses = self.statuses([control("ambiguous", "a", "b")])
        self.assertEqual(statuses, {"ambiguous": "ANCHOR_NOT_UNIQUE"})
        self.assertEqual(result["status"], "FAILED")

    def test_control_list_validation(self):
        path = self.root / "controls.json"
        for bad in ([], [{"id": "x"}], [control("a", "x", "x")],
                    [control("a", "x", "y", path="../escape.py")],
                    [control("a", "x", "y"), control("a", "x", "z")]):
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValueError):
                vm.load_controls(path)


class RepositoryControlsTests(unittest.TestCase):
    def test_every_repository_control_anchor_is_unique_and_test_exists(self):
        controls = vm.load_controls(ROOT / "tools" / "mutation" / "controls.json")
        self.assertGreaterEqual(len(controls), 8)
        for item in controls:
            with self.subTest(control=item["id"]):
                text = (ROOT / item["path"]).read_text(encoding="utf-8")
                self.assertEqual(text.count(item["old"]), 1)
                module = item["test"].split(".")[1]
                self.assertTrue((ROOT / "tests" / f"{module}.py").is_file())


if __name__ == "__main__":
    unittest.main()
