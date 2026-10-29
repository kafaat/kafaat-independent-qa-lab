import unittest
from qa_lab.rag import search
class RetrievalTests(unittest.TestCase):
    def test_source_bound_result(self):
        result=search([{'id':'a','text':'PostgreSQL worker failed','source_sha':'a'*40}], 'worker')
        self.assertEqual(result[0]['source_sha'],'a'*40)
        self.assertEqual(result[0]['status'],'REFERENCE_ONLY')
    def test_missing_query_rejected(self):
        with self.assertRaises(ValueError):search([], ' ')
