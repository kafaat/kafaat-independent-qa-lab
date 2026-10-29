import hashlib
import json
import unittest
from qa_lab.pg_queue import validate

class PostgreSQLInputTests(unittest.TestCase):
    def request(self):
        return {'idempotency_key':'unique','project':'demo'}
    def digest(self,request):
        return hashlib.sha256(json.dumps(request,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    def test_valid_request(self):
        request=self.request()
        self.assertIn('unique',validate(request,self.digest(request)))
    def test_mismatched_digest(self):
        with self.assertRaisesRegex(ValueError,'digest mismatch'):
            validate(self.request(),'a'*64)
    def test_nonfinite_json(self):
        request={**self.request(),'value':float('nan')}
        with self.assertRaisesRegex(ValueError,'invalid request JSON'):
            validate(request,'a'*64)
    def test_invalid_key(self):
        request={**self.request(),'idempotency_key':''}
        with self.assertRaisesRegex(ValueError,'invalid idempotency key'):
            validate(request,self.digest(request))
    def test_oversize(self):
        request={**self.request(),'value':'x'*(1024*1024)}
        with self.assertRaisesRegex(ValueError,'request too large'):
            validate(request,self.digest(request))
