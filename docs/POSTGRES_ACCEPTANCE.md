# PostgreSQL queue acceptance boundary

The optional PostgreSQL adapter in this PR is experimental and **not wired** into the SQLite API or Worker.

## Verified in GitHub Actions
- PostgreSQL 16 real service
- idempotent submit and key conflict handling
- two-worker atomic claim via SKIP LOCKED
- owner-only terminal transition
- process exit after committed claim
- SIGKILL after committed claim and manual reconciliation
- no automatic replay of RUNNING jobs

## Not yet verified
- actual API/Worker PostgreSQL integration
- multi-host distributed queue and network partition
- PostgreSQL restart/failover and power loss
- Railway deployment, Docker application image, NATS
- AI/RAG or production acceptance

A RUNNING job is an unresolved external outcome. Operators must reconcile it before any retry. A successful CI run must not be interpreted as an approved release.
