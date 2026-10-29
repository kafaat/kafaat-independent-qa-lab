import unittest
from pathlib import Path

class E2EInboxQueryTests(unittest.TestCase):
    def test_query_matches_inbox_schema(self):
        source=(Path(__file__).resolve().parents[1]/'qa_lab'/'railway_probe.py').read_text()
        self.assertIn("payload->>'run_id'=%s",source)
        self.assertNotIn("payload->'payload'->>'run_id'",source)
        self.assertIn("'inbox_count':inbox",source)
