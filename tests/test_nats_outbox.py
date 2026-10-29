import unittest
from qa_lab.nats_outbox import enqueue,publish_batch

class FakeConnection:
    def __init__(self,rows=()):
        self.rows=list(rows);self.executed=[]
    def execute(self,sql,params=None):
        self.executed.append((sql,params))
        return self
    def fetchall(self):
        return self.rows

class OutboxTests(unittest.TestCase):
    def test_reject_subject(self):
        with self.assertRaisesRegex(ValueError,'subject not allowlisted'):
            enqueue(FakeConnection(),'other.subject',{})
    def test_reject_oversize(self):
        with self.assertRaisesRegex(ValueError,'event too large'):
            enqueue(FakeConnection(),'qa.jobs.created',{'text':'x'*65537})
    def test_publisher_marks_after_publish(self):
        c=FakeConnection([('00000000-0000-0000-0000-000000000001','qa.jobs.created',{'run_id':'r'})])
        delivered=[]
        count=publish_batch(c,lambda subject,payload:delivered.append((subject,payload)))
        self.assertEqual(count,1)
        self.assertEqual(delivered[0][1]['event_id'],'00000000-0000-0000-0000-000000000001')
        self.assertIn('UPDATE qa_outbox',c.executed[-1][0])
    def test_failed_publish_does_not_mark(self):
        c=FakeConnection([('00000000-0000-0000-0000-000000000001','qa.jobs.created',{})])
        def fail(*args):raise RuntimeError('broker down')
        with self.assertRaises(RuntimeError):publish_batch(c,fail)
        self.assertFalse(any('UPDATE qa_outbox' in sql for sql,_ in c.executed))
