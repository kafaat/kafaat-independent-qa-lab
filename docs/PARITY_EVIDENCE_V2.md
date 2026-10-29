# Fail-closed parity evidence (v2)

## Reproduced defect
At main `3002a0014bf7c3aa5206ed2f48ef54b12e87b3ad`, two receipts containing
only `status=PASSED` could yield VERIFIED: missing input/output hashes compared
as `None == None`. Mutable refs, placeholder hashes and reused run identities
were also accepted. The baseline blob is `b48000edbedaf0d93d4b887454a0ba7888bd72eb`.

## New contract
The existing `evaluate(cases, receipts)` signature and four result states remain.
A case must pin `legacy_ref`, `new_ref`, both `*_tree_sha256` values, both
`*_recipe` identities, `input_digest`, and `comparator=exact-output-sha256-v1`.
Refs must be full lowercase Git SHA-1 or `sha256:<content-bundle-hash>`.

Each v2 receipt binds case, side, distinct UUID execution, source ref/tree,
recipe, actual normalized input/output and SHA-256 digests, timestamps with
explicit timezone, executed/unchanged flags, zero exit code, PASSED status,
and a recomputable envelope hash. Reusing an execution across cases is refused.

- Missing receipts or unpinned case criteria: NOT_RUN.
- Invalid, stale, tampered, cross-case, nonfinite or reused evidence: FAILED.
- Different valid output digests: PARTIAL.
- Matching complete evidence: VERIFIED (receipt consistency only).

Hashes are not signatures or host attestation. The evaluator does not execute
code, approve a release, or make fabricated evidence trustworthy. Each result
explicitly has `host_attested=false` and `production_accepted=false`.
Historical placeholder cases are intentionally NOT_RUN, not grandfathered in.

## Validation and local source-backed comparison
The patch has 37 dedicated regressions plus 4 existing compatibility tests.
A broader local candidate (including an opt-in project runner) passed 67 tests,
zero skips and zero unraisable resource errors. Only the parity fix/tests and
safe data evidence are published by this PR: the tool safety layer blocked
uploading the local project-runner implementation. No alternate upload route
was used. Its associated execution tests are NOT claimed to run in this PR CI.

The user-provided Unified Local Lab 0.7.8 archive was pinned to SHA-256
`8bc988c3ca18c27a221c3684d868d43ec0767fd16318a82c5ac518e80081b6f7`.
Its actual `test_unified_audio.py` suite ran twice: directly via unittest and
via the local trusted-snapshot runner. Both executed 8 tests with no skips;
normalized outcomes and input identities matched. Separate receipts, actual
case and local runner file-hash inventory are in `docs/evidence/parity-20261029`.

This is ONE local invocation-compatibility subcase, not a rewritten audio
engine or full profile acceptance. Both paths reuse the original audio tests
and implementation. `LEGACY_PARITY_PROGRESS.json` keeps all 21 deployed-profile
comparisons NOT_RUN, with one PARTIAL local coverage item (audio) and 20 NOT_RUN.
No legacy source, credentials, network execution recipe, or model was uploaded.

## Operational boundary
No Railway configuration, API/worker allowlist, database, NATS stream or stored
HTTP acceptance receipt is changed. The running worker still supports its
existing fixed smoke recipe. A real-project runner requires explicit trusted
code review and OS/container isolation before any network registration.
