import unittest
from pathlib import Path
class DashboardContractTests(unittest.TestCase):
    def test_read_only_sql(self):
        source=(Path(__file__).resolve().parents[1]/'qa_lab'/'pg_dashboard.py').read_text()
        self.assertIn('SELECT id,status',source)
        self.assertNotIn('DELETE FROM qa_jobs',source)
        self.assertNotIn('UPDATE qa_jobs',source)
    def test_authentication_required(self):
        source=(Path(__file__).resolve().parents[1]/'qa_lab'/'pg_dashboard.py').read_text()
        self.assertIn('QA_DASHBOARD_TOKEN',source)
