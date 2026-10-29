"""POSIX SIGKILL recovery test on a real PostgreSQL instance.

The child sends a ready marker only after the database claim commits.
Parent kills the child and verifies no second worker can claim the same job.
"""
import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import unittest
import uuid
from qa_lab.pg_queue import PostgresQueue

DSN=os.environ.get("QA_TEST_POSTGRES_DSN")

@unittest.skipUnless(DSN and os.name=="posix","requires PostgreSQL and POSIX")
class PostgreSQLKilledWorkerTests(unittest.TestCase):
    def test_sigkill_after_claim_commit(self):
        q=PostgresQueue(DSN);q.init()
        key="qa-sigkill-"+uuid.uuid4().hex
        req={"idempotency_key":key,"project":"sigkill"}
        sha=hashlib.sha256(json.dumps(req,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        child=None
        try:
            rid=q.submit(req,sha)["run_id"]
            script="""
import os,time
from qa_lab.pg_queue import PostgresQueue
q=PostgresQueue(os.environ['QA_TEST_POSTGRES_DSN'])
claimed=q.claim('sigkill-worker')
assert claimed and claimed['run_id']==os.environ['EXPECTED_RUN_ID']
print('CLAIM_COMMITTED',flush=True)
time.sleep(60)
"""
            child=subprocess.Popen([sys.executable,"-u","-c",script],env={**os.environ,"EXPECTED_RUN_ID":rid},stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            ready,_,_=select.select([child.stdout],[],[],10)
            self.assertTrue(ready,"worker did not confirm committed claim")
            self.assertEqual(child.stdout.readline().strip(),"CLAIM_COMMITTED")
            os.kill(child.pid,signal.SIGKILL)
            child.wait(timeout=10)
            self.assertEqual(child.returncode,-signal.SIGKILL)
            replacement=PostgresQueue(DSN)
            self.assertEqual(replacement.get(rid)["status"],"RUNNING")
            self.assertEqual(replacement.reconcile(rid)["action"],"manual-review")
            self.assertIsNone(replacement.claim("replacement-worker"))
            with self.assertRaises(ValueError):
                replacement.finish(rid,"replacement-worker","PASSED",{})
        finally:
            if child and child.poll() is None:
                child.kill();child.wait(timeout=10)
            if child is not None:
                if child.stdout is not None:
                    child.stdout.close()
                if child.stderr is not None:
                    child.stderr.close()
            with q._connect() as db:
                db.execute("DELETE FROM qa_jobs WHERE idempotency_key=%s",(key,))
