"""Review-only readback for real Impeccable 4.8.0 source-detector output.

No UI code is modified or deployed, and a static rule result is not a browser,
accessibility conformance certificate or visual judgment. The paired fixtures
are *synthetic*, not copied from SAHOOL.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat

MAX_JSON = 2 * 1024 * 1024
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")


class UIReviewBlocked(ValueError):
    pass


def read_file(path: str | Path, limit: int) -> bytes:
    path = Path(path)
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise UIReviewBlocked("UNSAFE_INPUT")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise UIReviewBlocked("INPUT_TOO_LARGE")
    if not data:
        raise UIReviewBlocked("EMPTY_INPUT")
    return data


def parse_json(raw: bytes):
    def unique(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise UIReviewBlocked("DUPLICATE_JSON_KEY")
            values[key] = value
        return values
    try:
        return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              UIReviewBlocked("NONFINITE_JSON")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UIReviewBlocked("INVALID_JSON") from exc


def findings_count(record: object) -> int:
    """Require an explicit reported findings collection, never infer success."""
    if isinstance(record, list):
        if all(isinstance(x, dict) for x in record):
            return len(record)
        raise UIReviewBlocked("INVALID_FINDINGS")
    if not isinstance(record, dict):
        raise UIReviewBlocked("INVALID_REPORT")
    for name in ("findings", "issues", "violations"):
        if name in record:
            findings = record[name]
            if not isinstance(findings, list) or not all(isinstance(x, dict) for x in findings):
                raise UIReviewBlocked("INVALID_FINDINGS")
            return len(findings)
    for name in ("results", "files", "targets"):
        if name in record and isinstance(record[name], list):
            return sum(findings_count(item) for item in record[name])
    raise UIReviewBlocked("UNSUPPORTED_DETECTOR_JSON")


def evaluate(baseline_json: str | Path, candidate_json: str | Path,
             baseline_html: str | Path, candidate_html: str | Path,
             baseline_exit: int, candidate_exit: int, image_digest: str,
             source_sha: str) -> dict:
    if (not isinstance(source_sha, str) or not HEX40.fullmatch(source_sha)
            or not isinstance(image_digest, str) or not IMAGE.fullmatch(image_digest)):
        raise UIReviewBlocked("INVALID_EXECUTION_IDENTITY")
    if type(baseline_exit) is not int or type(candidate_exit) is not int:
        raise UIReviewBlocked("INVALID_EXIT_STATUS")
    if baseline_exit != 2 or candidate_exit not in (0, 2):
        raise UIReviewBlocked("DETECTOR_EXECUTION_NOT_PROVEN")
    original = read_file(baseline_html, 128 * 1024)
    corrected = read_file(candidate_html, 128 * 1024)
    if original == corrected:
        raise UIReviewBlocked("NO_FIXTURE_CHANGE")
    baseline = read_file(baseline_json, MAX_JSON)
    candidate = read_file(candidate_json, MAX_JSON)
    before = findings_count(parse_json(baseline))
    after = findings_count(parse_json(candidate))
    if before <= 0 or after >= before:
        raise UIReviewBlocked("NO_MEASURABLE_DETECTOR_IMPROVEMENT")
    if candidate_exit == 0 and after != 0:
        raise UIReviewBlocked("DETECTOR_STATUS_CONFLICT")
    if candidate_exit == 2 and after == 0:
        raise UIReviewBlocked("DETECTOR_STATUS_CONFLICT")
    return {
        "format": "qa.impeccable-ui-observation.v1",
        "status": "OBSERVED_UI_IMPROVEMENT",
        "detector_version": "4.8.0",
        "baseline_primary_findings": before,
        "candidate_primary_findings": after,
        "baseline_exit": baseline_exit,
        "candidate_exit": candidate_exit,
        "baseline_html_sha256": hashlib.sha256(original).hexdigest(),
        "candidate_html_sha256": hashlib.sha256(corrected).hexdigest(),
        "baseline_report_sha256": hashlib.sha256(baseline).hexdigest(),
        "candidate_report_sha256": hashlib.sha256(candidate).hexdigest(),
        "image_digest": image_digest,
        "source_sha": source_sha,
        "mode": "static-html-only",
        "visual_browser_acceptance": False,
        "wcag_conformance_proven": False,
        "sahool_functional_acceptance": False,
        "production_accepted": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-json", required=True)
    parser.add_argument("--candidate-json", required=True)
    parser.add_argument("--baseline-html", required=True)
    parser.add_argument("--candidate-html", required=True)
    parser.add_argument("--baseline-exit", required=True, type=int)
    parser.add_argument("--candidate-exit", required=True, type=int)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        result = evaluate(args.baseline_json, args.candidate_json, args.baseline_html,
                          args.candidate_html, args.baseline_exit, args.candidate_exit,
                          args.image_digest, args.source_sha)
        raw = (json.dumps(result, sort_keys=True, allow_nan=False) + "\n").encode()
        with Path(args.out).open("xb") as f:
            f.write(raw)
        print(raw.decode().strip())
        return 0
    except (UIReviewBlocked, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "BLOCKED",
                          "reason": str(exc) if isinstance(exc, UIReviewBlocked)
                          else "INPUT_UNAVAILABLE", "production_accepted": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
