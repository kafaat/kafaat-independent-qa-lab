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

**Important limits:** Docker alone is not a security guarantee against hostile repository code or forged test output; use reviewed sources only. This is not a leased Hospital TestRun, not independent source attestation, not an authenticated operational integration, and not a remedy for Hospital DB-OBS-001. The Factory D-08 freeze and SAHOOL production remain unchanged.
