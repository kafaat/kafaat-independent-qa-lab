# Reviewed EO delta: local evidence, 2026-10-30

The attached EO delta was verified and imported into an isolated experimental
addition after PCM evidence PR #25 merged. The original ZIP and all 47 extracted
files remain unchanged. All 30 public satellite fixture/metadata files retain
their original bytes. This evidence approves the bounded offline research checks;
it does not approve a SAHOOL production deployment or global offset correction.

## Results

| Validation layer | Original delta | Reviewed integration |
| --- | ---: | ---: |
| Guard unit tests | 17 passed | 26 passed |
| Independent checks | 18 passed | 18 passed |
| Runner regressions | Not present | 12 passed |

These are distinct layers, not a claim of 56 independent domain cases. The
integration was tested with Python 3.12.14 and a newly installed, hash-pinned
NumPy 2.5.4 wheel. Source hashes were identical before and after acceptance.

## Evidence map

- [summary.json](summary.json): bounded result, input integrity, code identities,
  actual counts, diagnostics and limitations.
- [final/acceptance/result.json](final/acceptance/result.json): actual commands,
  timings, return codes, validated test identities and before/after source hashes.
- [final/runner_regressions.stderr.log](final/runner_regressions.stderr.log):
  twelve runner tests; command and machine summary are in the same directory.
- [baseline/findings.json](baseline/findings.json): defects reproduced on the
  original guard and deliberately modified isolated runner copies. Original
  reports, receipts and logs are retained under `baseline/`; the publication map
  resolves their historical workspace paths to repository evidence paths.
- [final/diagnostics/summary.json](final/diagnostics/summary.json): fresh results
  for three archived windows from two acquisitions, preserving normal metrics.
- [dependency.json](dependency.json), [sources.json](sources.json): dependency
  and primary-source provenance; no downloaded third-party pages are republished.
- [SHA256SUMS.sha256](SHA256SUMS.sha256): digests of this evidence set, excluding
  the checksum file itself.

## Findings and fixes

The original guard accepted conflicting asset CRS metadata, allowed finite
inputs to overflow into nonfinite calculations, inferred exact equivalence from
empty finite support in one probe, mishandled a huge integer scale, and failed to
return structured CLI JSON for some numeric failures. The repaired guard rejects
these unsupported states while preserving negative reflectance, visible NDVI
outliers and null empty-support statistics.

Four runner false-passes were reproduced: only one unit test, all 17 tests
skipped, contradictory independent case rows despite `all_passed=true`, and a
changed guard omitted from the manifest. The zero-test suite already failed in
the observed interpreter and is not counted as a reproduced false-pass. The
new runner enforces fixed reviewed test identities, per-case results, complete
manifest coverage, bound logs and before/after source identity.

The local receipt deliberately says `package_dirty=true` and
`tested_clean_commit=false`: these tests ran before the integration commit on
base `775ebf1ca37a1e76a509971950eccffc5affaad3`. The package digest binds the actual
tested bytes. GitHub Actions must supply its own run and checkout identities;
this historical local receipt is not a fabricated clean-commit CI result.

## Interpretation and reuse

Scale-only diagnostic residuals are almost zero in two windows and leave seven
red-band residual pixels in the selected March window. They motivate inspection
of those specific archived assets. C1 is a paired representation, not independent
radiometric ground truth; no global offset policy follows from this comparison.
The December fixture is API v1 baseline 05.09, not a current API v2 validation.

Use the tool as a standalone lab gate, and as reference fixtures before reviewing
SAHOOL ingestion code. Potential hospital cases remain candidates until an
actual product defect is reproduced. The runner/receipt pattern can inform the
factory; Sentinel-specific rules remain in this research tool.

Reproduction and attribution are documented in
[the tool README](../../../tools/eo-validation/README_AR.md) and
[DATA_SOURCES.md](../../../tools/eo-validation/DATA_SOURCES.md).

Contains modified Copernicus Sentinel data (2022).
