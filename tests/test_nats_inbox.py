"""Unit tests for durable NATS inbox."""
import unittest
from qa_lab.nats_inbox import accept_event

class FakeDB:
    def __init__(self):
        self.events={}
        self.row=None

    def execute(self,sql,params):
        if sql.lstrip().startswith('INSERT INTO qa_inbox'):
            eid,subject,payload=params
            self.row=(eid,) if eid not in self.events else None
            if self.row is not None:
                self.events[eid]=(subject,__import__('json').loads(payload))
        elif sql.lstrip().startswith('SELECT subject,payload FROM qa_inbox'):
            self.row=self.events.get(params[0])
        else:
            raise AssertionError('unexpected SQL')
        return self

    def fetchone(self):
        return self.row

class InboxTests(unittest.TestCase):
    def test_duplicate_event_has_no_second_effect(self):
        db=FakeDB()
        event={'event_id':'00000000-0000-0000-0000-000000000001','payload':{'run_id':'r'}}
        self.assertTrue(accept_event(db,'qa.jobs.created',event))
        self.assertFalse(accept_event(db,'qa.jobs.created',event))
        self.assertEqual(len(db.events),1)
    def test_reject_unknown_subject(self):
        with self.assertRaisesRegex(ValueError,'subject not allowlisted'):
            accept_event(FakeDB(),'other',{})
    def test_reject_invalid_id(self):
        with self.assertRaisesRegex(ValueError,'invalid event id'):
            accept_event(FakeDB(),'qa.jobs.created',{'event_id':'bad','payload':{}})
