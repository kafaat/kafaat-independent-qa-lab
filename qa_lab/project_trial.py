"""Opt-in, offline, source-verified project trial in a disposable Docker sandbox.

This tool is NOT a Railway API recipe and grants no Hospital or release authority.
It verifies a clean Git checkout, copies only tracked regular files, and runs an
allowlisted unittest command in a restricted container with network disabled.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import uuid

SHA = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
IMAGE = re.compile(r"[a-zA-Z0-9._/:-]+@sha256:[0-9a-f]{64}\Z")
IDENTIFIERS = ("tenant_id", "project_id", "case_id", "cycle_no", "snapshot_id",
               "attempt_no", "criteria_digest", "source_sha")
MAX_FILES = 5000
MAX_SOURCE_BYTES = 32 * 1024 * 1024


class TrialRejected(ValueError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def validate_binding(binding: object) -> dict:
    if not isinstance(binding, dict) or set(binding) != set(IDENTIFIERS):
        raise TrialRejected("BINDING_FIELDS_MISMATCH")
    for field in IDENTIFIERS:
        value = binding[field]
        if field in ("cycle_no", "attempt_no"):
            if type(value) is not int or not 1 <= value <= 2**31 - 1:
                raise TrialRejected("INVALID_BINDING:" + field)
        elif not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise TrialRejected("INVALID_BINDING:" + field)
    if not SHA.fullmatch(binding["source_sha"]):
        raise TrialRejected("INVALID_SOURCE_SHA")
    if not DIGEST.fullmatch(binding["criteria_digest"]):
        raise TrialRejected("INVALID_CRITERIA_DIGEST")
    return dict(binding)


def _git(repo: Path, *args: str) -> bytes:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1",
           "GIT_TERMINAL_PROMPT": "0"}
    try:
        p = subprocess.run(["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args], env=env,
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise TrialRejected("GIT_SOURCE_UNAVAILABLE") from exc
    return p.stdout


def capture(repo: str | Path, expected_sha: str) -> tuple[dict, list[tuple[str, bytes]]]:
    """Return bytes of tracked regular files and their content-pinned manifest."""
    root = Path(repo)
    if root.is_symlink() or not root.is_dir() or not isinstance(expected_sha, str) or not SHA.fullmatch(expected_sha):
        raise TrialRejected("INVALID_SOURCE_ROOT_OR_SHA")
    root = root.resolve(strict=True)
    if Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel").strip())).resolve() != root:
        raise TrialRejected("NOT_GIT_ROOT")
    actual_sha = _git(root, "rev-parse", "--verify", "HEAD").decode().strip()
    if actual_sha != expected_sha:
        raise TrialRejected("SOURCE_COMMIT_MISMATCH")
    if _git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise TrialRejected("SOURCE_DIRTY")
    rows = _git(root, "ls-files", "--stage", "-z").split(b"\0")
    entries = []
    total = 0
    for raw in rows:
        if not raw:
            continue
        try:
            metadata, name_bytes = raw.split(b"\t", 1)
            mode, _, stage = metadata.split(b" ")
            name = name_bytes.decode("utf-8", "strict")
        except (ValueError, UnicodeError) as exc:
            raise TrialRejected("INVALID_TRACKED_NAME") from exc
        if (mode not in (b"100644", b"100755") or stage != b"0" or
                name.startswith("/") or "\\" in name or "\0" in name or
                any(part in ("", ".", "..") for part in name.split("/"))):
            raise TrialRejected("UNSAFE_TRACKED_ENTRY")
        path = root / name
        if not path.is_file() or path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise TrialRejected("UNSAFE_TRACKED_ENTRY")
        # Reject symlinked intermediate directories, too.
        current = path.parent
        while current != root:
            if current.is_symlink():
                raise TrialRejected("UNSAFE_TRACKED_ENTRY")
            current = current.parent
        data = path.read_bytes()
        total += len(data)
        if total > MAX_SOURCE_BYTES or len(entries) >= MAX_FILES:
            raise TrialRejected("SOURCE_LIMIT")
        entries.append((name, data))
    if not entries:
        raise TrialRejected("EMPTY_SOURCE")
    files = [{"path": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
             for name, data in sorted(entries)]
    tree_sha = _git(root, "rev-parse", "HEAD^{tree}").decode().strip()
    manifest = {"format": "qa.git-source-manifest.v1", "source_sha": actual_sha,
                "git_tree_sha": tree_sha, "file_count": len(files), "total_bytes": total,
                "files": files}
    manifest["manifest_sha256"] = hashlib.sha256(canonical(manifest)).hexdigest()
    return manifest, entries


def _copy_tracked(entries: list[tuple[str, bytes]], destination: Path) -> None:
    destination.chmod(0o755)
    for name, data in entries:
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o755)
        path.write_bytes(data)
        path.chmod(0o444)


def docker_argv(image: str, source_dir: Path, name: str) -> list[str]:
    if not isinstance(image, str) or not IMAGE.fullmatch(image):
        raise TrialRejected("IMAGE_DIGEST_REQUIRED")
    if not re.fullmatch(r"qa-trial-[0-9a-f]{20}", name):
        raise TrialRejected("INVALID_RUN_ID")
    return ["docker", "run", "--rm", "--pull=never", "--name", name,
            "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=64",
            "--memory=256m", "--cpus=1", "--user=65534:65534",
            "--tmpfs=/tmp:rw,nosuid,nodev,size=32m",
            "--env=PYTHONDONTWRITEBYTECODE=1", "--env=PYTHONPATH=/src",
            "--mount", f"type=bind,source={source_dir},target=/src,readonly",
            "--workdir=/src", image,
            "python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]


def execute(repo: str | Path, binding: dict, image: str, key: bytes,
            *, timeout: int = 90) -> dict:
    """Run reviewed source inside Docker. Exits never grant Hospital authority."""
    b = validate_binding(binding)
    if type(key) is not bytes or len(key) < 32:
        raise TrialRejected("INVALID_SIGNING_KEY")
    if type(timeout) is not int or not 5 <= timeout <= 300:
        raise TrialRejected("INVALID_TIMEOUT")
    before, entries = capture(repo, b["source_sha"])
    name = "qa-trial-" + uuid.uuid4().hex[:20]
    with tempfile.TemporaryDirectory(prefix="qa-source-trial-") as temporary:
        source = Path(temporary) / "source"
        source.mkdir()
        _copy_tracked(entries, source)
        cmd = docker_argv(image, source, name)
        # The parent never passes Docker credentials, project secrets or a network.
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": temporary,
               "DOCKER_CONFIG": str(Path(temporary) / "empty-docker-config")}
        (Path(temporary) / "empty-docker-config").mkdir()
        with tempfile.TemporaryFile() as log:
            try:
                proc = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, close_fds=True)
                try:
                    returncode = proc.wait(timeout=timeout)
                    timed_out = False
                except subprocess.TimeoutExpired:
                    cleanup = subprocess.run(["docker", "rm", "-f", name], env=env,
                                             capture_output=True, timeout=15, check=False)
                    if cleanup.returncode != 0:
                        proc.kill()
                        proc.wait(timeout=10)
                        raise TrialRejected("SANDBOX_CLEANUP_UNCONFIRMED")
                    proc.kill()
                    proc.wait(timeout=10)
                    returncode, timed_out = -1, True
            except (OSError, subprocess.SubprocessError) as exc:
                raise TrialRejected("SANDBOX_UNAVAILABLE_OR_CLEANUP_UNKNOWN") from exc
            log.seek(0)
            output = log.read(1024 * 1024 + 1)
            if len(output) > 1024 * 1024:
                raise TrialRejected("SANDBOX_LOG_LIMIT")
    after, _ = capture(repo, b["source_sha"])
    if before != after:
        raise TrialRejected("SOURCE_CHANGED_DURING_EXECUTION")
    text = output.decode("utf-8", "replace")
    match = re.search(r"(?m)^Ran ([0-9]+) tests? in ", text)
    count = int(match.group(1)) if match else 0
    has_skips = bool(re.search(r"\(skipped=|skipped [0-9]+", text))
    passed = not timed_out and returncode == 0 and count > 0 and not has_skips and bool(
        re.search(r"(?m)^OK\s*$", text))
    payload = {"format": "qa.reviewed-project-trial.v1", "binding": b,
               "source_manifest": before, "sandbox": "docker-no-network-read-only-capdrop",
               "image": image, "recipe": "python-unittest-discover-v1",
               "result": "PASSED" if passed else "TIMED_OUT" if timed_out else "FAILED",
               "tests_observed": count, "exit_code": returncode, "skipped_detected": has_skips,
               "output_sha256": hashlib.sha256(output).hexdigest(),
               "source_verified_by_runner": True,
               "source_attested_independently": False,
               "production_accepted": False, "authority": "execution-evidence-only"}
    signature = hmac.new(key, canonical(payload), hashlib.sha256).hexdigest()
    return {"payload": payload, "signature": {"algorithm": "hmac-sha256", "value": signature}}


def verify(envelope: object, expected_binding: object, key: bytes, *,
           expected_manifest_sha256: str, expected_image: str,
           minimum_tests: int) -> dict:
    """Offline independent matching only; never imports a result into Hospital."""
    errors = []
    try:
        binding = validate_binding(expected_binding)
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
            raise TrialRejected("INVALID_ENVELOPE")
        payload, sig = envelope["payload"], envelope["signature"]
        if not isinstance(payload, dict) or not isinstance(sig, dict):
            raise TrialRejected("INVALID_ENVELOPE")
        if set(sig) != {"algorithm", "value"} or sig["algorithm"] != "hmac-sha256":
            raise TrialRejected("INVALID_SIGNATURE")
        if not isinstance(sig["value"], str) or not DIGEST.fullmatch(sig["value"]):
            raise TrialRejected("INVALID_SIGNATURE")
        if type(key) is not bytes or len(key) < 32 or not hmac.compare_digest(
                hmac.new(key, canonical(payload), hashlib.sha256).hexdigest(), sig["value"]):
            raise TrialRejected("SIGNATURE_MISMATCH")
        if not isinstance(payload.get("binding"), dict):
            raise TrialRejected("BINDING_MISMATCH")
        if validate_binding(payload["binding"]) != binding:
            raise TrialRejected("BINDING_MISMATCH")
        src = payload.get("source_manifest")
        if not isinstance(src, dict) or src.get("source_sha") != binding["source_sha"]:
            raise TrialRejected("SOURCE_MISMATCH")
        digest = src.get("manifest_sha256")
        source_without_hash = {k: v for k, v in src.items() if k != "manifest_sha256"}
        if not isinstance(digest, str) or not hmac.compare_digest(
                hashlib.sha256(canonical(source_without_hash)).hexdigest(), digest):
            raise TrialRejected("SOURCE_MANIFEST_MISMATCH")
        if not isinstance(expected_manifest_sha256, str) or not DIGEST.fullmatch(expected_manifest_sha256):
            raise TrialRejected("EXPECTED_MANIFEST_REQUIRED")
        if not hmac.compare_digest(digest, expected_manifest_sha256):
            raise TrialRejected("SOURCE_MANIFEST_NOT_EXPECTED")
        if not isinstance(expected_image, str) or not IMAGE.fullmatch(expected_image) or payload.get("image") != expected_image:
            raise TrialRejected("IMAGE_MISMATCH")
        if type(minimum_tests) is not int or minimum_tests <= 0:
            raise TrialRejected("MINIMUM_TESTS_REQUIRED")
        if (payload.get("format") != "qa.reviewed-project-trial.v1"
                or payload.get("authority") != "execution-evidence-only"
                or payload.get("source_verified_by_runner") is not True
                or payload.get("source_attested_independently") is not False
                or payload.get("production_accepted") is not False
                or payload.get("sandbox") != "docker-no-network-read-only-capdrop"
                or payload.get("recipe") != "python-unittest-discover-v1"
                or not isinstance(payload.get("image"), str)
                or not IMAGE.fullmatch(payload["image"])):
            raise TrialRejected("INVALID_EXECUTION_CLAIMS")
        if (payload.get("result") != "PASSED" or type(payload.get("tests_observed")) is not int
                or payload["tests_observed"] < minimum_tests or type(payload.get("exit_code")) is not int
                or payload["exit_code"] != 0 or payload.get("skipped_detected") is not False
                or not isinstance(payload.get("output_sha256"), str)
                or not DIGEST.fullmatch(payload["output_sha256"])):
            raise TrialRejected("INCOMPLETE_TEST_EVIDENCE")
    except (TrialRejected, TypeError, ValueError, OverflowError, KeyError) as exc:
        errors.append(str(exc) if isinstance(exc, TrialRejected) else "INVALID_EVIDENCE")
    return {"format": "qa.project-trial-verification.v1", "status": "MATCHED" if not errors else "REJECTED",
            "reasons": errors, "hospital_admission": False, "production_accepted": False,
            "source_independently_attested": False}


def main() -> int:
    """CLI for reviewed, locally mounted source only; no network or dispatch."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--image", required=True, help="Locally cached image@sha256 digest")
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    try:
        key_path = Path(args.key_file)
        st = key_path.lstat()
        if not stat.S_ISREG(st.st_mode) or (st.st_mode & 0o077) or key_path.is_symlink():
            raise TrialRejected("PRIVATE_KEY_FILE_REQUIRED")
        if hasattr(os, "getuid") and st.st_uid != os.getuid():
            raise TrialRejected("PRIVATE_KEY_FILE_REQUIRED")
        key = bytes.fromhex(key_path.read_text(encoding="ascii").strip())
        doc = Path(args.binding).read_bytes()
        if len(doc) > 32768:
            raise TrialRejected("BINDING_FILE_TOO_LARGE")
        def pairs(items):
            d = {}
            for k, v in items:
                if k in d:
                    raise TrialRejected("DUPLICATE_JSON_KEY")
                d[k] = v
            return d
        binding = json.loads(doc, object_pairs_hook=pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(TrialRejected("NONFINITE_JSON")))
        receipt = execute(args.repo, binding, args.image, key, timeout=args.timeout)
        dest = Path(args.output).absolute()
        source = Path(args.repo).resolve(strict=True)
        if dest == source or source in dest.parents:
            raise TrialRejected("OUTPUT_WITHIN_SOURCE")
        encoded = canonical(receipt) + b"\n"
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        print(json.dumps({"status": receipt["payload"]["result"],
                          "receipt_sha256": hashlib.sha256(encoded).hexdigest(),
                          "authority": "execution-evidence-only", "hospital_admission": False}))
        return 0 if receipt["payload"]["result"] == "PASSED" else 1
    except (TrialRejected, ValueError, OSError, UnicodeError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc) if isinstance(exc, TrialRejected)
                          else "INPUT_OR_EXECUTION_REJECTED", "hospital_admission": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
