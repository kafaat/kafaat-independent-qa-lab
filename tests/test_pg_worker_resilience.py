"""The PostgreSQL worker waits out transient database errors instead of exiting."""
import contextlib
import io
import unittest

from qa_lab import pg_worker


class FakeOperationalError(Exception):
    pass


class FlakyQueue:
    def __init__(self, init_failures=0, claim_failures=0, jobs=()):
        self.init_failures, self.claim_failures = init_failures, claim_failures
        self.jobs, self.finished, self.init_calls = list(jobs), [], 0

    def init(self):
        self.init_calls += 1
        if self.init_failures:
            self.init_failures -= 1
            raise FakeOperationalError("failed to resolve host 'postgres.railway.internal'")

    def claim(self, worker_id):
        if self.claim_failures:
            self.claim_failures -= 1
            raise FakeOperationalError("server closed the connection unexpectedly")
        return self.jobs.pop(0) if self.jobs else None

    def finish(self, rid, worker_id, status, receipt):
        self.finished.append((rid, status))
        return {"status": status}


def run(queue, iterations):
    sleeps, stderr = [], io.StringIO()
    with contextlib.redirect_stderr(stderr):
        pg_worker.run(queue, "w", transient=(FakeOperationalError,), sleep=sleeps.append,
                      iterations=iterations)
    return sleeps, stderr.getvalue()


class ResilienceTests(unittest.TestCase):
    def test_dns_failure_at_startup_is_retried_with_backoff(self):
        queue = FlakyQueue(init_failures=3, jobs=[{"run_id": "r1", "request": {"recipe": "shell"}}])
        sleeps, log = run(queue, iterations=5)
        self.assertEqual(sleeps[:3], [2, 4, 8])
        self.assertEqual(queue.init_calls, 4)
        self.assertEqual(queue.finished, [("r1", "BLOCKED")])
        self.assertIn("FakeOperationalError", log)
        self.assertNotIn("postgres.railway.internal", log)

    def test_backoff_is_capped_and_resets_after_success(self):
        queue = FlakyQueue(claim_failures=8)
        sleeps, _ = run(queue, iterations=10)
        self.assertEqual(sleeps[:8], [2, 4, 8, 16, 32, 60, 60, 60])
        self.assertEqual(sleeps[8:], [2, 2])

    def test_other_errors_still_propagate(self):
        class Broken(FlakyQueue):
            def claim(self, worker_id):
                raise ValueError("programming error")
        with self.assertRaises(ValueError):
            run(Broken(), iterations=1)

    def test_real_transient_class_is_psycopg_operational_error_when_installed(self):
        errors = pg_worker.transient_db_errors()
        try:
            import psycopg
        except ImportError:
            self.assertEqual(errors, ())
        else:
            self.assertEqual(errors, (psycopg.OperationalError,))


if __name__ == "__main__":
    unittest.main()
