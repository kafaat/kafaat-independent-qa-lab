"""Regression: PostgreSQL GUC options must be separately flagged."""
import ast
import pathlib
import unittest

FILE = pathlib.Path(__file__).resolve().parents[1] / "qa_lab" / "acceptance_receipt_audit.py"

class PostgresOptionsTests(unittest.TestCase):
    def test_read_latest_uses_separate_c_flags(self):
        tree = ast.parse(FILE.read_text(encoding="utf-8"))
        matches = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "connect":
                matches.extend(k.value.value for k in node.keywords if k.arg == "options" and isinstance(k.value, ast.Constant))
        self.assertEqual(matches, ["-c statement_timeout=5000 -c default_transaction_read_only=on"])

if __name__ == "__main__":
    unittest.main()
