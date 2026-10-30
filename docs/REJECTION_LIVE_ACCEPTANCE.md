# Synthetic rejection -> durable audit -> operator HTTP review

This one-shot probe closes the Railway acceptance gap left after PR #21. It
checks actual deployed services, not a dashboard fixture or a direct SQL insert.

The probe first verifies authentication, invalid-filter rejection, refused POST,
security headers and audit storage readiness. It then publishes **exactly one**
harmless malformed JSON message on `qa.jobs.created`, with a unique probe marker.
It requires existing file-backed `QA_JOBS` and existing `qa-audit-inbox`; it creates
neither. The broker's PubAck sequence plus raw content hash and subject hash bind
an independently calculated rejection ID to the actual consumer's PostgreSQL row.

The probe uses only SELECT inside read-only, statement-bounded SQL transactions.
It compares all 14 metadata fields with JSON detail and the filtered/keyset list,
checks exclusive cursors, Arabic HTML list/detail visibility, absence of the raw
synthetic marker, and unchanged audit data after HTTP reads. No original event,
job, rejection or production data is modified/deleted. The synthetic audit entry
is intentionally retained in REVIEW_REQUIRED as execution evidence, not quietly
removed. The receipt identifies it by rejection ID and SHA256, without raw body,
headers, token or exception text.

## Execution limits

`python -m qa_lab.rejection_live_probe` requires the fixed experiments project and
environment, `QA_REJECTION_SYNTHETIC=1`, internal dashboard/NATS/database targets,
and equality of `QA_SOURCE_SHA` with `RAILWAY_GIT_COMMIT_SHA`. The existing one-shot
qa-e2e-probe service can run it with restart policy NEVER; no new public ingress or
service is needed. `QA_REJECTION_INCLUDE_HTTP=1` also runs the preceding 23-control
HTTP/worker/Outbox/Inbox/dashboard acceptance before the new probe.

The receipt records 20 controls, deployment reference, publication confirmation (UNKNOWN if no publish acknowledgement returns),
audit snapshot hash and a canonical receipt SHA256. Its status is FAILED on any
missing, mismatched, unsafe or unavailable evidence. It does not independently
attest a service image, observe the exact commit-before-TERM timing, certify a
long soak, grant production approval, or establish semantic RAG/legacy parity.
The existing rejection handler tests separately cover commit-before-TERM.

## Reproducibility

27 real-loopback/contract tests run without external clients on Python 3.12/3.13.
A separate CI case starts real PostgreSQL 18, file-backed JetStream, the actual
consumer and actual dashboard. It requires all 20 controls, zero skips, no
unraisable resource warnings, one audit row and no Inbox row. The disposable CI
schema is cleaned up by the harness; the probe itself never deletes audit data.
A CI success is not substituted for a Railway receipt.
