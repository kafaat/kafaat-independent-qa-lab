"""Receipt output tails are redacted before hashing (qa_lab/redaction.py)."""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from qa_lab import core
from qa_lab.core import Lab
from qa_lab.pg_worker import execute_one
from qa_lab.redaction import MAX_INPUT_CHARS, RULES_VERSION, redact_tail, redact_text

GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
AWS = "AKIA" + "IOSFODNN7EXAMPLE"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA7q\nabc\n-----END RSA PRIVATE KEY-----"


class RedactTextTests(unittest.TestCase):
    def test_known_secret_shapes_are_replaced_without_digests(self):
        cases = {
            "github-token": f"token {GH} used",
            "aws-access-key-id": f"key={AWS}",
            "jwt": f"auth {JWT}",
            "url-credentials": "postgres://qa:s3cr3tP4ss@db:5432/qa",
            "bearer-token": "Authorization: Bearer abcdefghij0123456789xyz",
            "keyword-assignment": 'DB_PASSWORD="correct horse battery"',
            "private-key": f"before\n{PEM}\nafter",
            "anthropic-api-key": "sk-ant-api03-abcdefghijklmnopqrstuvwxyz012345",
        }
        for rule, text in cases.items():
            with self.subTest(rule=rule):
                redacted, counts = redact_text(text)
                self.assertIn(f"[REDACTED:{rule}]", redacted)
                self.assertNotRegex(redacted, r"\[REDACTED:[a-z0-9-]+:[0-9a-f]+\]")
                self.assertGreaterEqual(counts.get(rule, 0), 1)
        self.assertNotIn("s3cr3tP4ss", redact_text(cases["url-credentials"])[0])
        self.assertNotIn("MIIEpAIBAAKCAQEA7q", redact_text(cases["private-key"])[0])

    def test_ordinary_test_output_is_untouched(self):
        text = ("Ran 12 tests in 0.3s\nOK\nqa-smoke-ok\n"
                "sha256 da5efe88edf3630b880504ca8540a4ec1e93ee95914e69c2e688f59aeb1df1df\n"
                "commit 4e311d7a851e5e13768d6561586af79d8b270ea5\n"
                "password=changeme token: null secret=${SECRET} api_key=<redacted>\n"
                "id 41541631626 uuid 123e4567-e89b-12d3-a456-426614174000\n")
        self.assertEqual(redact_text(text), (text, {}))

    def test_existing_placeholders_are_kept(self):
        text = "password=[REDACTED:keyword-assignment]"
        self.assertEqual(redact_text(text), (text, {}))

    def test_input_limit_and_type(self):
        with self.assertRaises(ValueError):
            redact_text("x" * (MAX_INPUT_CHARS + 1))
        with self.assertRaises(TypeError):
            redact_text(b"bytes")

    def test_adversarial_input_is_bounded_in_time(self):
        for text in ("password=" * 7000, "a://" + "b" * 256 + ":" * 30000,
                     "bearer " * 9000, "-----BEGIN PRIVATE KEY-----" * 2400,
                     "eyJ" + "a" * 4000 + ".eyJ" + "b" * 8000 + "." * 30000):
            text = text[:MAX_INPUT_CHARS]
            started = time.monotonic()
            redact_text(text)
            self.assertLess(time.monotonic() - started, 2.0)


class RedactTailTests(unittest.TestCase):
    def test_secret_straddling_the_truncation_boundary_never_leaks_a_fragment(self):
        output = "x" * 100 + " " + GH + "\n" + "y" * 20
        for keep in range(10, 70):
            with self.subTest(keep=keep):
                tail, _ = redact_tail(output, keep)
                self.assertLessEqual(len(tail), keep)
                for fragment in (GH[i:i + 8] for i in range(4, len(GH) - 8)):
                    self.assertNotIn(fragment, tail)

    def test_long_output_keeps_only_the_tail(self):
        tail, counts = redact_tail("z" * 200000 + f" {GH}", 100)
        self.assertTrue(tail.endswith("[REDACTED:github-token]"))
        self.assertEqual(counts, {"github-token": 1})
        self.assertEqual(redact_tail(None, 10), ("", {}))


class ReceiptIntegrationTests(unittest.TestCase):
    def test_sqlite_receipt_is_redacted_before_hashing_and_still_verifies(self):
        recipe = ["python3", "-c",
                  f"import sys; print('leak {GH}'); print('url postgres://u:pw1234@h/db', file=sys.stderr)"]
        with tempfile.TemporaryDirectory() as d, \
                patch.dict(core.RECIPES, {"python-smoke": recipe}), \
                patch.dict(os.environ, {"QA_REQUIRE_GIT_SHA": "0"}):
            lab = Lab(d)
            rid = lab.submit({"idempotency_key": "redact", "project": "demo",
                              "source_sha": "a" * 40, "recipe": "python-smoke"})["run_id"]
            receipt = lab.execute(rid, Path(d))
            self.assertTrue(lab.verify(rid))
            stored = lab.get(rid)["receipt"]
        self.assertEqual(stored, receipt)
        self.assertNotIn(GH, str(stored))
        self.assertNotIn("pw1234", str(stored))
        self.assertIn("[REDACTED:github-token]", stored["stdout_tail"])
        self.assertIn("[REDACTED:url-credentials]", stored["stderr_tail"])
        self.assertEqual(stored["output_redaction"]["findings"],
                         {"github-token": 1, "url-credentials": 1})
        self.assertEqual(stored["output_redaction"]["rules_version"], RULES_VERSION)
        self.assertFalse(stored["output_redaction"]["completeness_claimed"])

    def test_clean_smoke_receipt_reports_zero_findings(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"QA_REQUIRE_GIT_SHA": "0"}):
            lab = Lab(d)
            rid = lab.submit({"idempotency_key": "clean", "project": "demo",
                              "source_sha": "a" * 40, "recipe": "python-smoke"})["run_id"]
            receipt = lab.execute(rid, Path(d))
        self.assertEqual(receipt["stdout_tail"].strip(), "qa-smoke-ok")
        self.assertEqual(receipt["output_redaction"]["findings_count"], 0)

    def test_postgres_worker_receipt_is_redacted(self):
        class FakeQueue:
            def claim(self, worker_id):
                return {"run_id": "r", "request": {"recipe": "python-smoke", "source_sha": "a" * 40}}

            def finish(self, rid, worker_id, status, receipt):
                return {"status": status, "receipt": receipt}

        class Proc:
            returncode = 0
            stdout = f"qa-smoke-ok {AWS}\n"
            stderr = ""

        with patch("qa_lab.pg_worker.subprocess.run", return_value=Proc()):
            result = execute_one(FakeQueue(), "w")
        self.assertNotIn(AWS, result["receipt"]["stdout_tail"])
        self.assertEqual(result["receipt"]["output_redaction"]["findings"], {"aws-access-key-id": 1})


if __name__ == "__main__":
    unittest.main()
