import unittest
from qa_lab.rag_eval import evaluate
class RetrievalEvaluationTests(unittest.TestCase):
    def test_scores_expected_documents(self):
        cases=[{'id':'c','query':'water','expected_ids':['a','b']}]
        r=evaluate(cases,lambda q:[{'id':'a'}])
        self.assertEqual(r['mean_recall_at_k'],0.5)
        self.assertEqual(r['hit_rate'],1)
    def test_empty_eval_rejected(self):
        with self.assertRaises(ValueError):evaluate([],lambda q:[])
