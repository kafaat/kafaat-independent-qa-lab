# Private-network knowledge acceptance (experimental)

## Goal
Validate **the actual running read-only knowledge HTTP service** on the Railway
private network without exposing the service publicly or using submitted project code.
The probe's one-shot executable is `python -m qa_lab.knowledge_live_probe`.

## Acceptance boundary
Checks eleven HTTP properties: no/wrong token (401), authenticated health,
retrieval with exact pinned-corpus citation and source digest, bounded BM25 search,
abstention on missing evidence, rejection of client-selected project/source,
duplicate JSON query keys, unsupported media type, attempted HTTP ingestion and
overlong request body.

The probe refuses public URLs, HTTPS endpoints, redirects and proxies; its default
allowlisted target is precisely `http://qa-knowledge.railway.internal:8781`.
It runs only a fixed set of request payloads, without source-code execution,
knowledge ingestion or release authority. Source SHA labels are content references,
**not host attestation**. Failure logs expose only bounded reason codes.

## CI
The GitHub workflow runs 12 strict local contract tests on Python 3.12 and
3.13 and builds the knowledge image. It starts two containers on a Docker-private
network (non-root and read-only filesystem), runs the same probe across that
network and checks the 11-step PASSED JSON receipt.

## Railway rollout
1. Merge only after green PR checks.
2. Provision a **new** one-shot service `qa-knowledge-probe` in `experiments`,
   source pinned to the merged commit and image built from `Dockerfile.knowledge`.
3. Set `QA_KNOWLEDGE_URL=http://qa-knowledge.railway.internal:8781`,
   `QA_KNOWLEDGE_TOKEN=${{qa-knowledge.QA_KNOWLEDGE_TOKEN}}` as a Railway
   variable reference, and `QA_EXPECTED_PROBE_SOURCE_SHA=<merged commit SHA>`.
4. Set start command `python -m qa_lab.knowledge_live_probe`, restart policy NEVER.
5. Do not create public domains or TCP proxies. Verify log attributes show
   `status=PASSED`, `checks_passed=11`, source and deployment identities, and
   `receipt_sha256`. Do not infer PASS from Railway's deploy status alone.

## Limits
Only the three public-document corpus entries are in scope, no user-level ACL,
trained semantic model, LLM synthesis, executable project recipes or complete
legacy parity. No production approval.
