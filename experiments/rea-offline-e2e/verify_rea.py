"""Fail-closed readback of REA's offline static-JavaScript analysis.

Tool execution occurs only in the dedicated GitHub Actions disposable container,
not in this module. Byte and identity checks cannot certify REA's analysis
correctness, the host OS isolation, provider safety or the right to analyze
third-party binaries. No provider bridges or runtime captures are enabled.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat

MAX_ANALYSIS = 4 * 1024 * 1024
MAX_FIXTURE_FILE = 128 * 1024
FIXTURE_FILES = ("main.js", "pricing.js", "package.json")
IMAGE_SHA = re.compile(r"sha256:[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")


class ProbeBlocked(ValueError):
    pass


def _read_bounded(path: Path, limit: int) -> bytes:
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise ProbeBlocked("UNSAFE_FILE")
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ProbeBlocked("OVERSIZED_INPUT")
    return content


def _load_json_bytes(data: bytes) -> object:
    def unique(pairs):
        result = {}
        for key, val in pairs:
            if key in result:
                raise ProbeBlocked("DUPLICATE_JSON_KEY")
            result[key] = val
        return result
    try:
        return json.loads(data.decode("utf-8", "strict"),
                          object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              ProbeBlocked("NONFINITE_JSON")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeBlocked("INVALID_JSON") from exc


def inspect_fixture(directory: str | Path) -> dict:
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ProbeBlocked("INVALID_FIXTURE")
    files = {}
    for filename in FIXTURE_FILES:
        data = _read_bounded(directory / filename, MAX_FIXTURE_FILE)
        if not data:
            raise ProbeBlocked("EMPTY_SOURCE")
        files[filename] = hashlib.sha256(data).hexdigest()
    package = _load_json_bytes(_read_bounded(directory / "package.json",
                                              MAX_FIXTURE_FILE))
    if not isinstance(package, dict) or package.get("name") != "qa-rea-static-js-fixture":
        raise ProbeBlocked("UNREVIEWED_FIXTURE")
    return files


def assess(analysis: str | Path, fixture: str | Path, image_digest: str,
           source_sha: str, *, tool_version: str = "6.8.0") -> dict:
    """Validate actual analyzer output bytes, not a predeclared success boolean."""
    if (not isinstance(image_digest, str) or not IMAGE_SHA.fullmatch(image_digest)
            or not isinstance(source_sha, str) or not HEX40.fullmatch(source_sha)
            or tool_version != "6.8.0"):
        raise ProbeBlocked("INVALID_EXECUTION_IDENTITY")
    source_files = inspect_fixture(fixture)
    raw = _read_bounded(Path(analysis), MAX_ANALYSIS)
    if not raw:
        raise ProbeBlocked("EMPTY_ANALYSIS")
    doc = _load_json_bytes(raw)
    if not isinstance(doc, dict) or not doc:
        raise ProbeBlocked("INVALID_ANALYSIS_SHAPE")
    # The REA output shape varies between workflows; insist on explicit file
    # references, not an unbound report claiming success.
    raw_index = json.dumps(doc, ensure_ascii=False, sort_keys=True, allow_nan=False)
    if not all(name in raw_index for name in ("main.js", "pricing.js")):
        raise ProbeBlocked("REQUIRED_MODULES_NOT_OBSERVED")
    return {
        "format": "qa.rea-offline-observation.v1",
        "status": "OBSERVED_FOR_REVIEW",
        "fixture_sha256": source_files,
        "analysis_sha256": hashlib.sha256(raw).hexdigest(),
        "source_sha": source_sha,
        "image_digest": image_digest,
        "rea_version": tool_version,
        "declared_mode": "static-javascript-only",
        "network_enabled": False,
        "provider_bridges_used": False,
        "source_attested_independently": False,
        "findings_security_confirmed": False,
        "hospital_admission": False,
        "production_accepted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--fixture", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        report = assess(args.analysis, args.fixture, args.image_digest,
                        args.source_sha)
        raw = (json.dumps(report, sort_keys=True, ensure_ascii=False,
                          allow_nan=False) + "\n").encode("utf-8")
        with Path(args.out).open("xb") as out:
            out.write(raw)
        print(raw.decode().strip())
        return 0
    except (ProbeBlocked, OSError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc) if
                          isinstance(exc, ProbeBlocked) else "INPUT_UNAVAILABLE",
                          "hospital_admission": False, "production_accepted": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
