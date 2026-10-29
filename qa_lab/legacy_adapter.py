"""Read-only import of explicitly supplied legacy receipts, not legacy code execution."""
import json
from pathlib import Path
from .core import digest

def inspect_legacy_receipt(path):
    p=Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > 2_000_000:
        raise ValueError("unsafe receipt path")
    obj=json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(obj,dict): raise ValueError("receipt must be object")
    return {"source":"unified-local-lab", "path":p.name,"source_digest":digest(obj),"status":"UNVERIFIED_EXTERNAL", "authority":"reference-only"}
