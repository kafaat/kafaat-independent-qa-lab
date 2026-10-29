"""Real HTTP request -> PostgreSQL -> separate worker process -> HTTP read.

Uses a loopback server with ephemeral port and a dedicated PostgreSQL CI DB.
"""
import hashlib
import http.client
import json
import os
import subprocess
import sys
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from qa_lab.pg_api import PostgreSQLHandler
from qa_lab.pg_queue import PostgresQueue

DSN=os.environ.get('QA_TEST_POSTGRES_DSN')

@unittest.skipUnless(DSN,'requires real PostgreSQL')
class PostgreSQLHTTPFlowTests(unittest.TestCase):
    def test_http_to_separate_worker(self):
        q=PostgresQueue(DSN);q.init()
        key='qa-http-'+uuid.uuid4().hex
        token='qa-test-token-'+'x'*32
        handler=type('TestPGHandler',(PostgreSQLHandler,),{'queue':q})
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        previous=os.environ.get('QA_API_TOKEN')
        os.environ['QA_API_TOKEN']=token
        connection=http.client.HTTPConnection('127.0.0.1',server.server_address[1],timeout=10)
        try:
            request={'idempotency_key':key,'project':'http-flow','source_sha':'a'*40,'recipe':'python-smoke'}
            connection.request('POST','/v1/runs',body=json.dumps(request),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
            response=connection.getresponse()
            self.assertEqual(response.status,202)
            run_id=json.loads(response.read())['run_id']
            code="""
import os
from qa_lab.pg_queue import PostgresQueue
from qa_lab.pg_worker import execute_one
q=PostgresQueue(os.environ['QA_TEST_POSTGRES_DSN'])
assert execute_one(q,'http-worker')['status']=='PASSED'
"""
            worker=subprocess.run([sys.executable,'-c',code],env=os.environ.copy(),capture_output=True,text=True,timeout=20)
            self.assertEqual(worker.returncode,0,worker.stderr)
            connection.request('GET','/v1/runs/'+run_id,headers={'Authorization':'Bearer '+token})
            response=connection.getresponse()
            self.assertEqual(response.status,200)
            body=json.loads(response.read())
            self.assertEqual(body['status'],'PASSED')
            self.assertEqual(body['receipt']['authority'],'execution-evidence-only')
        finally:
            connection.close()
            server.shutdown();server.server_close();thread.join(timeout=5)
            if previous is None:os.environ.pop('QA_API_TOKEN',None)
            else:os.environ['QA_API_TOKEN']=previous
            with q._connect() as db:
                db.execute('DELETE FROM qa_jobs WHERE idempotency_key=%s',(key,))
