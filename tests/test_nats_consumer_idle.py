"""Idle JetStream pull must not take the consumer process down."""
import asyncio
import unittest
import nats
from qa_lab.nats_consumer import fetch_idle_safe

class FakeSub:
    def __init__(self, result=None, error=None):
        self.result=result
        self.error=error
        self.kwargs=None
    async def fetch(self, **kwargs):
        self.kwargs=kwargs
        if self.error is not None:
            raise self.error
        return self.result

class IdlePullTests(unittest.IsolatedAsyncioTestCase):
    async def test_asyncio_timeout_returns_empty_list(self):
        sub=FakeSub(error=asyncio.TimeoutError())
        self.assertEqual(await fetch_idle_safe(sub, timeout=0.4), [])
        self.assertEqual(sub.kwargs, {'batch':10,'timeout':0.4})
    async def test_nats_timeout_returns_empty_list(self):
        self.assertEqual(await fetch_idle_safe(FakeSub(error=nats.errors.TimeoutError())), [])
    async def test_non_timeout_remains_error(self):
        with self.assertRaises(RuntimeError):
            await fetch_idle_safe(FakeSub(error=RuntimeError('broken')))
    async def test_fetched_messages_preserved(self):
        item=object()
        self.assertEqual(await fetch_idle_safe(FakeSub(result=[item])), [item])
