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
from .redaction import redact_tail, summary as redaction_summary

def execute_one(queue, worker_id):
    job=queue.claim(worker_id)
    if not job:return None
    request=job['request']
    rid=job['run_id']
    if isinstance(request,dict) and request.get('recipe')=='audio-pcm-v1':
        from .audio_jobs import enabled, execute, digest
        if not enabled():
            return queue.finish(rid,worker_id,'BLOCKED',{'status':'BLOCKED','reason':'audio recipe disabled','authority':'execution-evidence-only'})
        try:
            receipt=execute(request)
        except Exception as exc:  # keep the worker alive and the claim closed
            return queue.finish(rid,worker_id,'BLOCKED',{'status':'BLOCKED','reason':f'audio runner error: {type(exc).__name__}','authority':'execution-evidence-only'})
        receipt.pop('receipt_sha256',None)
        receipt.update(run_id=rid,worker_id=worker_id)
        receipt['receipt_sha256']=digest(receipt)
        return queue.finish(rid,worker_id,receipt['status'],receipt)
    if not isinstance(request,dict) or request.get('recipe')!='python-smoke':
        return queue.finish(rid,worker_id,'BLOCKED',{'status':'BLOCKED','reason':'recipe not allowlisted','authority':'execution-evidence-only'})
    try:
        proc=subprocess.run([sys.executable,'-c',"print('qa-smoke-ok')"],capture_output=True,text=True,timeout=10)
        status='PASSED' if proc.returncode==0 else 'FAILED'
        stdout,out_counts=redact_tail(proc.stdout,1024)
        stderr,err_counts=redact_tail(proc.stderr,1024)
        receipt={'status':status,'exit_code':proc.returncode,'stdout_tail':stdout,'stderr_tail':stderr,'output_redaction':redaction_summary(out_counts,err_counts),'source_sha':request.get('source_sha'),'recipe':'python-smoke','authority':'execution-evidence-only','environment_claim':'unattested-worker'}
    except subprocess.TimeoutExpired:
        status='TIMED_OUT'
        receipt={'status':status,'reason':'timeout','authority':'execution-evidence-only','environment_claim':'unattested-worker'}
    except (OSError, UnicodeError) as exc:
        status='BLOCKED'
        receipt={'status':status,'reason':f'runner unavailable: {type(exc).__name__}','authority':'execution-evidence-only','environment_claim':'unattested-worker'}
    return queue.finish(rid,worker_id,status,receipt)

MAX_BACKOFF_S=60


def transient_db_errors():
    """Connection-level database errors to wait out (DNS, refused, dropped)."""
    try:
        import psycopg
    except ImportError:
        return ()
    return (psycopg.OperationalError,)


def _log(message):
    print(message, file=sys.stderr, flush=True)


def run(queue, worker_id, *, transient=None, sleep=time.sleep, idle_s=2, iterations=None):
    """Poll forever; wait out transient database errors with capped backoff.

    A transient error before a claim leaves the queue unchanged. One after a
    claim (while finishing) leaves that job RUNNING for the existing operator
    reconciliation; the worker keeps serving other jobs instead of exiting,
    which on Railway exhausted the restart budget and left the service CRASHED.
    Only the exception type is logged, never the DSN.
    """
    transient=transient_db_errors() if transient is None else transient
    initialised=False
    failures=0
    count=0
    while iterations is None or count<iterations:
        count+=1
        try:
            if not initialised:
                queue.init()
                initialised=True
            idle=execute_one(queue,worker_id) is None
            failures=0
        except transient as exc:
            failures+=1
            delay=min(MAX_BACKOFF_S,idle_s*(2**min(failures-1,6)))
            _log(f'pg_worker: database unavailable ({type(exc).__name__}); retry {failures} in {delay}s')
            sleep(delay)
            continue
        if idle:
            sleep(idle_s)


def main():
    dsn=os.environ.get('QA_POSTGRES_DSN')
    if not dsn:raise RuntimeError('QA_POSTGRES_DSN required')
    run(PostgresQueue(dsn),f'{socket.gethostname()}-{uuid.uuid4()}')

if __name__=='__main__':main()
