#!/usr/bin/env python3
"""Targeted negative controls: each named mutation must make a named test fail.

Method ported from kafaat/unified-local-lab scripts/verify_066_mutations.py
(pytest there, unittest here): every mutant runs in a disposable copy of the
tracked source; the baseline must pass first; only a run whose result is
failures>0 with no errors and no skips counts as KILLED, so an import error,
a crash, a skip or a timeout never counts. The checkout is never modified.
This is a list of controls for specific guarantees, not a mutation score.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SUMMARY = re.compile(r"^Ran (\d+) tests? in", re.M)
_COUNTS = re.compile(r"(failures|errors|skipped|expected failures|unexpected successes)=(\d+)")


def tracked_files(root):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    return [name for name in out.stdout.decode().split("\0") if name]


def load_controls(path):
    controls = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(controls, list) or not controls:
        raise ValueError("a non-empty list of controls is required")
    keys = {"id", "path", "old", "new", "test", "guards"}
    for control in controls:
        if not isinstance(control, dict) or set(control) != keys:
            raise ValueError(f"each control needs exactly {sorted(keys)}")
        rel = Path(control["path"])
        if rel.is_absolute() or ".." in rel.parts or control["old"] == control["new"]:
            raise ValueError(f"bad control: {control.get('id')}")
    if len({c["id"] for c in controls}) != len(controls):
        raise ValueError("duplicate control id")
    return controls


def run_tests(workdir, tests, timeout):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(workdir),
           "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(workdir)}
    try:
        proc = subprocess.run([sys.executable, "-B", "-m", "unittest", *tests], cwd=workdir, env=env,
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"returncode": None, "timed_out": True, "ran": 0, "counts": {}}
    ran = _SUMMARY.search(proc.stderr)
    lines = proc.stderr.strip().splitlines()
    last = lines[-1] if lines else ""
    counts = {key: int(value) for key, value in _COUNTS.findall(last)}
    return {"returncode": proc.returncode, "timed_out": False, "ran": int(ran.group(1)) if ran else 0,
            "counts": counts, "ok": last == "OK" or last.startswith("OK (")}


def copy_tree(root, files, target):
    for name in files:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, destination)


def digest_files(root, files):
    return hashlib.sha256(b"".join(name.encode() + b"\0" + (root / name).read_bytes()
                                   for name in sorted(files))).hexdigest()


def verify(root, controls, timeout):
    root = Path(root).resolve(strict=True)
    files = tracked_files(root)
    before = digest_files(root, files)
    rows = []
    with tempfile.TemporaryDirectory() as scratch:
        base = Path(scratch) / "baseline"
        copy_tree(root, files, base)
        baseline = run_tests(base, sorted({c["test"] for c in controls}), timeout)
        baseline_ok = (baseline["returncode"] == 0 and baseline["ran"] > 0 and baseline.get("ok")
                       and not baseline["counts"].get("skipped"))
        if baseline_ok:
            for control in controls:
                work = Path(scratch) / ("work-" + control["id"])
                copy_tree(root, files, work)
                target = work / control["path"]
                text = target.read_text(encoding="utf-8")
                if text.count(control["old"]) != 1:
                    rows.append({"id": control["id"], "status": "ANCHOR_NOT_UNIQUE"})
                    continue
                target.write_text(text.replace(control["old"], control["new"]), encoding="utf-8")
                result = run_tests(work, [control["test"]], timeout)
                counts = result["counts"]
                killed = (result["returncode"] == 1 and result["ran"] > 0 and counts.get("failures", 0) > 0
                          and not counts.get("errors") and not counts.get("skipped"))
                rows.append({"id": control["id"], "guards": control["guards"], "test": control["test"],
                             "status": "KILLED" if killed else "SURVIVED_OR_INVALID", **result})
                shutil.rmtree(work)
    unchanged = digest_files(root, files) == before
    passed = baseline_ok and unchanged and len(rows) == len(controls) and all(
        r["status"] == "KILLED" for r in rows)
    return {"status": "PASSED" if passed else "FAILED", "baseline": baseline, "controls": rows,
            "source_unchanged": unchanged, "scope": "targeted negative controls, not a mutation score",
            "production_accepted": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--controls", default="tools/mutation/controls.json")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output", help="write the full JSON result here")
    args = parser.parse_args(argv)
    controls = load_controls(Path(args.project_root) / args.controls)
    result = verify(args.project_root, controls, args.timeout)
    for row in result["controls"]:
        print(f"{row['id']}: {row['status']}", flush=True)
    if args.output:
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "controls": len(result["controls"]),
                      "source_unchanged": result["source_unchanged"]}))
    return 0 if result["status"] == "PASSED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
