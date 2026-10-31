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

## Optional local operator console
An opt-in, loopback-only server-rendered browser console now supports a single
local operator: login/logout, bounded run search and pagination, approved-recipe
submission, run detail, and receipt hash verification. The browser never receives
the administrative API bearer token. See [operator console setup and security
boundaries](docs/OPERATOR_CONSOLE_AR.md). This is NOT multi-user project RBAC or
a public deployment. The default Compose startup remains unchanged.

## Operator UI container acceptance

A dedicated isolated, opt-in Docker Compose acceptance checks the actual
localhost operator-ui container, real browser submission -> API -> SQLite
worker -> receipt, secret boundaries, logout and restart, and cleanup.
See [operator acceptance](docs/OPERATOR_COMPOSE_ACCEPTANCE_AR.md) and
[reviewed security boundaries](docs/OPERATOR_SECURITY_REVIEW.md).
This proves neither project RBAC nor public deployment readiness.

## Branch protection and independent review

The importable **proposal** for `main` branch protection lives in
[docs/governance/main-ruleset.json](docs/governance/main-ruleset.json), with
[activation guidance](docs/governance/RULESET_ACTIVATION_AR.md) and an
[independent human review handoff](docs/governance/INDEPENDENT_REVIEW_HANDOFF_AR.md).
Committing these files does **not** activate GitHub Rulesets or constitute an
independent approval. Governance completion remains tracked in
[issue #46](https://github.com/kafaat/kafaat-independent-qa-lab/issues/46).
