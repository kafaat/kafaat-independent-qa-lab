# Independent offline PCM integrity and scoped legacy comparison

`qa_lab.audio_pcm` is a separately implemented bounded data parser, not a
project-code runner, worker recipe or speech/model quality test. It imports no
legacy module and uses no subprocess, network, plugin loader or model.

## Data contract

The byte API is `validate_pcm(bytes, sample_rate=None, channels=None,
min_duration=0.001, max_duration=300.0)`. `validate_wav(path, **expectations)` reads
one owned local regular file; the CLI prints a result without creating an output
directory: `python -m qa_lab.audio_pcm --wav ./clip.wav`.

The contract accepts RIFF/WAVE integer PCM at 8/16/24/32 container bits, 1 or 2
channels and 8–192 kHz, with one format chunk before one data chunk. Whole-file
RIFF size, chunk bounds/padding, block alignment, byte rate, whole-frame length,
duration, and non-silent content must agree. Padded ancillary chunks are allowed;
64 MiB and 4096 chunks are hard maxima. Full-container extensible PCM is supported
with an exact 22-byte extension, PCM subtype, and consistent channel mask.
Reduced-valid-bit PCM is deliberately unsupported; this can be valid WAV and
must NOT be described as corruption. Silence is a fixture-policy rejection, not
an assertion that silent WAV is invalid.

The file API rejects symlink leaves, directories, FIFOs/devices, oversized input
and detected inode/stat changes. It closes file descriptors and never writes the
input. It is not a filesystem sandbox: parent directories must be trusted and
this API is not registered as a network upload or remote execution endpoint.

## Actual local comparison on 2026-10-30

The user-provided Unified Local Lab archive is pinned to
`8bc988c3ca18c27a221c3684d868d43ec0767fd16318a82c5ac518e80081b6f7`.
Only its reviewed `integrations/audio_contract.py` ran locally as the reference.
Its eight original unit tests passed separately. No legacy source is copied into
this repository or CI, and no generic execution runner is published.

47 identical generated inputs ran through the original validator and the new
implementation. 41 compatibility cases matched exactly after normalization:
accepted cases preserve every metric; rejected cases compare the rejection
decision rather than different error text. Five deliberate policy-strengthening
differences reject inconsistent block alignment, inconsistent byte rate, a 15-bit
container, duplicate format chunks and an inconsistent channel mask that the
original accepted. The sixth difference is unsupported reduced-valid-bit PCM.
None of these six differences is counted as a matching parity case.

Full source-bound v2 receipts were evaluated locally with the existing fail-closed
parity evaluator. The source manifests cover the named audio modules only, not
whole repository trees. The complete receipts are in the delivered
`Independent_QA_Audio_PCM_20261030.zip` under `evidence/comparison.json`.
`docs/evidence/audio-pcm-20261030/summary.json` is only a compact archived summary,
not a substitute v2 receipt or a signature. CI checks the fixture and new module
hashes and replays the new implementation against the archived reference digest;
it does NOT rerun or newly certify the legacy engine.

## Validation and remaining scope

The dedicated workflow requires 75 behavioural and archive-consistency tests on
Python 3.12/3.13, with zero skips and zero unraisable resource errors. Local tests
ran on Python 3.13.5. The published API/worker allowlists and Railway service settings are not changed
by the new module. Audio profile coverage remains
PARTIAL; whole-profile, legacy CLI and deployed worker parity are NOT proven.
Other 20 profiles remain NOT_RUN. ASR, TTS quality, hardware and production
approval are explicitly outside this result.

## Primary format references

- Microsoft WAVEFORMATEX: https://learn.microsoft.com/en-us/windows/win32/api/mmeapi/ns-mmeapi-waveformatex
- Microsoft WAVEFORMATEXTENSIBLE: https://learn.microsoft.com/en-us/windows/win32/api/mmreg/ns-mmreg-waveformatextensible
- Python wave limitations: https://docs.python.org/3.13/library/wave.html
