# Data sources and interpretation

Contains modified Copernicus Sentinel data (2022), accessed through Element84
Earth Search API v1. These are archived research windows from tile 38PMC, acquired
on 2022-03-21 and 2022-12-06. Three windows do not constitute three independent
acquisitions or a representative agricultural sample. They are not the user's
farm boundaries.

The original STAC Item metadata, product/datastrip identifiers, asset URLs,
extraction windows and affine grid declarations remain alongside the NPZ arrays.
The `source` and `aligned` SCL arrays preserve the recorded nearest-neighbour
mapping. The fixture bytes were not changed during import or hardening.

## Attribution and source records

- [Sentinel Data Legal Notice](https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice)
  governs Sentinel data use and provides the modified-data attribution above.
  Data attribution is separate from any software license; no provider
  endorsement is implied.
- [Copernicus S2 Products](https://sentiwiki.copernicus.eu/web/s2-products)
  describes Level-2A reflectance as `(DN + BOA_ADD_OFFSET) / QUANTIFICATION_VALUE`
  after processing baseline 04.00, with `DN=0` still representing NoData.
- [Element84 Sentinel-2 documentation](https://github.com/Element84/earth-search/blob/184b25376b9b2dcc5296bb5a4b0820e51fba039a/docs/collections/sentinel-2-l2a.md)
  is pinned to commit `184b25376b9b2dcc5296bb5a4b0820e51fba039a`. Its gain/offset
  guidance directs consumers to the asset's declared `raster:bands` scale and
  offset. This tool does not authorize overriding that guidance globally.
- [Copernicus NDVI example](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Process/Examples/S2L2A.html)
  uses `(B08 - B04) / (B08 + B04)` for NDVI.

The December fixture carries processing baseline 05.09 in API v1. The pinned
Element84 document states that API v2 excludes 05.09 and changes Collection 1's
collection name. Results from these archived fixtures must not be presented as
validation of the current v2 collection.

## What a comparison establishes

Each old/C1 pair refers to the same source product and datastrip. C1 is a
comparator, not independent radiometric ground truth. The diagnostic
`old_scale_only_hypothesis` is restricted to these explicit assets. A smaller
residual is evidence to investigate their metadata/encoding relationship, not
permission to remove offsets in SAHOOL or to correct all Element84 imagery.

Negative reflectance is distinct from NoData. NDVI values outside [-1, 1] remain
visible. Quality counters overlap, and cloud/shadow counters do not imply that
all reported statistics were filtered by an agricultural cloud mask. Empty
numeric support yields null statistics. Overflow of otherwise finite supported
input is rejected explicitly.

## Imported artifact and dependency

The input artifact `SAHOOL_EO_Validation_Delta_20261030(1).zip` has SHA-256:

```text
d4a4fe50a42821eeaa0d6a60725ef23183f1b9d80731aa8bf8fb6876787d303a
```

Its original binary patch has SHA-256:

```text
9383e473409e6e177456b4b84244381c9364ba5a19fe032bfa17dc2443963bbc
```

The original patch added 38 files. The reviewed integration adds fixed acceptance
contracts, runner regressions, provenance documentation and an isolated workflow,
and corrects guard/runner defects. The regenerated manifest identifies this
revised tool; it is not a signature over the original artifact.

CI uses the SHA-256-pinned NumPy 2.5.4 CPython 3.12 Linux x86_64 wheel listed by
[official PyPI metadata](https://pypi.org/pypi/numpy/2.5.4/json). Dependency
installation is a network step; the validation commands themselves use local
files only. A successful CI installation is recorded separately from the
original attachment's existing-environment execution.
