# Reviewed source trial (experimental, offline)

The optional `qa_lab.project_trial` module is **not** a network API and does not modify the existing Railway `python-smoke` worker. It adds a separate, evidence-only test path for operator-reviewed source.

It checks a clean Git HEAD against the 40-character expected commit, rejects untracked or changed files, symbolic links and gitlinks, and copies only tracked regular-file bytes (never `.git`) to a disposable source directory. The resulting manifest binds filenames, SHA256 hashes, Git tree ID, file count and total size.

A digest-pinned, locally cached Python image runs `python -m unittest discover -s tests -p test_*.py` with Docker `--network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges`, an unprivileged user and explicit memory/CPU/PID/time limits. Results and source are inspected again after execution. The parent HMAC-signs an evidence-only receipt using a key kept outside source; tests never receive that key.

The Hospital must provide the expected tenant/project/case/cycle/attempt/snapshot/criteria/source bindings, the independently expected manifest SHA256, image digest and minimum test count. None may be inferred from the producer's receipt. A correctly signed, matching receipt remains **review-only**, with `source_attested_independently=false`, `production_accepted=false` and no case-closure authority.

Local CLI (only on an approved isolated test host with Docker; provide private key mode 0600):

```bash
python -m qa_lab.project_trial \
  --repo /absolute/clean/reviewed/git-checkout \
  --binding /private/hospital-binding.json \
  --image 'python:3.12-slim-bookworm@sha256:<verified-digest>' \
  --key-file /private/qa-producer-key.hex \
  --output /private/new-receipt.json
```

The CLI creates a new output file, returns nonzero on unsuccessful tests, does not transmit anything to the Hospital, and never grants operational approval. A sample GitHub Actions job tests real fixture bytes inside Docker and another job checks the negative contracts without Docker.

## Explicit baseline negative controls

`execute_with_output(...)` uses the **same** Docker executor and returns
`(receipt, bounded_output_bytes)`. The existing `execute(...)` API and signed
receipt fields remain unchanged. Output is untrusted diagnostic data; its SHA256
must match the authenticated receipt before it can support a negative control.

`verify(...)` still requires `PASSED` by default. A reviewer may explicitly supply
`expected_result="FAILED"`, `observed_output=...`, and a nonempty list of exact
`expected_failure_ids`, for example `test_calculation.Suite.test_total`. In this
mode `minimum_tests` is an **exact** catalog count. Only exit code 1 with the
expected assertion failures qualifies. Import/collection/runtime errors, Docker
errors, signals, timeouts, skips, unexpected failure IDs, changed output, missing
receipt fields and duplicate summaries are rejected. The report retains
`observed_status="FAILED"`, `expected_status="FAILED"`,
`purpose="negative-control"`, and `project_accepted=false`.

The caller must independently fix the expected source SHAs, manifest digests,
image digest, criteria and catalog before execution, and prove that baseline and
candidate contain identical test bytes. `MATCHED` means the expected negative
control was observed; it never means that a failing project passed acceptance.
The additional Docker CI job demonstrates an intentional arithmetic defect and
its correction with the same two tests. It is a synthetic software experiment,
not an operational project integration. Text parsing remains limited to reviewed
source and cannot certify adversarial tests that forge their own output.

**Important limits:** Docker alone is not a security guarantee against hostile repository code or forged test output; use reviewed sources only. This is not a leased Hospital TestRun, not independent source attestation, not an authenticated operational integration, and not a remedy for Hospital DB-OBS-001. The Factory D-08 freeze and SAHOOL production remain unchanged.
