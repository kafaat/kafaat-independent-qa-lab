import tempfile
import unittest
from pathlib import Path
from qa_lab.knowledge import KnowledgeStore
from qa_lab.parity import evaluate,summary
class KnowledgeTests(unittest.TestCase):
    def test_persistent_source_bound_search(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'knowledge.sqlite'
            KnowledgeStore(p).add({'id':'doc','source_sha':'a'*40,'text':'PostgreSQL recovery'})
            result=KnowledgeStore(p).query('recovery')
            self.assertEqual(result[0]['source_sha'],'a'*40)
    def test_invalid_document(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):KnowledgeStore(Path(d)/'db').add({'id':'x'})
class ParityTests(unittest.TestCase):
    def test_missing_receipts_are_not_run(self):
        cases=[{'id':'a','legacy_ref':'old','new_ref':'new'}]
        self.assertEqual(summary(evaluate(cases,{}))['NOT_RUN'],1)
    def test_matching_receipts_verified(self):
        cases=[{'id':'a','legacy_ref':'old','new_ref':'new'}]
        r={'input_digest':'x','output_digest':'y','status':'PASSED'}
        self.assertEqual(evaluate(cases,{('a','legacy'):r,('a','new'):r})[0]['status'],'VERIFIED')
