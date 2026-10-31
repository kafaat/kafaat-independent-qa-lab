"""Fail-closed Railway restarts: no live Railway access or volume changes."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from qa_lab.railway_restart_preflight import (
    InventoryRejected, REQUIRED_MOUNTS, REQUIRED_SERVICES, assess, read_json,
)


def fixture():
    return {
        "project_id": "883c38a4-9e14-47be-8fd2-c4377192da81",
        "environment_id": "4dfcdb2c-7377-4321-9793-527a111784bc",
        "services": [
            {"name": name, "latest_deployment_status": "SUCCESS"}
            for name in sorted(REQUIRED_SERVICES)
        ],
        "volumes": [
            {"name": name, "state": "live", "service_name": service, "mount_path": mount}
            for name, (service, mount) in REQUIRED_MOUNTS.items()
        ],
        "staged_changes": False,
    }


class RestartGateTests(unittest.TestCase):
    def test_healthy_inventory_never_grants_restart(self):
        result = assess(fixture())
        self.assertEqual(result["status"], "REVIEW_REQUIRED")
        self.assertFalse(result["restart_allowed"])
        self.assertFalse(result["volume_contents_verified"])
        self.assertFalse(result["backup_restore_verified"])
        self.assertFalse(result["removal_actor_verified"])
        self.assertIn("BACKUP_AND_ACTOR_EVIDENCE_REQUIRED", result["reasons"])

    def test_offline_environment_is_rejected(self):
        doc = fixture()
        doc["services"][0]["latest_deployment_status"] = None
        self.assertIn("SERVICE_NOT_RUNNING", assess(doc)["reasons"])

    def test_missing_postgres_volume_is_rejected(self):
        doc = fixture()
        doc["volumes"] = [v for v in doc["volumes"] if v["name"] != "postgres-volume"]
        self.assertIn("VOLUME_INVENTORY_INCOMPLETE", assess(doc)["reasons"])

    def test_volume_remount_to_wrong_service_or_path_rejected(self):
        for key, value in (("service_name", "qa-api"), ("mount_path", "/tmp"), ("state", "staged-create")):
            with self.subTest(key=key):
                doc = fixture()
                doc["volumes"][0][key] = value
                self.assertIn("VOLUME_MOUNT_MISMATCH", assess(doc)["reasons"])

    def test_duplicate_or_unknown_services_are_rejected(self):
        doc = fixture()
        doc["services"][0] = copy.deepcopy(doc["services"][1])
        self.assertIn("DUPLICATE_SERVICES", assess(doc)["reasons"])
        self.assertIn("SERVICE_INVENTORY_MISMATCH", assess(doc)["reasons"])

    def test_staged_patch_must_not_be_ignored(self):
        doc = fixture()
        doc["staged_changes"] = {"action": "delete-volume"}
        self.assertIn("PENDING_ENVIRONMENT_PATCH", assess(doc)["reasons"])

    def test_cannot_supply_self_attested_grants(self):
        doc = fixture()
        doc["backup_verified"] = True
        self.assertIn("INVALID_INVENTORY_SHAPE", assess(doc)["reasons"])

    def test_wrong_environment_is_rejected(self):
        doc = fixture()
        doc["environment_id"] = "production"
        self.assertIn("PROJECT_OR_ENVIRONMENT_MISMATCH", assess(doc)["reasons"])

    def test_duplicate_and_nonfinite_json_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "inventory.json"
            for raw, reason in (
                ('{"x": 1, "x": 2}', "DUPLICATE_JSON_KEY"),
                ('{"x": NaN}', "NONFINITE_JSON"),
            ):
                p.write_text(raw)
                with self.assertRaisesRegex(InventoryRejected, reason):
                    read_json(p)

    def test_large_input_rejected_before_materializing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sparse.json"
            with p.open("wb") as stream:
                stream.truncate(12 * 1024 * 1024)
            with self.assertRaisesRegex(InventoryRejected, "INVALID_INPUT_SIZE"):
                read_json(p)

    def test_cli_exit_code_one_for_review_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "inventory.json"
            p.write_text(json.dumps(fixture()))
            run = subprocess.run(
                [sys.executable, "-m", "qa_lab.railway_restart_preflight", "--inventory", str(p)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(run.returncode, 1)
            self.assertFalse(json.loads(run.stdout)["restart_allowed"])

    def test_cli_fails_closed_for_bad_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "inventory.json"
            p.write_text("{}")
            run = subprocess.run(
                [sys.executable, "-m", "qa_lab.railway_restart_preflight", "--inventory", str(p)],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(run.returncode, 2)
            self.assertEqual(json.loads(run.stdout)["status"], "REJECTED")


if __name__ == "__main__":
    unittest.main()
