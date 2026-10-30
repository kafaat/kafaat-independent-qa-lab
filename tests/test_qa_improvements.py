import unittest
from qa_lab.reliability_policy import Circuit, decide, retry_read_only
from qa_lab.cost_accounting import summarize
from qa_lab.parity_inventory import audit

class ImprovementTests(unittest.TestCase):
    def test_circuit_opens_and_cools_down(self):
        state = Circuit()
        state, ok = decide(state, now=1, outcome='failure', threshold=2)
        self.assertTrue(ok)
        state, ok = decide(state, now=2, outcome='failure', threshold=2)
        self.assertFalse(ok)
        self.assertFalse(decide(state, now=5)[1])
        self.assertTrue(decide(state, now=33)[1])
        self.assertEqual(decide(state, now=33, outcome='success')[0], Circuit())

    def test_retry_no_mutation(self):
        self.assertFalse(retry_read_only(method='POST', attempt=1, idempotency_proven=True)['retry'])
        self.assertTrue(retry_read_only(method='GET', attempt=1)['retry'])
        self.assertFalse(retry_read_only(method='GET', attempt=3)['retry'])

    def test_invalid_circuit_values(self):
        for value in (float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):
                decide(Circuit(), now=value)

    def test_costs_precise_and_no_double_count(self):
        events = [{'event_id': 'a', 'category': 'compute', 'amount': '0.10', 'currency': 'USD'},
                  {'event_id': 'b', 'category': 'compute', 'amount': '0.20', 'currency': 'USD'}]
        self.assertEqual(summarize(events)['total'], '0.30')
        with self.assertRaises(ValueError):
            summarize(events + [events[0]])

    def test_bad_costs_fail_closed(self):
        for amount in ('NaN', '-1', 'Infinity', 0.123456789, True):
            with self.assertRaises(ValueError):
                summarize([{'event_id': 'x', 'category': 'model', 'amount': amount, 'currency': 'USD'}])

    def test_parity_does_not_infer_approval(self):
        result = audit({'profiles': [{'id': 'p', 'local_coverage': 'PARTIAL', 'deployed_parity': 'NOT_RUN'}]})
        self.assertEqual(result['status'], 'INVENTORY_ONLY')
        self.assertFalse(result['full_capability_parity'])

    def test_duplicate_parity_entries(self):
        p = {'id': 'p', 'local_coverage': 'NOT_RUN', 'deployed_parity': 'NOT_RUN'}
        self.assertEqual(audit({'profiles': [p, p]})['status'], 'INVALID')

if __name__ == '__main__':
    unittest.main()
