import unittest
from qa_lab.pg_api import validate_submission
from qa_lab.pg_worker import execute_one
from qa_lab.pg_queue import validate

class SecurityContractTests(unittest.TestCase):
    def test_api_refuses_arbitrary_recipe(self):
        with self.assertRaisesRegex(ValueError,'recipe not allowed'):
            validate_submission({'idempotency_key':'x','project':'p','source_sha':'a'*40,'recipe':'shell'})
    def test_api_refuses_extra_execution_fields(self):
        with self.assertRaisesRegex(ValueError,'exact request fields'):
            validate_submission({'idempotency_key':'x','project':'p','source_sha':'a'*40,'recipe':'python-smoke','command':'whoami'})
    def test_queue_rejects_wrong_digest(self):
        with self.assertRaisesRegex(ValueError,'digest mismatch'):
            validate({'idempotency_key':'x'},'a'*64)
    def test_worker_blocks_unapproved_recipe(self):
        class FakeQueue:
            def claim(self,worker):
                return {'run_id':'r','request':{'recipe':'shell'}}
            def finish(self,rid,worker,status,receipt):
                return {'status':status,'receipt':receipt}
        result=execute_one(FakeQueue(),'w')
        self.assertEqual(result['status'],'BLOCKED')
        self.assertEqual(result['receipt']['authority'],'execution-evidence-only')
