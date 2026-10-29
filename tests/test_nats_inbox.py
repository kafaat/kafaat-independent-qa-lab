"""Unit tests for durable NATS inbox."""
import unittest
from qa_lab.nats_inbox import accept_event

class FakeDB:
    def __init__(self):self.ids=set()
    def execute(self,sql,params):
        self.last=params
        return self
    def fetchone(self):
        eid=self.last[0]
        if eid in self.ids:return None
        self.ids.add(eid)
        return (eid,)

class InboxTests(unittest.TestCase):
    def test_duplicate_event_has_no_second_effect(self):
        db=FakeDB()
        event={'event_id':'00000000-0000-0000-0000-000000000001','payload':{'run_id':'r'}}
        self.assertTrue(accept_event(db,'qa.jobs.created',event))
        self.assertFalse(accept_event(db,'qa.jobs.created',event))
    def test_reject_unknown_subject(self):
        with self.assertRaisesRegex(ValueError,'subject not allowlisted'):
            accept_event(FakeDB(),'other',{})
    def test_reject_invalid_id(self):
        with self.assertRaisesRegex(ValueError,'invalid event id'):
            accept_event(FakeDB(),'qa.jobs.created',{'event_id':'bad','payload':{}})
