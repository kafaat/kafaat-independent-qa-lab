# Railway experimental recovery: offline evidence preflight

This new stdlib-only tool evaluates **sanitized configuration inventories**, not live Railway data or backups. It never calls Railway APIs, changes volumes, restarts containers or approves a deployment. It is deliberately **not** a recovery mechanism.

A human operator may export a non-secret JSON inventory containing the exact `project_id`, `environment_id`, and 11 service names with latest deployment statuses, plus exactly two volume mounts (`postgres-volume` on PostgreSQL at `/var/lib/postgresql/data`, and `nats-jetstream-data` on `qa-nats` at `/data`). A machine-readable `staged_changes` flag must be false or null.

Run:

```bash
python -m qa_lab.railway_restart_preflight --inventory /private/sanitized-inventory.json
python -m unittest discover -s tests -p test_railway_restart_preflight.py -v
```

Fail-closed status is `REJECTED` (exit 2). Even with all eleven services online and mounts structurally correct, result is **`REVIEW_REQUIRED` (exit 1), with `restart_allowed=false`** because a non-secret config inventory cannot attest any of:

- Who removed the 11 deployments around 15:04 UTC and whether it was authorized.
- Actual bytes of PostgreSQL/JetStream volumes after the shutdown.
- A backup that can be restored and matched to the relevant source/revisions.
- Approval of a nondestructive recovery sequence and current E2E/receipt results.

Existing `kafaat/kafaat-independent-qa-lab#36` remains the incident source of truth. This tool introduces no secret/token variable, health endpoint, or production exception and does not waive Hospital `DB-OBS-001` or TestRun/Lease admission controls. No operator should infer live restart permission from these syntactic checks. Before any recovery, obtain authorized Railway audit evidence, verify volume snapshots/backups independently, then explicitly plan phased startup and new signed E2E receipts.
