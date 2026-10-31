# REA static JavaScript E2E — isolated experiment

This optional workflow evaluates [morluto/rea](https://github.com/morluto/rea) version **6.8.0** against a tiny, **synthetic and reviewed** three-file JavaScript application. It does **not** use REA's Ghidra/Hopper/IDA/Rizin provider bridges or run arbitrary native binaries. It does not modify the default QA API, worker, Railway, Factory, SAHOOL, or Hospital service.

The GitHub Actions job builds a disposable Node 24 image, downloads the exact npm package version with `--ignore-scripts`, then runs REA's static JavaScript analysis in Docker with network disabled, non-root UID, read-only mounts, dropped Linux capabilities, no new privileges, PID/memory/CPU limits and bounded temporary files. No GitHub checkout credentials are persisted into the runner workspace and no secrets are passed to Docker.

The verifier `verify_rea.py` independently reads at most 4 MiB of captured JSON, refuses symlinks, duplicate JSON keys and NaN, hashes the actual fixture bytes and REA output, requires both fixture modules to appear in the analyzer's own JSON, and records the Docker image SHA256 ID and GitHub checked-out commit. It never says a security finding is confirmed; the only positive status is `OBSERVED_FOR_REVIEW`.

Local deterministic checks (no REA installation necessary):

```sh
python -m unittest discover -s experiments/rea-offline-e2e -p 'test_*.py' -v
```

**Limitations:** a tagged base Docker image and an exact npm version are not a complete immutable supply-chain lock; npm package integrity, signed image provenance, a fully audited dependency tree and genuine host isolation need separate review. The Git checkout source SHA is a contextual reference, not cryptographic verification that the external REA package is safe. The static fixture is not a Hospital case specimen, and the experiment is not an operational security assessment or reverse-engineering license grant. REA's SECURITY.md explicitly warns that local bridges are **not** sandboxes. Such bridges are never used here.

**Exit behavior:** REA CLI failure, timeout, malformed evidence or missing module means the CI check fails. The verifier's positive receipt still declares `hospital_admission=false`, `production_accepted=false` and `source_attested_independently=false`. Do not connect this workflow to Hospital case closure or move any external resource out of DRAFT status.
