"""Real PostgreSQL transactional outbox tests."""
import hashlib
import json
import os
import unittest
import uuid
from qa_lab.pg_queue import PostgresQueue

DSN=os.environ.get('QA_TEST_POSTGRES_DSN')

@unittest.skipUnless(DSN,'requires real PostgreSQL')
class TransactionalOutboxTests(unittest.TestCase):
    def test_create_and_finish_events_are_transactional(self):
        q=PostgresQueue(DSN);q.init()
        key='qa-outbox-'+uuid.uuid4().hex
        request={'idempotency_key':key,'project':'outbox'}
        digest=hashlib.sha256(json.dumps(request,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        event_ids=[]
        try:
            run_id=q.submit(request,digest)['run_id']
            with q._connect() as db:
                created=db.execute("SELECT id FROM qa_outbox WHERE subject='qa.jobs.created' AND payload->>'run_id'=%s",(run_id,)).fetchall()
            self.assertEqual(len(created),1)
            event_ids.extend(row[0] for row in created)
            claimed=q.claim('outbox-worker')
            self.assertEqual(claimed['run_id'],run_id)
            q.finish(run_id,'outbox-worker','PASSED',{'status':'PASSED'})
            with q._connect() as db:
                finished=db.execute("SELECT id FROM qa_outbox WHERE subject='qa.jobs.finished' AND payload->>'run_id'=%s",(run_id,)).fetchall()
            self.assertEqual(len(finished),1)
            event_ids.extend(row[0] for row in finished)
            self.assertEqual(q.get(run_id)['status'],'PASSED')
        finally:
            with q._connect() as db:
                db.execute("DELETE FROM qa_outbox WHERE id=ANY(%s)",(event_ids,))
                db.execute("DELETE FROM qa_jobs WHERE idempotency_key=%s",(key,))
