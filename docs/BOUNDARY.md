# Service boundary

- Hospital: owns cases, priorities, closure, merge and deployment decisions.
- Independent QA Lab: accepts approved execution requests, runs tests and issues receipts.
- AI triage: advisory only; never changes pass/fail or bypasses test gates.
- Every result must bind to source revision, environment, test inputs and execution receipt.
- Missing or stale evidence is BLOCKED/UNKNOWN, never PASS.

## Public repository policy
Do not publish the imported legacy source, credentials, private evidence or deployment variables until license, provenance and secret reviews are complete.
