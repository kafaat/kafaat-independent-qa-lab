"""Deterministic advisory triage; never changes test verdict."""
def classify(run):
    status = run.get("status", "UNKNOWN")
    receipt = run.get("receipt") or {}
    if status == "PASSED":
        category, action = "passed", "none"
    elif status == "TIMED_OUT":
        category, action = "timeout", "inspect runtime and resource limits"
    elif status == "BLOCKED":
        category, action = "environment", "inspect prerequisites and attestation"
    elif status == "FAILED":
        category, action = "test_failure", "inspect test logs and reproduce"
    else:
        category, action = "unresolved", "wait or reconcile"
    return {"run_id":run.get("run_id"),"status":status,"category":category,"recommended_action":action,"authority":"advisory-only"}
