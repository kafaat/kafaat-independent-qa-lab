# Bounded audio validation jobs (experimental, opt-in)

`POST /v1/audio-runs` submits a **data-validation job**, not project code.
The existing `/v1/runs` contract and its `python-smoke` allowlist stay unchanged.
The new route and worker dispatch each require `QA_ENABLE_AUDIO_PCM=1`; with the
flag absent/other values the route returns 404 and an inserted audio job is
BLOCKED. Deploy a compatible worker before enabling the API route. There is no
reclaim/re-execute of RUNNING jobs after interruption.

## Request and constraints

Use the existing private `QA_API_TOKEN` as Bearer authorization. Exactly these
six fields are accepted, all strings:

```json
{
  "idempotency_key": "unique-upload-attempt",
  "project": "owned-audio-fixtures",
  "source_sha": "<40-lowercase-hex project reference>",
  "recipe": "audio-pcm-v1",
  "audio_base64": "<canonical standard base64 of complete RIFF/WAVE bytes>",
  "audio_sha256": "<64-lowercase-hex SHA-256 of decoded bytes>"
}
```

Decoded input is 1–65,536 bytes; HTTP body is at most 92,160 bytes. The transport
rejects noncanonical base64, duplicate JSON keys, duplicated Authorization or
Content-Length headers, transfer encoding, unknown fields and incorrect input
digests. No filename/path/URL/command/model is accepted. Parsing does not happen
in the API: a valid transport envelope can contain invalid media, which becomes
a terminal FAILED job. A malformed envelope is rejected before queue insertion;
the worker independently revalidates it and uses BLOCKED if persisted input was
corrupted. Reusing a key with identical request returns the same run; changed
input returns HTTP 409. This does not add a concurrent-idempotency proof.

The full input **is retained as base64 in `qa_jobs.request`**. It is not copied to
Outbox/Inbox events, receipts, HTTP errors or request logs. No media retention
job, at-rest encryption guarantee, public upload UI or per-user/project ACL is
introduced. The existing token authorizes the environment; the `project` field
is not an access-control mechanism. Use owned, non-sensitive test fixtures until
those independent controls are approved.

## Result and evidence

Read the existing `GET /v1/runs/{run_id}` endpoint for terminal status and receipt.
The fixed parser accepts the bounded integer-PCM contract documented in
`AUDIO_PCM_PARITY.md`. Silence is a deliberate policy failure, not invalid-WAV
certification. Each audio receipt includes input SHA-256/size, request SHA-256,
parser source-file SHA-256, output SHA-256, run/worker IDs and envelope SHA-256.
The parser's reviewed file digest is pinned; a mismatch blocks execution.

PASSED means this particular file met the PCM contract, not speech quality,
ASR/TTS accuracy, full legacy parity, sandbox/host attestation, or production
approval. `source_sha` is only the caller's project reference. Hashing the local
parser file is a content check, not proof of an uncompromised interpreter or host.
The dashboard retains its existing status/receipt projection; complete structured
audio metrics are available from the run API, not a new media UI.

## Acceptance and rollout

43 unit/real-loopback HTTP tests cover envelope validation, worker revalidation,
gates, idempotency, privacy and evidence integrity. Dedicated CI runs them on
Python 3.12/3.13 and three real PostgreSQL 18 + NATS cases with the actual worker,
relay and consumer, requiring exact published/received events for PASSED,
FAILED and BLOCKED jobs. The live test uses a disposable schema and broker.

Local scoped validation includes the existing 76 PCM tests (119 total). Local
PostgreSQL/NATS execution and full-repository regression are not claimed; those
are separate CI gates. Deployment must preserve the existing smoke and rejection
acceptance. This document is not a Railway receipt and does not mark any deployed
legacy profile VERIFIED. No generic or previously blocked project runner is
included.
