"""Independent boundary contract; hospital decisions are never accepted."""
FORBIDDEN = frozenset({"close_case", "merge", "deploy", "approve_release", "case_status", "hospital_decision"})
REQUIRED = frozenset({"run_id", "project", "source_sha", "recipe", "status", "authority"})

def validate_evidence(receipt):
    if not isinstance(receipt, dict) or not REQUIRED.issubset(receipt):
        raise ValueError("missing evidence fields")
    if FORBIDDEN.intersection(receipt):
        raise ValueError("hospital authority forbidden")
    if receipt["authority"] != "execution-evidence-only":
        raise ValueError("authority mismatch")
    if receipt["status"] not in {"PASSED", "FAILED", "TIMED_OUT", "BLOCKED"}:
        raise ValueError("invalid evidence status")
    return True
