"""Real PostgreSQL API/worker contract test, on dedicated CI database."""
import hashlib
import json
import os
import unittest
import uuid
from qa_lab.pg_api import validate_submission
from qa_lab.pg_queue import PostgresQueue, canonical
from qa_lab.pg_worker import execute_one

DSN=os.environ.get('QA_TEST_POSTGRES_DSN')

@unittest.skipUnless(DSN,'requires real PostgreSQL')
class PostgreSQLServiceFlowTests(unittest.TestCase):
    def test_api_contract_to_worker_receipt(self):
        q=PostgresQueue(DSN);q.init()
        key='qa-service-flow-'+uuid.uuid4().hex
        request={'idempotency_key':key,'project':'integration','source_sha':'a'*40,'recipe':'python-smoke'}
        try:
            validated=validate_submission(request)
            digest=hashlib.sha256(canonical(validated).encode()).hexdigest()
            run_id=q.submit(validated,digest)['run_id']
            result=execute_one(q,'service-flow-worker')
            self.assertEqual(result['run_id'],run_id)
            state=q.get(run_id)
            self.assertEqual(state['status'],'PASSED')
            self.assertEqual(state['receipt']['authority'],'execution-evidence-only')
            self.assertEqual(state['receipt']['stdout_tail'].strip(),'qa-smoke-ok')
            self.assertIsNone(execute_one(q,'service-flow-worker'))
        finally:
            with q._connect() as db:
                db.execute('DELETE FROM qa_jobs WHERE idempotency_key=%s',(key,))

class PostgreSQLSubmissionValidationTests(unittest.TestCase):
    def test_reject_arbitrary_command(self):
        with self.assertRaisesRegex(ValueError,'exact request fields'):
            validate_submission({'idempotency_key':'a','project':'p','source_sha':'a'*40,'recipe':'python-smoke','command':'rm -rf /'})
    def test_reject_unapproved_recipe(self):
        with self.assertRaisesRegex(ValueError,'recipe not allowed'):
            validate_submission({'idempotency_key':'a','project':'p','source_sha':'a'*40,'recipe':'shell'})
