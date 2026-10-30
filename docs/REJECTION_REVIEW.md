# Read-only rejection review

Extends the existing PostgreSQL dashboard. Authentication and private-only
operating assumptions are unchanged. The dashboard token is an environment-wide
operator credential: this is **not** per-project or per-user access control.

## Routes

- `GET /rejections`: Arabic HTML metadata list with reason/stream filters.
- `GET /rejections/{rejection_id}`: escaped metadata details.
- `GET /api/rejections`: bounded JSON list.
- `GET /api/rejections/{rejection_id}`: JSON metadata details.

All four require the existing `QA_DASHBOARD_TOKEN` Bearer credential. Root run
navigation links to review. This does not introduce browser sessions or login.
Allowed list filters are `reason`, `stream`, `limit` (1–100), and `after` (64-char
lowercase SHA256 rejection identity). Duplicate and unknown filters are rejected.
Pagination is descending rejection fingerprint, explicitly **not chronological**.
`page_count` counts the returned page, not all records or system health.

## Data and safety

Reads `qa_nats_rejections`, created by consumer PR #20. No DDL, replay, deletion,
acknowledgement, disposition mutation, or state-changing HTTP endpoint is added.
Read-only database transactions use a three-second statement timeout. SQL
parameters are bound. Only explicit metadata columns are selected. There are no
raw message bodies, headers, credentials or exception strings in the response.
Hash fingerprints are evidence references, not signatures or anonymization.

The table missing, database failure, or invalid stored metadata returns a
sanitized HTTP 503, never a healthy empty result. Missing valid IDs return 404;
invalid IDs/filters return 400. Existing dashboard no-store/CSP/security headers
and token validation apply. HTML escapes all displayed metadata.

## Acceptance and rollout

35 unit/loopback HTTP tests and 5 isolated real PostgreSQL 18 tests are required
by the dedicated workflow. The latter seeds the actual production audit schema
and uses its writer, checks filters/pagination, denies a write transaction,
verifies HTTP, and proves reads leave audit rows unchanged. Dedicated jobs reject
skips and unraisable resource errors. Generic no-database suites may explicitly
skip the five live tests; that does not replace the dedicated live job.

First deploy the audit-enabled consumer and confirm its table, then deploy the
dashboard commit. A missing table fails closed without touching the runs UI.
Verify unauthorized requests are rejected and authorized metadata is visible.
No public domain or token changes are needed. Production readiness, alerting,
retention, replay, source attestation and full legacy parity remain out of scope.
