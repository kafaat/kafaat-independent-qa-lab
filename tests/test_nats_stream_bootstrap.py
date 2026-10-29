"""Regression for the missing QA_JOBS stream startup path."""
import ast
from pathlib import Path
import unittest

class StreamBootstrapTests(unittest.TestCase):
    def test_consumer_checks_stream_before_subscribe(self):
        path=Path(__file__).resolve().parents[1]/'qa_lab'/'nats_consumer.py'
        tree=ast.parse(path.read_text())
        source=path.read_text()
        self.assertIn("await js.stream_info('QA_JOBS')",source)
        self.assertIn("await js.add_stream(",source)
        self.assertLess(source.index("await js.stream_info('QA_JOBS')"),source.index("await js.pull_subscribe("))
