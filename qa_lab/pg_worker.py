"""Experimental PostgreSQL worker with allowlisted, non-shell smoke recipe.

Does not run untrusted project code. A committed claim is not retried
automatically after interruption; operator reconciliation is required.
"""
import os
import socket
import subprocess
import sys
import time
import uuid
from .pg_queue import PostgresQueue

def execute_one(queue, worker_id):
    job=queue.claim(worker_id)
    if not job:return None
    request=job['request']
    rid=job['run_id']
    if not isinstance(request,dict) or request.get('recipe')!='python-smoke':
        return queue.finish(rid,worker_id,'BLOCKED',{'status':'BLOCKED','reason':'recipe not allowlisted','authority':'execution-evidence-only'})
    try:
        proc=subprocess.run([sys.executable,'-c',"print('qa-smoke-ok')"],capture_output=True,text=True,timeout=10)
        status='PASSED' if proc.returncode==0 else 'FAILED'
        receipt={'status':status,'exit_code':proc.returncode,'stdout_tail':proc.stdout[-1024:],'stderr_tail':proc.stderr[-1024:],'source_sha':request.get('source_sha'),'recipe':'python-smoke','authority':'execution-evidence-only','environment_claim':'unattested-worker'}
    except subprocess.TimeoutExpired:
        status='TIMED_OUT'
        receipt={'status':status,'reason':'timeout','authority':'execution-evidence-only','environment_claim':'unattested-worker'}
    return queue.finish(rid,worker_id,status,receipt)

def main():
    dsn=os.environ.get('QA_POSTGRES_DSN')
    if not dsn:raise RuntimeError('QA_POSTGRES_DSN required')
    queue=PostgresQueue(dsn)
    queue.init()
    worker_id=f'{socket.gethostname()}-{uuid.uuid4()}'
    while True:
        if execute_one(queue,worker_id) is None:
            time.sleep(2)

if __name__=='__main__':main()
