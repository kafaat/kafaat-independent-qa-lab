"""Regression for a message-ID collision that must not be ACKed as a replay."""
import json
import unittest
import uuid
from qa_lab.nats_inbox import accept_event

class InboxDB:
    def __init__(self):
        self.events = {}
        self.result = None
    def execute(self, sql, params):
        if sql.lstrip().startswith('INSERT INTO qa_inbox'):
            eid,subject,payload=params
            self.result=(eid,) if eid not in self.events else None
            if self.result is not None:
                self.events[eid]=(subject,json.loads(payload))
        elif sql.lstrip().startswith('SELECT subject,payload FROM qa_inbox'):
            (eid,)=params
            self.result=self.events.get(eid)
        else:
            raise AssertionError('unexpected SQL: '+sql)
        return self
    def fetchone(self): return self.result

class CollisionTests(unittest.TestCase):
    def setUp(self):
        self.db=InboxDB();self.id=str(uuid.uuid4())
        self.payload={'run_id':'run', 'status':'PASSED'}
    def item(self,payload=None,id=None):
        return {'event_id':id or self.id,'payload':self.payload if payload is None else payload}
    def test_normal_replay_returns_false_without_change(self):
        self.assertTrue(accept_event(self.db,'qa.jobs.finished',self.item()))
        before=dict(self.db.events)
        self.assertFalse(accept_event(self.db,'qa.jobs.finished',self.item()))
        self.assertEqual(before,self.db.events)
    def test_different_payload_cannot_reuse_event_id(self):
        self.assertTrue(accept_event(self.db,'qa.jobs.finished',self.item()))
        with self.assertRaisesRegex(ValueError,'event id collision'):
            accept_event(self.db,'qa.jobs.finished',self.item({'run_id':'other','status':'PASSED'}))
    def test_different_subject_cannot_reuse_event_id(self):
        self.assertTrue(accept_event(self.db,'qa.jobs.finished',self.item()))
        with self.assertRaisesRegex(ValueError,'event id collision'):
            accept_event(self.db,'qa.jobs.created',self.item())
    def test_json_object_key_order_does_not_false_positive(self):
        self.assertTrue(accept_event(self.db,'qa.jobs.finished',self.item()))
        payload={'status':'PASSED','run_id':'run'}
        self.assertFalse(accept_event(self.db,'qa.jobs.finished',self.item(payload)))
    def test_different_event_ids_are_independent(self):
        self.assertTrue(accept_event(self.db,'qa.jobs.finished',self.item()))
        self.assertTrue(accept_event(self.db,'qa.jobs.finished',self.item(id=str(uuid.uuid4()))))
        self.assertEqual(len(self.db.events),2)
    def test_collision_cannot_change_original_entry(self):
        self.assertTrue(accept_event(self.db,'qa.jobs.finished',self.item()))
        before=dict(self.db.events)
        for changed_subject,changed_data in [('qa.jobs.finished',{'run_id':'forged'}),('qa.jobs.created',self.payload)]:
            with self.assertRaisesRegex(ValueError,'event id collision'):
                accept_event(self.db,changed_subject,self.item(changed_data))
        self.assertEqual(before,self.db.events)
    def test_duplicate_without_visible_record_fails_closed(self):
        class LostRecord(InboxDB):
            def execute(self,sql,params):
                if sql.lstrip().startswith('INSERT INTO qa_inbox'):
                    self.result=None
                    return self
                return super().execute(sql,params)
        with self.assertRaisesRegex(ValueError,'unverifiable duplicate'):
            accept_event(LostRecord(),'qa.jobs.finished',self.item())
    def test_nonfinite_payload_fails_before_insert(self):
        with self.assertRaises(ValueError):
            accept_event(self.db,'qa.jobs.finished',self.item({'run_id':float('nan')}))
        self.assertFalse(self.db.events)

if __name__=='__main__':unittest.main()
