import unittest
from qa_lab.legacy_guard import LegacyProbeGuard


class LegacyProbeGuardTests(unittest.TestCase):
    def test_success_and_failure_do_not_retry(self):
        now = [1.]
        guard = LegacyProbeGuard(threshold=2, cooldown=10, clock=lambda: now[0])
        calls = [0]
        def failed():
            calls[0] += 1
            raise OSError('unavailable')
        for _ in range(2):
            with self.assertRaises(OSError):
                guard.call(failed)
        with self.assertRaisesRegex(RuntimeError, 'legacy_circuit_open'):
            guard.call(failed)
        self.assertEqual(calls[0], 2)
        now[0] = 12.
        self.assertEqual(guard.call(lambda: {'status': 'ok'}), {'status': 'ok'})
        with self.assertRaises(OSError):
            guard.call(failed)
        self.assertEqual(calls[0], 3)

    def test_bad_legacy_response_is_not_retried(self):
        guard = LegacyProbeGuard(threshold=1, cooldown=4, clock=lambda: 0.)
        with self.assertRaises(ValueError):
            guard.call(lambda: (_ for _ in ()).throw(ValueError('invalid')))
        with self.assertRaises(RuntimeError):
            guard.call(lambda: None)


if __name__ == '__main__':
    unittest.main()
