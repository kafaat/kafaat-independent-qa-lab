# Independent QA Lab

Independent QA Lab is an experimental, standalone execution-evidence service.

## Boundaries
- The technical hospital owns cases, decisions, and closure.
- QA Lab executes approved tests and returns evidence; it does not merge or deploy production changes.
- AI suggestions are advisory and must pass independent tests.

## Local startup
See [the root SQLite startup and acceptance guide](docs/ROOT_STACK_AR.md).
The default Compose stack has an explicit initializer, API, worker, and read-only
services for evidence and advisory triage. It publishes the API on loopback only.
It is not a browser login/product frontend or a sandbox for untrusted projects.
PostgreSQL, NATS, audio and knowledge configurations remain separate capabilities.

## Status
Acceptance must be tied to the tested source and environment; a green build or
receipt hash is not production/release approval. Live deployment/recovery,
individual user sessions and project authorization, broader recipe acceptance,
and legacy licensing/provenance/secret reviews remain separately gated.
No production deployment is performed by the root-stack acceptance workflow.
