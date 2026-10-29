"""Process-boundary PostgreSQL recovery test.

The child process claims and commits a job, then exits without finishing.
A fresh parent process must observe RUNNING and refuse re-claim.
This is a controlled process exit, not a SIGKILL/power-loss certification.
"""
import hashlib
import json
import os
import subprocess
import sys
import unittest
import uuid
from qa_lab.pg_queue import PostgresQueue

DSN = os.environ.get("QA_TEST_POSTGRES_DSN")

@unittest.skipUnless(DSN, "requires QA_TEST_POSTGRES_DSN")
class PostgreSQLProcessRecoveryTests(unittest.TestCase):
    def test_child_exits_after_committed_claim(self):
        q = PostgresQueue(DSN)
        q.init()
        key = "qa-child-" + uuid.uuid4().hex
        request = {"idempotency_key": key, "project": "process-boundary"}
        digest = hashlib.sha256(json.dumps(request,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        try:
            run_id = q.submit(request,digest)["run_id"]
            script = """
import os
from qa_lab.pg_queue import PostgresQueue
q=PostgresQueue(os.environ['QA_TEST_POSTGRES_DSN'])
claimed=q.claim('child-worker')
assert claimed is not None
assert claimed['run_id']==os.environ['EXPECTED_RUN_ID']
os._exit(0)
"""
            env = {**os.environ, "EXPECTED_RUN_ID":run_id}
            completed = subprocess.run([sys.executable,"-c",script],env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual(completed.returncode,0,completed.stderr)
            replacement=PostgresQueue(DSN)
            self.assertEqual(replacement.get(run_id)["status"],"RUNNING")
            self.assertEqual(replacement.reconcile(run_id)["action"],"manual-review")
            self.assertIsNone(replacement.claim("replacement-worker"))
            with self.assertRaises(ValueError):
                replacement.finish(run_id,"replacement-worker","PASSED",{})
        finally:
            with q._connect() as db:
                db.execute("DELETE FROM qa_jobs WHERE idempotency_key=%s",(key,))
