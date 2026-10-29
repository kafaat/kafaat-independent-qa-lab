# Independent QA Railway live acceptance

The following must be run against the actual Railway environment, not inferred from service SUCCESS:
1. Submit a unique fixed python-smoke job via authenticated HTTP API.
2. Confirm worker records terminal PASSED receipt with source_sha.
3. Verify exactly one qa.jobs.created and one qa.jobs.finished outbox row.
4. Verify JetStream QA_JOBS contains both events and inbox has durable event IDs.
5. Repeat idempotency key with same payload (same run) and changed payload (conflict).
6. Restart NATS and verify file-backed stream and consumer state.
7. Test unauthorized API and dashboard requests.
8. Pin deployed commit, environment and receipt digests.

No live acceptance has been claimed by this document.
