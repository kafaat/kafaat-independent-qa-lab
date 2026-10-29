# QA Lab execution plan

This repository remains public. The complete imported legacy laboratory is intentionally excluded until license, provenance, and secret review. Current experiment scope is an execution-evidence kernel with deterministic advisory triage, not an autonomous AI laboratory.

Acceptance gates before Railway source connection:
1. Publish reviewed application source and pinned Docker build context.
2. Run Python regression tests and image build.
3. Provision persistent storage and reconcile worker crash semantics.
4. Test real service-to-service requests, failure recovery, and receipt verification.
5. Record deployment identity and terminal Railway status.

Hospital owns cases and decisions; QA Lab returns execution evidence only.
