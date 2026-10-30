import tempfile
import unittest
from qa_lab.core import Lab
from qa_lab.receipt_cost import estimate


class ReceiptCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = Lab(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def submit(self):
        run = self.lab.submit({'idempotency_key': 'unique-key', 'project': 'sample',
                               'source_sha': 'a' * 40, 'recipe': 'python-smoke'})
        return run['run_id']

    def test_pending_is_not_billable(self):
        rid = self.submit()
        with self.assertRaisesRegex(ValueError, 'terminal'):
            estimate(self.lab, [rid], usd_per_hour='1')

    def test_executed_receipt_is_estimated(self):
        rid = self.submit()
        self.lab.execute(rid, self.tmp.name)
        result = estimate(self.lab, [rid], usd_per_hour='1.5')
        self.assertEqual(result['status'], 'ESTIMATE_ONLY')
        self.assertFalse(result['invoice_verified'])
        self.assertEqual(result['cost']['event_count'], 1)
        self.assertEqual(result['receipts'][0]['receipt_sha256'], self.lab.get(rid)['receipt']['sha256'])

    def test_duplicate_and_bad_rate(self):
        rid = self.submit()
        for ids, rate in (([rid, rid], '1'), ([rid], '-1'), ([rid], 'NaN')):
            with self.assertRaises(ValueError):
                estimate(self.lab, ids, usd_per_hour=rate)

    def test_missing_run_is_not_silently_zero(self):
        with self.assertRaises(KeyError):
            estimate(self.lab, ['missing'], usd_per_hour='1')


if __name__ == '__main__':
    unittest.main()
