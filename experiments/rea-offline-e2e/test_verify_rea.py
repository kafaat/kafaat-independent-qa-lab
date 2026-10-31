"""Negative contracts for REA's recorded static-output readback."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from verify_rea import (ProbeBlocked, MAX_ANALYSIS, _load_json_bytes,
                        _read_bounded, inspect_fixture, assess)

IMAGE = "sha256:" + "f" * 64
SHA = "a" * 40
ROOT = Path(__file__).resolve().parent / "fixtures" / "app"


class StaticReadbackContract(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.fixture = self.path / "fixture"
        self.fixture.mkdir()
        for name in ("main.js", "pricing.js", "package.json"):
            shutil.copyfile(ROOT / name, self.fixture / name)
        self.analysis = self.path / "analysis.json"
        self.analysis.write_text(json.dumps({"modules": [{"path": "main.js"},
                                                        {"path": "pricing.js"}]}))

    def validated(self):
        return assess(self.analysis, self.fixture, IMAGE, SHA)

    def test_review_only_for_well_formed_observation(self):
        result = self.validated()
        self.assertEqual(result["status"], "OBSERVED_FOR_REVIEW")
        self.assertEqual(result["rea_version"], "6.8.0")
        self.assertEqual(set(result["fixture_sha256"]), {"main.js", "pricing.js", "package.json"})
        self.assertFalse(result["network_enabled"])
        self.assertFalse(result["source_attested_independently"])
        self.assertFalse(result["production_accepted"])

    def test_missing_module_is_not_accepted(self):
        self.analysis.write_text('{"modules":[{"path":"main.js"}]}')
        with self.assertRaisesRegex(ProbeBlocked, "REQUIRED_MODULES_NOT_OBSERVED"):
            self.validated()

    def test_duplicate_json_fields_and_nan_fail_closed(self):
        for data in (b'{"modules":[],"modules":[]}', b'{"modules":NaN}'):
            with self.subTest(data=data):
                with self.assertRaises(ProbeBlocked):
                    _load_json_bytes(data)

    def test_large_analysis_rejected_without_unbounded_read(self):
        with self.analysis.open("wb") as f:
            f.truncate(MAX_ANALYSIS * 10)
        with patch("pathlib.Path.read_bytes", side_effect=AssertionError("unbounded read")):
            with self.assertRaisesRegex(ProbeBlocked, "OVERSIZED_INPUT"):
                self.validated()

    def test_symlinked_analysis_rejected(self):
        self.analysis.unlink()
        self.analysis.symlink_to(ROOT / "main.js")
        with self.assertRaisesRegex(ProbeBlocked, "UNSAFE_FILE"):
            self.validated()

    def test_missing_source_rejected(self):
        (self.fixture / "pricing.js").unlink()
        with self.assertRaises((OSError, ProbeBlocked)):
            self.validated()

    def test_modified_package_name_refused(self):
        (self.fixture / "package.json").write_text('{"name":"unexpected"}')
        with self.assertRaisesRegex(ProbeBlocked, "UNREVIEWED_FIXTURE"):
            self.validated()

    def test_image_version_and_sha_are_pinned(self):
        for image, sha, version in ((IMAGE, SHA[:-1] + "X", "6.8.0"),
                                    ("sha256:123", SHA, "6.8.0"),
                                    (IMAGE, SHA, "latest")):
            with self.subTest(image=image, sha=sha):
                with self.assertRaisesRegex(ProbeBlocked, "INVALID_EXECUTION_IDENTITY"):
                    assess(self.analysis, self.fixture, image, sha, tool_version=version)

    def test_cli_keeps_observation_without_adoption_authority(self):
        output = self.path / "receipt.json"
        process = subprocess.run([sys.executable, str(Path(__file__).with_name("verify_rea.py")),
                                  "--analysis", str(self.analysis),
                                  "--fixture", str(self.fixture),
                                  "--image-digest", IMAGE, "--source-sha", SHA,
                                  "--out", str(output)],
                                 text=True, capture_output=True, timeout=15)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        data = json.loads(output.read_text())
        self.assertEqual(data["status"], "OBSERVED_FOR_REVIEW")
        self.assertFalse(data["hospital_admission"])
        self.assertFalse(data["production_accepted"])
        self.assertFalse(data["source_attested_independently"])


if __name__ == "__main__":
    unittest.main()
