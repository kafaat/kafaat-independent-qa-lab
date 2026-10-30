import hashlib
import unittest
from qa_lab.http_acceptance import canonical
from qa_lab.acceptance_receipt_audit import audit_record

SHA = 'a' * 40
DEP = 'deploy-example'

def receipt():
    doc = {'status': 'PASSED', 'requested_source_sha': SHA,
           'deployment_identity': {'RAILWAY_DEPLOYMENT_ID': DEP},
           'checks': [{'name': 'worker_receipt', 'passed': True}]}
    doc['receipt_sha256'] = hashlib.sha256(canonical(doc).encode()).hexdigest()
    return doc

class AcceptanceAuditTests(unittest.TestCase):
    def test_consistent_receipt(self):
        self.assertEqual(audit_record(receipt(), SHA, DEP)['status'], 'CONSISTENT')

    def test_tamper_rejected(self):
        doc = receipt()
        doc['checks'][0]['passed'] = False
        self.assertIn('RECEIPT_DIGEST_MISMATCH', audit_record(doc, SHA, DEP)['reasons'])

    def test_wrong_deployment_rejected(self):
        self.assertIn('DEPLOYMENT_MISMATCH', audit_record(receipt(), SHA, 'other')['reasons'])

    def test_failed_result_rejected(self):
        doc = receipt()
        doc['status'] = 'FAILED'
        doc['receipt_sha256'] = hashlib.sha256(canonical({k:v for k,v in doc.items() if k!='receipt_sha256'}).encode()).hexdigest()
        self.assertIn('ACCEPTANCE_NOT_PASSED', audit_record(doc, SHA, DEP)['reasons'])

if __name__ == '__main__':
    unittest.main()
