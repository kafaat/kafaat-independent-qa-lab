# Dashboard V2 private HTTP acceptance

This is an execution-evidence check, not production approval or a source checkout attestation.

The one-shot `qa_lab.http_acceptance` probe extends the 15 original API/worker/Outbox/Inbox/HTML checks with eight Dashboard JSON controls:

- `GET /api/runs` requires authentication and rejects wrong tokens.
- `GET /api/runs/{run_id}` requires authentication.
- Invalid status filters return 400; POST mutations return 405.
- Authenticated, project- and status-filtered UUID-keyset pages include the actual run.
- JSON detail lists exactly the two Outbox event IDs and matching subjects, with `published`, `received`, and `payload_matches` all `true`.
- Detail binds the requested source SHA and explicitly carries `source_attestation=request-reference-only` and `production_accepted=false`.
- JSON responses return `Cache-Control: no-store`, `X-Content-Type-Options: nosniff` and a JSON content type.

The probe saves the safe receipt in PostgreSQL and logs only fields without tokens. It refuses public origins, redirects, and unauthorized mutations. An old HTML-only Dashboard must fail the added controls. It is permitted to run only in the isolated experiments environment with existing private service credentials.

CI runs 14 unit/loopback tests and one PostgreSQL 18 + NATS JetStream multi-service integration test. The real Railway acceptance must additionally produce `status=PASSED`, 23 passing checks, the expected deployment identity and a matching receipt hash. No project code or arbitrary user-supplied commands are executed.
