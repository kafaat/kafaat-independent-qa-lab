"""Independent QA execution kernel. No hospital case-management authority."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import time
import uuid

RECIPES = {
    "python-smoke": ["python3", "-c", "print('qa-smoke-ok')"],
    "python-compile": ["python3", "-m", "compileall", "-q", "qa_lab"],
}

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

class Lab:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        self.db = self.root / "lab.sqlite3"
        with closing(self.connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, project TEXT NOT NULL, source_sha TEXT NOT NULL, recipe TEXT NOT NULL, status TEXT NOT NULL, receipt TEXT, created REAL NOT NULL)")
        os.chmod(self.db, 0o600)

    def connect(self):
        db = sqlite3.connect(self.db, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def submit(self, req):
        if not isinstance(req, dict) or set(req) != {"idempotency_key", "project", "source_sha", "recipe"}:
            raise ValueError("exact request keys required")
        key, project, sha, recipe = (req[x] for x in ("idempotency_key", "project", "source_sha", "recipe"))
        if not all(isinstance(v, str) for v in (key, project, sha, recipe)) or not 1 <= len(key) <= 128 or not 1 <= len(project) <= 128:
            raise ValueError("invalid identifiers")
        if not (len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)):
            raise ValueError("source_sha must be 40 lowercase hex chars")
        if recipe not in RECIPES:
            raise ValueError("recipe not allowlisted")
        rh = digest(req)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,request_hash FROM runs WHERE key=?", (key,)).fetchone()
            if row:
                if row[1] != rh: raise ValueError("idempotency key conflict")
                return {"run_id":row[0],"reused":True}
            rid = str(uuid.uuid4())
            db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)", (rid,key,rh,project,sha,recipe,"PENDING",None,time.time()))
            return {"run_id":rid,"reused":False}

    def get(self, rid):
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT id,project,source_sha,recipe,status,receipt FROM runs WHERE id=?", (rid,)).fetchone()
        if not row: raise KeyError(rid)
        return {"run_id":row[0],"project":row[1],"source_sha":row[2],"recipe":row[3],"status":row[4],"receipt":json.loads(row[5]) if row[5] else None}

    def execute(self, rid, workspace, timeout=30):
        workspace=Path(workspace).resolve(strict=True)
        if not workspace.is_dir(): raise ValueError("workspace must be a directory")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row=db.execute("SELECT project,source_sha,recipe,status FROM runs WHERE id=?",(rid,)).fetchone()
            if not row: raise KeyError(rid)
            if row[3] != "PENDING": raise ValueError("run is not pending; reconcile before retry")
            claimed=db.execute("UPDATE runs SET status='RUNNING' WHERE id=? AND status='PENDING'",(rid,)).rowcount
            if claimed != 1: raise ValueError("run already claimed")
        project,sha,recipe,_=row
        try:
            self._attest_checkout(workspace, sha)
        except (ValueError, OSError) as exc:
            return self._finish_blocked_claim(rid, project, sha, recipe, str(exc))
        started=time.time()
        try:
            proc=subprocess.run(RECIPES[recipe],cwd=workspace,capture_output=True,text=True,timeout=min(int(timeout),120),env={"PATH":os.environ.get("PATH","/usr/bin:/bin"),"PYTHONDONTWRITEBYTECODE":"1"})
            status="PASSED" if proc.returncode == 0 else "FAILED"
            code=proc.returncode
            stdout=proc.stdout[-16000:]
            stderr=proc.stderr[-16000:]
        except subprocess.TimeoutExpired:
            status,code,stdout,stderr="TIMED_OUT",None,"","recipe timeout"
        except (OSError, UnicodeError) as exc:
            status,code,stdout,stderr="BLOCKED",None,"",f"runner unavailable: {type(exc).__name__}"
        receipt={"schema_version":"1.0","run_id":rid,"project":project,"source_sha":sha,"recipe":recipe,"status":status,"exit_code":code,"started_at":started,"finished_at":time.time(),"stdout_tail":stdout,"stderr_tail":stderr,"workspace":str(workspace),"environment_claim":"local-unattested","authority":"execution-evidence-only"}
        receipt["sha256"]=digest(receipt)
        with closing(self.connect()) as db, db:
            changed=db.execute("UPDATE runs SET status=?,receipt=? WHERE id=? AND status='RUNNING'",(status,canonical(receipt),rid)).rowcount
            if changed != 1: raise RuntimeError("run state changed during execution; receipt not committed")
        return receipt

    def _attest_checkout(self, workspace, sha):
        if os.environ.get("QA_REQUIRE_GIT_SHA", "0") != "1":
            return
        try:
            actual=subprocess.run(["git","rev-parse","HEAD"],cwd=workspace,capture_output=True,text=True,timeout=5,check=True).stdout.strip()
            dirty=subprocess.run(["git","status","--porcelain"],cwd=workspace,capture_output=True,text=True,timeout=5,check=True).stdout.strip()
        except (OSError,subprocess.SubprocessError) as exc:
            raise ValueError("unable to attest checkout") from exc
        if actual != sha or dirty:
            raise ValueError("checkout SHA mismatch or dirty tree")

    def _finish_blocked_claim(self, rid, project, sha, recipe, reason):
        receipt={"schema_version":"1.0","run_id":rid,"project":project,"source_sha":sha,"recipe":recipe,"status":"BLOCKED","reason":reason[:500],"authority":"execution-evidence-only","environment_claim":"local-unattested"}
        receipt["sha256"]=digest(receipt)
        with closing(self.connect()) as db, db:
            changed=db.execute("UPDATE runs SET status='BLOCKED',receipt=? WHERE id=? AND status='RUNNING'",(canonical(receipt),rid)).rowcount
            if changed != 1: raise RuntimeError("claim lost; manual reconciliation required")
        return receipt

    def reject(self, rid, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason required")
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT project,source_sha,recipe,status FROM runs WHERE id=?", (rid,)).fetchone()
            if not row: raise KeyError(rid)
            if row[3] != "PENDING": raise ValueError("claimed or terminal run requires manual reconciliation")
            receipt = {"schema_version":"1.0", "run_id":rid,"project":row[0],"source_sha":row[1],"recipe":row[2],"status":"BLOCKED", "reason":reason[:500],"authority":"execution-evidence-only", "environment_claim":"local-unattested"}
            receipt["sha256"] = digest(receipt)
            db.execute("UPDATE runs SET status='BLOCKED',receipt=? WHERE id=?",(canonical(receipt),rid))
            return receipt

    def reconcile(self, rid):
        run = self.get(rid)
        return {"run_id":rid,"status":run["status"],"action":"manual-review" if run["status"]=="RUNNING" else "none", "authority":"execution-evidence-only"}

    def verify(self,rid):
        receipt=self.get(rid)["receipt"]
        if receipt is None: return False
        return digest({k:v for k,v in receipt.items() if k!="sha256"}) == receipt["sha256"]
