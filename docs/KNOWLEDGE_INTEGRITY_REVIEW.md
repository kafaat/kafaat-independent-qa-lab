# Knowledge integrity review

Reviewed head: `407d03e3d036b5c9c6a904b6b8be4109d901b5ef` (PR #13).

## Reproduced defect
A five-chunk document remained `EVIDENCE_FOUND` after deleting its final chunk.
Individual surviving chunk hashes all remained valid. The new first/middle/last
chunk deletion tests all fail on the reviewed head and pass with this fix.

## Changes
- Read every selected source in one SQLite read transaction.
- Verify consecutive ordinals, continuous character offsets, one document digest,
  and SHA-256 of the complete reassembled UTF-8 document before returning hits.
- Verify all scoped documents even when only another document matches the query.
- Open the knowledge HTTP service index using SQLite `mode=ro`. The service cannot
  ingest, change schema, or silently create an empty index at startup.
- Startup requires every pinned source; missing versions never fall back to latest.
- Reject duplicate JSON request and scope keys.

## Validation
22 new executable regressions, including real loopback HTTP requests, plus the
previous 57 behavioural cases: 79 passed locally on Python 3.13.5, in six separate
concurrent test processes; zero skipped tests or unraisable resource errors.
These are test processes, not independent AI agents. The CI workflow requires 79
on Python 3.12/3.13 and retains the three real PostgreSQL 18 dashboard tests.

## Deployment requirements and limits
Provision/ingest a reviewed index with `KnowledgeIndex(path)` offline first; then
run the service with `QA_KNOWLEDGE_DB`, a valid token and a complete pinned scope.
The existing chunk schema remains compatible. An older incomplete index is now
rejected and must be rebuilt from its pinned source rather than auto-repaired.
Hashes detect inconsistent data, not a malicious writer that rewrites all data
and hashes. No signatures, new embedding model, LLM generation, source execution,
project ACLs, or whole-capability legacy parity are claimed.

## Follow-up: runtime query correction for wholly missing pinned documents

A completely deleted pinned document previously yielded `INSUFFICIENT_EVIDENCE` during `retrieve` even though startup `verify_scope` rejected it. The query now requires every selected `(document_id, source_sha)` to exist before scoring. Two additional executable regressions prove whole-source deletion and a missing second pinned document cannot silently return a partial answer. The knowledge/dashboard workflow now requires 81 behavioural cases on each of Python 3.12 and 3.13. This guards index completeness only; it does not provide host signatures, a neural model, user ACLs or full legacy parity.
