"""Offline unit tests for fail-closed source-detector comparison."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

from verify_ui import (MAX_JSON, UIReviewBlocked, read_file, parse_json,
                       findings_count, evaluate)

SHA = "a" * 40
IMAGE = "sha256:" + "b" * 64
ROOT = Path(__file__).resolve().parent


class ImpeccableReadbackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.original = self.root / "baseline.html"
        self.fixed = self.root / "candidate.html"
        shutil.copyfile(ROOT / "fixtures/baseline/index.html", self.original)
        shutil.copyfile(ROOT / "fixtures/candidate/index.html", self.fixed)
        self.bjson = self.root / "baseline.json"
        self.cjson = self.root / "candidate.json"
        self.bjson.write_text(json.dumps({"findings": [{"ruleId": "baseline-a"},
                                                       {"ruleId": "baseline-b"}]}))
        self.cjson.write_text('{"findings":[]}')

    def checked(self, before=2, after=0, image=IMAGE, sha=SHA):
        return evaluate(self.bjson, self.cjson, self.original, self.fixed,
                        before, after, image, sha)

    def test_valid_measurement_is_review_only(self):
        result = self.checked()
        self.assertEqual(result["status"], "OBSERVED_UI_IMPROVEMENT")
        self.assertEqual((result["baseline_primary_findings"],
                          result["candidate_primary_findings"]), (2, 0))
        self.assertFalse(result["wcag_conformance_proven"])
        self.assertFalse(result["production_accepted"])
        self.assertFalse(result["sahool_functional_acceptance"])

    def test_reject_no_finding_difference(self):
        self.cjson.write_text(self.bjson.read_text())
        with self.assertRaisesRegex(UIReviewBlocked, "NO_MEASURABLE"):
            self.checked(after=2)

    def test_reject_status_mismatch(self):
        with self.assertRaisesRegex(UIReviewBlocked, "DETECTOR_EXECUTION_NOT_PROVEN"):
            self.checked(before=0)
        with self.assertRaisesRegex(UIReviewBlocked, "DETECTOR_STATUS_CONFLICT"):
            self.checked(after=2)

    def test_reject_invalid_json_nonfinite_and_duplicate_keys(self):
        for value in (b'{"findings":[],"findings":[]}', b'{"findings":NaN}'):
            with self.subTest(value=value):
                with self.assertRaises(UIReviewBlocked):
                    parse_json(value)

    def test_reject_large_file_before_unbounded_read(self):
        with self.bjson.open("wb") as f:
            f.truncate(MAX_JSON * 5)
        with self.assertRaisesRegex(UIReviewBlocked, "INPUT_TOO_LARGE"):
            self.checked()

    def test_reject_symlink_analysis(self):
        self.bjson.unlink()
        self.bjson.symlink_to(self.cjson)
        with self.assertRaisesRegex(UIReviewBlocked, "UNSAFE_INPUT"):
            self.checked()

    def test_reject_invalid_source_and_image(self):
        with self.assertRaisesRegex(UIReviewBlocked, "INVALID_EXECUTION_IDENTITY"):
            self.checked(image="node:24")
        with self.assertRaisesRegex(UIReviewBlocked, "INVALID_EXECUTION_IDENTITY"):
            self.checked(sha="not-a-commit")

    def test_reject_unrecognized_detector_shape(self):
        self.bjson.write_text('{"unexpected":[]}')
        with self.assertRaisesRegex(UIReviewBlocked, "UNSUPPORTED_DETECTOR_JSON"):
            self.checked()

    def test_nested_detector_results_can_be_counted(self):
        value = {"files": [{"findings": [{"id": "a"}]}, {"findings": [{"id": "b"}]}]}
        self.assertEqual(findings_count(value), 2)


if __name__ == "__main__":
    unittest.main()
