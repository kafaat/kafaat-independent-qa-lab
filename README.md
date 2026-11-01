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

## Branch protection and optional human review

On 2026-11-01, the repository owner removed **mandatory human approvals** and
mandatory retrospective human security/functional sign-off for the local
Independent QA Lab. Human review remains available **optionally**. It must never
be recorded as completed unless actually performed.

The live [CI Ruleset #26292547](https://github.com/kafaat/kafaat-independent-qa-lab/rules/26292547)
enforces pull requests, strict successful GitHub Actions contexts `test`,
`operator-ui`, `root-stack`, and `http-stack`, and blocks deletion and
force pushes to `main`. No human approvals or resolved-review-thread gate
is mandated. The live rule's old name contains "temporary"; this is a legacy
label, not a pending requirement to introduce human approvals.

[Canonical JSON policy](docs/governance/main-ruleset.json),
[legacy CI-only alias](docs/governance/main-ruleset-ci-only.json),
[activation/readback guide](docs/governance/RULESET_ACTIVATION_AR.md),
[optional security review checklist](docs/governance/INDEPENDENT_REVIEW_HANDOFF_AR.md),
and [decision record](docs/governance/DECISION_20261101_NO_HUMAN_GATE_AR.md)
describe the policy. Files alone do not activate or modify GitHub Rulesets.
[Issue #46](https://github.com/kafaat/kafaat-independent-qa-lab/issues/46)
keeps historical review decisions without manufacturing human sign-off.

**This does not authorize public or production deployment.** Security controls
for internet exposure, multi-user identity/RBAC, backup/restore, and an
authorized release decision remain separate.
