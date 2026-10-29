# Legacy Unified Local Lab parity acceptance

All current matrix cases remain NOT_RUN. A parity claim requires access to the actual legacy source, a pinned commit/tree hash, explicit license and provenance review, sanitized fixtures, identical input digests, and execution receipts from both engines.

Required comparison groups:
- CLI and recipe discovery
- job submission and idempotency
- failure/timeout semantics
- SQLite lifecycle and state recovery
- receipt integrity and evidence provenance
- safety and isolation boundaries
- PostgreSQL and NATS additions (new-only, not legacy parity)

Every case must have a documented disposition: VERIFIED, PARTIAL, FAILED, or NOT_RUN. Never convert missing source, missing test execution or skipped checks into VERIFIED. This document is a gate, not evidence of parity.
