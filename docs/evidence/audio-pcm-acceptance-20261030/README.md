# Limited synthetic PCM acceptance — 2026-10-30

This directory publishes the completed acceptance run for the archived independent
PCM package. The complete evidence bundle is preserved byte-for-byte so its raw
logs, deterministic inputs, source snapshot and replay commands remain available
from the repository.

## Results

| Check | Observed result |
| --- | --- |
| Packaged offline suite | 76/76 tests in each of two fresh Python 3.12.14 runs |
| Failures, errors, skips and unraisable resource errors | Zero |
| Independent API cases | 30 cases repeated twice: 60/60 executions passed |
| Independent CLI cases | 3 scenarios repeated twice: 6/6 executions passed |
| Repetition | Test results and synthetic inputs/outputs match between repetitions |
| Original package integrity | 31/31 checksum entries; original ZIP and package unchanged |
| Archive consistency review | 131/131 assertions; these are not additional runtime tests |
| Final bundle integrity | 82 checksum entries covering 83 ZIP members except the manifest |
| Synthetic audio | 30 generated WAV files, 2,744 bytes total, at most 172 bytes per file |

The standalone runners use the Python standard library. They reject existing
output directories and outputs inside their protected input tree. The independent
runner's final pass condition includes unchanged function defaults. The acceptance
receipt is copied exactly from the ZIP; it has not been rewritten for publication.

## Source identity and current repository linkage

The accepted input was `Independent_QA_Audio_PCM_20261030.zip`:

- Input archive SHA-256: `bb1eb9cf1fc5739c1b6c74dd7b4c6ef0e60cba6612c311ae2aa5607f94575115`.
- Tested `audio_pcm.py` SHA-256: `2ec8dde53cb0afde7f2b1090dad6d14be8e408a108315486dbc38d5a8100f9b0`.
- Publication base: `f6ac494dc4fa8b747736af57d935edf8d3bf8a2d`.
- That base contains merged PCM PR #23 plus the later opt-in audio jobs change.

The module, parity helper, PCM fixtures/tests and associated evidence documentation
match the pinned publication base by Git blob ID. Across all eleven bundled source
files, nine match and two do not:

1. The archived `audio-pcm.yml` predates the current workflow's explicit selection
   of the original three PCM test modules. The newer job tests have a separate gate.
2. The archive has an empty `qa_lab/__init__.py`; the repository has a docstring and
   `__version__` metadata. This qualifies the original archive README's wording that
   the initializer was copied from the base. It is a scoped runnable package, not a
   byte-for-byte repository checkout.

[source_linkage.json](source_linkage.json) records every compared blob. The local
acceptance does not certify the full current repository or the newer audio-job/API/
worker integration. Any CI triggered by this PR is a separate, current-head result.

## Evidence interpretation

- The earlier archived `audio_tests.log` contains 70 tests; the final archived
  `strict_audio_tests.log` contains 76. The newly delivered replay ran 76 twice.
- The archived comparison has 41 matching compatibility cases and six declared
  differences. The new parser was rerun against recorded legacy output; the legacy
  implementation was not executed again.
- The original eight legacy tests, six mutation runs and historical CI metadata
  remain historical evidence. Mutation failures are intentional guard-disable
  results, not failures of the intact parser.
- Silence rejection and unsupported reduced-valid-bit PCM are contract policies;
  rejection does not by itself establish that such a file is invalid WAV.
- Full audio-profile coverage remains `PARTIAL`; deployed comparison remains
  `NOT_RUN`; speech quality and production approval are not claimed.
- Paths and timestamps inside the bundle describe the completed local run. Its
  statements that no remote mutation or current GitHub query occurred describe
  that earlier run, before this later publication step.

The bundle contains independently implemented project code, synthetic fixtures
and scoped evidence. It contains no imported legacy implementation or real audio.
A targeted publication scan found no credentials, private keys, tokens or signed
access URLs. It is not a general-purpose secret-audit certification.

## Files and verification

- [Complete ZIP](Independent_QA_Audio_PCM_Acceptance_20261030.zip): unchanged self-contained bundle.
- [Acceptance receipt](acceptance_receipt.json): exact extracted receipt for review.
- [Source linkage](source_linkage.json): current base linkage and explicit differences.
- [SHA256SUMS.sha256](SHA256SUMS.sha256): checksums for the four files in this directory.

The ZIP SHA-256 is:

```text
7cf77795ab992ce4ba6ec9a9bc3d7a2c72bb1cdf80f966b064406111741324e2
```

From this directory, verify the outer files:

```bash
sha256sum -c SHA256SUMS.sha256
```

Extract the ZIP into a new directory. From the extracted bundle root, verify its
inner manifest and replay on Linux with Python 3.12:

```bash
sha256sum -c SHA256SUMS.sha256
PYTHONDONTWRITEBYTECODE=1 python -B acceptance_replay.py --package ./package --out ./replay-new
PYTHONDONTWRITEBYTECODE=1 python -B -W error::ResourceWarning independent/run_independent_acceptance.py --source ./package/source --output ./independent-new
```

These commands need no external packages or network connection. Choose fresh output
directory names for later attempts. The normalized results are deterministic;
wall-clock timestamps, elapsed time and absolute local paths are not expected to
be identical across machines.
