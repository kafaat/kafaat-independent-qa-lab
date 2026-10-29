# Source-scoped knowledge and read-only dashboard v2

## Execution and deployment boundary

This patch adds retrieval, read-only HTTP views, and evidence reporting. It does
not add a project code runner, change the worker recipe allowlist, modify existing
Railway services, call a model provider, or expose public domains. Local validation
uses six concurrent test processes, not independent AI agents.

## Knowledge index

`qa_lab.knowledge_index.KnowledgeIndex` stores immutable, SHA-bound document versions
in SQLite. Text is chunked with original character offsets and document/integrity
SHA-256 digests. A query must provide a project and an explicit document-to-source
version mapping; missing versions are not replaced by newer or older documents.
Integrity is rechecked before returning evidence. No-match answers abstain.

The default retriever uses BM25. An optional callable embedder plus a versioned
model identity (`name@revision`) can ingest vectors, and queries can supply vectors
from that same model. Dimensions, finite numbers, nonzero norms and model identity
are checked; lexical/vector ranks are fused. This is an integration interface,
not a bundled or evaluated neural embedding model. Tests use explicitly synthetic
vectors. Arabic normalization handles diacritics and tatweel, NOT morphological
stemming. Answers quote evidence verbatim with offsets/citations; no LLM synthesis
or automatic instruction execution is performed.

The project and source map are scope controls, not authentication of provenance.
Their caller must be trusted. Hashes prove consistency, not source truth or a host
signature. Reindex into a separate database when changing chunking/embedding model;
do not invent a source SHA to bypass an immutable-version conflict.

## Knowledge HTTP service (opt-in, not deployed by this patch)

`python -m qa_lab.knowledge_service` requires:
- `QA_KNOWLEDGE_DB`: persistent pre-ingested index path.
- `QA_KNOWLEDGE_SCOPE`: JSON with `project` and `sources` selected by the operator.
- `QA_KNOWLEDGE_TOKEN`: independent runtime token; no unresolved secret templates.
- Optional `QA_BIND_HOST` (default loopback), `PORT` (default 8781).

Authenticated `POST /v1/search` and `POST /v1/answer` accept only `query` and `limit`.
A caller cannot change the project/source map, supply a remote URL, ingest a
new document, or run a command. Responses are bounded by query/chunk limits.
A private service volume and operator-controlled ingestion are required for
persistent deployment. SQLite is a local single-service index, not a distributed
index or an existing PostgreSQL data migration.

## PostgreSQL dashboard

Existing `python -m qa_lab.pg_dashboard` remains available with the same env vars.
It now supports project/status filters, bounded ID-keyset pagination, run details,
allowlisted receipt fields and Outbox/Inbox ID/subject/payload-match indicators.
`GET /api/runs` and `GET /api/runs/<uuid>` return the same read-only view as JSON.
Every database read runs in a READ ONLY transaction with a statement timeout.
This complements, and does not replace, a dedicated read-only database role.
HTML escapes project names, filters and log text; CSP, no-store and no-referrer
headers are sent. Duplicate auth headers, unbounded filters and all POST writes
are rejected. SQL values are bound parameters. Storage exceptions do not disclose
connection details.

Pagination is explicitly UUID order, NOT creation-time order. A browser login
session, per-user/project authorization, visual browser acceptance, job creation,
cancellation, and manual reconciliation controls are still out of scope. Bearer
authentication remains required; do not put the token in a URL.

## Parity report

`python -m qa_lab.parity_report evidence.json` recomputes a report from a bounded
JSON bundle: `cases` plus receipt entries `{case_id, role, receipt}`. Duplicate
keys/receipt slots and unknown case bindings are rejected. It uses the v2 guard
already on main; absent or unpinned evidence remains NOT_RUN. Exit 2 indicates
any FAILED/PARTIAL/NOT_RUN case. Reports are hashed and explicitly state no new
execution, no host attestation, no full-capability parity or production approval.
The archived audio comparison can be rechecked without re-running legacy code.
The existing whole-profile parity matrix is unchanged.

## Tests and limits

57 local behavioral tests cover ingestion, retrieval, protected HTTP, dashboard
queries/escaping/auth, and report validation. CI repeats these on Python 3.12/3.13,
then runs 3 live PostgreSQL 18 tests in a new disposable schema. The live job fails
on missing/zero/skipped tests or unraisable resource errors. Existing HTTP stack
acceptance also exercises dashboard compatibility. Local execution does not
claim live PostgreSQL or Docker acceptance; those results must be read from CI.

The documentation corpus/evaluation required for real RAG quality, a trained
embedding model, LLM faithfulness evaluation, and full legacy profile comparison
remain separate open work. This patch cannot close those by passing unit tests.
