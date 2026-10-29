"""Safety regressions for public bootstrap and the execution kernel."""
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from qa_lab.core import Lab

class BoundaryTests(unittest.TestCase):
    def test_idempotency_conflict_does_not_create_extra_run(self):
        with tempfile.TemporaryDirectory() as d:
            lab=Lab(d)
            req={'idempotency_key':'a','project':'demo','source_sha':'a'*40,'recipe':'python-smoke'}
            first=lab.submit(req)
            self.assertTrue(lab.submit(req)['reused'])
            with self.assertRaises(ValueError):lab.submit({**req,'recipe':'python-compile'})
            self.assertEqual(lab.get(first['run_id'])['status'],'PENDING')
    def test_reconcile_running_never_reexecutes(self):
        with tempfile.TemporaryDirectory() as d:
            lab=Lab(d)
            rid=lab.submit({'idempotency_key':'b','project':'demo','source_sha':'b'*40,'recipe':'python-smoke'})['run_id']
            with closing(lab.connect()) as db, db:
                db.execute("UPDATE runs SET status='RUNNING' WHERE id=?",(rid,))
            self.assertEqual(lab.reconcile(rid)['action'],'manual-review')
            self.assertEqual(lab.get(rid)['status'],'RUNNING')
    def test_compose_does_not_publish_internal_services(self):
        text=(Path(__file__).resolve().parents[1]/'compose.yaml').read_text()
        for service in ('evidence:', 'triage:'):
            section=text.split('\n  '+service,1)[1].split('\n  ',1)[0]
            self.assertNotIn('ports:',section)
