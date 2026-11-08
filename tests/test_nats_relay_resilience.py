"""The outbox relay waits out transient database errors instead of exiting."""
import asyncio
import contextlib
import io
import unittest

from qa_lab import nats_relay


class FakeOperationalError(Exception):
    pass


class FakeDB:
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @contextlib.contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=None):
        if sql.lstrip().startswith("SELECT"):
            rows = [(eid, "qa.jobs.created", {"n": eid}) for eid in self.store["pending"]]
            return type("Result", (), {"fetchall": lambda _self: rows})()
        if sql.lstrip().startswith("UPDATE"):
            self.store["pending"].remove(params[0])
        return None


class FakeJetStream:
    def __init__(self):
        self.published = []

    async def publish(self, subject, body, headers, timeout):
        self.published.append((subject, headers["Nats-Msg-Id"]))


def connector(store, failures):
    def connect():
        if failures:
            failures.pop()
            raise FakeOperationalError("failed to resolve host 'postgres.railway.internal'")
        return FakeDB(store)
    return connect


def run(connect, js, iterations):
    sleeps, stderr = [], io.StringIO()

    async def sleep(seconds):
        sleeps.append(seconds)

    with contextlib.redirect_stderr(stderr):
        asyncio.run(nats_relay.relay_loop(connect, js, transient=(FakeOperationalError,),
                                          sleep=sleep, iterations=iterations))
    return sleeps, stderr.getvalue()


class RelayResilienceTests(unittest.TestCase):
    def test_dns_failures_are_retried_then_rows_are_published(self):
        store, js = {"pending": [1, 2]}, FakeJetStream()
        sleeps, log = run(connector(store, [1, 1, 1]), js, iterations=4)
        self.assertEqual(sleeps, [2, 4, 8, 1])
        self.assertEqual(js.published, [("qa.jobs.created", "1"), ("qa.jobs.created", "2")])
        self.assertEqual(store["pending"], [])
        self.assertIn("FakeOperationalError", log)
        self.assertNotIn("postgres.railway.internal", log)

    def test_backoff_is_capped_and_resets(self):
        store = {"pending": []}
        sleeps, _ = run(connector(store, [1] * 8), FakeJetStream(), iterations=10)
        self.assertEqual(sleeps, [2, 4, 8, 16, 32, 60, 60, 60, 1, 1])

    def test_other_errors_still_propagate(self):
        def broken():
            raise ValueError("programming error")
        with self.assertRaises(ValueError):
            run(broken, FakeJetStream(), iterations=1)


if __name__ == "__main__":
    unittest.main()
