"""Check the QA NATS container startup contract without starting any services."""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]


def verify(dockerfile: str, service_start: str | None) -> list[str]:
    errors = []
    lines = [x.strip() for x in dockerfile.splitlines() if x.strip() and not x.lstrip().startswith("#")]
    entrypoints = [x for x in lines if x.startswith("ENTRYPOINT ")]
    commands = [x for x in lines if x.startswith("CMD ")]
    if len(entrypoints) != 1 or len(commands) != 1:
        return ["NATS_ENTRYPOINT_CMD_REQUIRED"]
    try:
        entry = json.loads(entrypoints[0][len("ENTRYPOINT "):])
        cmd = json.loads(commands[0][len("CMD "):])
    except (ValueError, json.JSONDecodeError):
        return ["NATS_EXEC_FORM_REQUIRED"]
    if entry != ["nats-server"] or cmd != ["-js", "-sd", "/data"]:
        errors.append("NATS_DOCKER_CONTRACT_CHANGED")
    # Railway starts a Dockerfile service using its own custom command semantics:
    # do not allow a second command that could override the image entrypoint.
    if service_start is not None and service_start.strip():
        errors.append("RAILWAY_START_OVERRIDE_MUST_BE_UNSET")
    return errors


def main() -> int:
    file = ROOT / "Dockerfile.nats"
    errors = verify(file.read_text(encoding="utf-8"), None)
    print(json.dumps({"result": "PASS" if not errors else "BLOCKED", "errors": errors,
        "railway_requirement": "Remove custom start command; retain Dockerfile ENTRYPOINT/CMD and /data volume",
        "dispatch": False, "deployment": False}, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
