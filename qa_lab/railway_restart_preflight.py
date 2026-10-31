"""Offline Railway restart preflight: reports blockers, never authorizes deployment.

Accepts sanitized metadata exported by an operator. It cannot inspect Railway
volume bytes, backups, secrets, removal actors or authorization records.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

MAX_INPUT = 256 * 1024
REQUIRED_SERVICES = frozenset({
    "qa-api", "qa-worker", "qa-outbox-relay", "qa-nats-consumer",
    "qa-nats", "qa-dashboard", "qa-e2e-probe", "qa-knowledge",
    "qa-knowledge-probe", "qa-acceptance-receipt-audit", "Postgres",
})
REQUIRED_MOUNTS = {
    "postgres-volume": ("Postgres", "/var/lib/postgresql/data"),
    "nats-jetstream-data": ("qa-nats", "/data"),
}


class InventoryRejected(ValueError):
    pass


def read_json(path: str | Path) -> object:
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_INPUT + 1)
    if not raw or len(raw) > MAX_INPUT:
        raise InventoryRejected("INVALID_INPUT_SIZE")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InventoryRejected("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              InventoryRejected("NONFINITE_JSON")))
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, InventoryRejected):
            raise
        raise InventoryRejected("MALFORMED_JSON") from exc


def blocked(reasons: list[str], valid: bool) -> dict:
    """Report-only: even perfect inventory cannot grant restart authority."""
    return {
        "format": "qa.railway-restart-evidence-preflight.v1",
        "status": "REVIEW_REQUIRED" if valid else "REJECTED",
        "reasons": sorted(set(reasons)),
        "restart_allowed": False,
        "volume_contents_verified": False,
        "backup_restore_verified": False,
        "removal_actor_verified": False,
        "operational_authority_verified": False,
        "production_accepted": False,
        "requires": [
            "PROVEN_REMOVAL_AUTHORIZATION",
            "VOLUME_CONTENTS_READONLY_VERIFICATION",
            "RESTORABLE_BACKUP_RECEIPTS",
            "APPROVED_NONDESTRUCTIVE_RESTART_PLAN",
            "FRESH_E2E_AND_RECEIPT_AUDIT",
        ],
    }


def assess(document: object) -> dict:
    if not isinstance(document, dict) or set(document) != {
        "project_id", "environment_id", "services", "volumes", "staged_changes"
    }:
        return blocked(["INVALID_INVENTORY_SHAPE"], False)
    if (document["project_id"] != "883c38a4-9e14-47be-8fd2-c4377192da81"
            or document["environment_id"] != "4dfcdb2c-7377-4321-9793-527a111784bc"):
        return blocked(["PROJECT_OR_ENVIRONMENT_MISMATCH"], False)
    services, volumes = document["services"], document["volumes"]
    if not isinstance(services, list) or not isinstance(volumes, list):
        return blocked(["INVALID_COLLECTION_TYPE"], False)
    service_names = []
    reasons = []
    for row in services:
        if not isinstance(row, dict) or set(row) != {"name", "latest_deployment_status"}:
            return blocked(["INVALID_SERVICE_ROW"], False)
        if not isinstance(row["name"], str) or type(row["latest_deployment_status"]) not in (str, type(None)):
            return blocked(["INVALID_SERVICE_ROW"], False)
        service_names.append(row["name"])
    if len(service_names) != len(REQUIRED_SERVICES) or set(service_names) != REQUIRED_SERVICES:
        reasons.append("SERVICE_INVENTORY_MISMATCH")
    if len(service_names) != len(set(service_names)):
        reasons.append("DUPLICATE_SERVICES")
    for row in services:
        if row["latest_deployment_status"] != "SUCCESS":
            reasons.append("SERVICE_NOT_RUNNING")
    seen = set()
    for row in volumes:
        if not isinstance(row, dict) or set(row) != {"name", "state", "service_name", "mount_path"}:
            return blocked(["INVALID_VOLUME_ROW"], False)
        name = row["name"]
        if not isinstance(name, str) or name in seen or name not in REQUIRED_MOUNTS:
            reasons.append("UNKNOWN_OR_DUPLICATE_VOLUME")
            continue
        seen.add(name)
        expected_service, expected_mount = REQUIRED_MOUNTS[name]
        if row["state"] != "live" or row["service_name"] != expected_service or row["mount_path"] != expected_mount:
            reasons.append("VOLUME_MOUNT_MISMATCH")
    if seen != set(REQUIRED_MOUNTS):
        reasons.append("VOLUME_INVENTORY_INCOMPLETE")
    if document["staged_changes"] not in (False, None):
        reasons.append("PENDING_ENVIRONMENT_PATCH")
    if reasons:
        return blocked(reasons, False)
    # Do not allow inventory metadata to impersonate backups, actor audit, or owner sign-off.
    return blocked(["BACKUP_AND_ACTOR_EVIDENCE_REQUIRED"], True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    args = parser.parse_args()
    try:
        report = assess(read_json(args.inventory))
    except (InventoryRejected, OSError) as exc:
        report = blocked([str(exc) if isinstance(exc, InventoryRejected) else "INVENTORY_UNAVAILABLE"], False)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report["status"] == "REVIEW_REQUIRED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
