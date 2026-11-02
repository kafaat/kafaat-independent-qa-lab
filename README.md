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

Since 2026-11-01 20:00 (+03:00), live [Ruleset #26292547](https://github.com/kafaat/kafaat-independent-qa-lab/rules/26292547)
requires **zero** human approvals with every review gate off, and still requires
the four strict GitHub Actions checks, a pull request, no bypass, and
deletion/force-push protection. The read-only verifier reported PASS, and a
disposable PR (#52, closed unmerged) showed merging is allowed with no reviews
when checks pass and blocked when the required `test` check fails.
If the Ruleset is ever changed again, an administrator restores this **existing**
Ruleset as follows:
set approvals to 0, disable last-push approval, stale-review dismissal,
mandatory conversation resolution and the extra approval for unattributed
Copilot pull requests (`require_extra_approval_for_unattributed_changes`, previously
enabled live; it only adds an approval when the count is above zero),
and retain the strict required checks (`test`, `operator-ui`, `root-stack`,
`http-stack`, `ci-gate`), pull-request requirement, no bypass, and
deletion/force-push protection.
The rule may then be renamed "QA Lab main — required CI, optional human review".
Do not import a second overlapping Ruleset; an admin may instead `PUT` the
canonical policy onto the existing ID 26292547. Confirm the effective
parameters with the read-only `python tools/governance/verify_live_ruleset.py`
using an admin-authenticated GitHub CLI.

### Single CI gate (`ci-gate`)

With human review optional, CI is the only merge gate. The `ci-gate` job
([workflow](.github/workflows/required-ci-gate.yml),
[script](tools/ci/required_checks_gate.py)) derives the expected check names
from the workflow files and requires **every** job of every workflow that runs
on all pull requests (35 checks when added, including the live PostgreSQL and
NATS acceptance jobs) to succeed on the PR head, plus any path-filtered check
that was triggered. Missing checks fail closed at a 40-minute deadline; after
re-running a failed job, re-run `ci-gate` too. Job names are unique across
workflows and every action is pinned to a commit SHA. Preview the list offline
with `python tools/ci/required_checks_gate.py --list`. The Ruleset must list
`ci-gate` as a required check; the owner added it on 2026-11-02 (17:35 +03:00),
the read-only verifier reported PASS, and disposable PR #54 (closed unmerged)
was blocked when only the non-named `boundary` check failed.

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

## Branch cleanup

Merged work is preserved in each pull request (`refs/pull/<n>/head`), so its
branch can be deleted and later restored from the pull request page. The
manual **Merged branch cleanup** workflow
([workflow](.github/workflows/branch-cleanup.yml),
[script](tools/ci/merged_branch_cleanup.py)) lists, and with the `apply` box
ticked deletes, only branches that are not `main`/default/protected, have no
open pull request, and whose current head equals the head of a closed pull
request. It is a dry run by default. Enable *Settings → General → Automatically
delete head branches* to keep new merges tidy.

## License

Licensed under the [Apache License, Version 2.0](LICENSE); see [NOTICE](NOTICE).
Third-party tools downloaded at run time keep their own licenses, and the
imported legacy `unified-local-lab` source is not part of this repository.
