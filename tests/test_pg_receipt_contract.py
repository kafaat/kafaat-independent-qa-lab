import unittest
from qa_lab.pg_worker import execute_one

class ReceiptContractTests(unittest.TestCase):
    def test_smoke_receipt_is_advisory_evidence(self):
        class FakeQueue:
            def claim(self,worker):
                return {'run_id':'r','request':{'recipe':'python-smoke','source_sha':'a'*40}}
            def finish(self,rid,worker,status,receipt):
                return {'run_id':rid,'status':status,'receipt':receipt}
        result=execute_one(FakeQueue(),'w')
        self.assertEqual(result['status'],'PASSED')
        self.assertEqual(result['receipt']['authority'],'execution-evidence-only')
        self.assertEqual(result['receipt']['environment_claim'],'unattested-worker')
        for forbidden in ('merge_approved','case_closed','release_approved'):
            self.assertNotIn(forbidden,result['receipt'])
