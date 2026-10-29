"""Probe contract tests: static checks do not replace live Railway execution."""
import unittest
from pathlib import Path
class ProbeContracts(unittest.TestCase):
    def test_probe_requires_source_sha(self):
        source=(Path(__file__).resolve().parents[1]/'qa_lab'/'railway_probe.py').read_text()
        self.assertIn("os.environ['QA_SOURCE_SHA']",source)
        self.assertIn("run['receipt'].get('source_sha')",source)
    def test_probe_checks_both_events(self):
        source=(Path(__file__).resolve().parents[1]/'qa_lab'/'railway_probe.py').read_text()
        self.assertIn("'qa.jobs.created','qa.jobs.finished'",source)
        self.assertIn("await js.stream_info('QA_JOBS')",source)
