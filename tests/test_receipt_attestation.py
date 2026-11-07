"""QA receipts as in-toto Statements in DSSE envelopes (qa_lab/attestation.py)."""
import base64
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qa_lab import attestation as att
from qa_lab.core import Lab

OPENSSL = shutil.which("openssl")


def make_receipt(directory):
    with patch.dict(os.environ, {"QA_REQUIRE_GIT_SHA": "0"}):
        lab = Lab(directory)
        rid = lab.submit({"idempotency_key": "att", "project": "demo", "source_sha": "a" * 40,
                          "recipe": "python-smoke"})["run_id"]
        lab.execute(rid, Path(directory))
    return lab, rid, lab.get(rid)["receipt"]


def make_key(directory, name):
    key = Path(directory, f"{name}.pem")
    subprocess.run([OPENSSL, "genpkey", "-algorithm", "ed25519", "-out", str(key)], check=True,
                   capture_output=True)
    der = subprocess.run([OPENSSL, "pkey", "-in", str(key), "-pubout", "-outform", "DER"],
                         check=True, capture_output=True).stdout
    return key, f"{name} ed25519 {base64.b64encode(der).decode()}\n"


class StatementTests(unittest.TestCase):
    def test_pae_matches_the_dsse_specification_example(self):
        self.assertEqual(att.pae("http://example.com/HelloWorld", b"hello world"),
                         b"DSSEv1 29 http://example.com/HelloWorld 11 hello world")

    def test_statement_binds_the_receipt_digest_and_never_claims_authority(self):
        with tempfile.TemporaryDirectory() as d:
            _, rid, receipt = make_receipt(d)
        statement = att.receipt_statement(receipt)
        self.assertEqual(statement["_type"], "https://in-toto.io/Statement/v1")
        self.assertEqual(statement["predicateType"], "https://in-toto.io/attestation/test-result/v0.1")
        self.assertEqual(statement["subject"], [{"name": f"qa-lab/receipt/{rid}",
                                                 "digest": {"sha256": receipt["sha256"]}}])
        self.assertEqual(statement["predicate"]["result"], "PASSED")
        notes = statement["predicate"]["configuration"][0]["annotations"]
        self.assertIs(notes["authentication_claimed"], False)
        self.assertIs(notes["production_accepted"], False)
        self.assertIs(notes["source_sha_verified"], False)

    def test_tampered_receipt_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            _, _, receipt = make_receipt(d)
        with self.assertRaises(att.AttestationError):
            att.receipt_statement({**receipt, "status": "FAILED"})
        with self.assertRaises(att.AttestationError):
            att.receipt_statement({k: v for k, v in receipt.items() if k != "sha256"})


class UnsignedEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        _, _, self.receipt = make_receipt(self.tmp.name)
        self.env = att.envelope(att.receipt_statement(self.receipt))

    def test_unsigned_envelope_is_consistent_but_not_authenticated(self):
        self.assertEqual(set(self.env), {"payloadType", "payload", "signatures", "authentication_claimed"})
        self.assertIs(self.env["authentication_claimed"], False)
        result = att.verify_envelope(self.env, self.receipt)
        self.assertEqual(result["status"], "UNSIGNED")
        self.assertIs(result["authentication_claimed"], False)

    def test_structural_and_binding_failures(self):
        other = dict(self.receipt, run_id="other")
        other["sha256"] = att.digest({k: v for k, v in other.items() if k != "sha256"})
        cases = {
            "INVALID": [{**self.env, "payloadType": "text/plain"},
                        {**self.env, "payload": "not base64!"},
                        {**self.env, "authentication_claimed": True},
                        {**self.env, "extra": 1},
                        {**self.env, "signatures": [{"keyid": "x"}]}],
        }
        for status, documents in cases.items():
            for document in documents:
                with self.subTest(status=status, keys=sorted(document)):
                    self.assertEqual(att.verify_envelope(document, self.receipt)["status"], status)
        self.assertEqual(att.verify_envelope(self.env, other)["status"], "MISMATCH")


@unittest.skipUnless(OPENSSL, "openssl not installed")
class SignedEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.tmp.name
        self.lab, self.rid, self.receipt = make_receipt(d)
        self.key, self.line = make_key(d, "qa-lab")
        self.other_key, self.other_line = make_key(d, "stranger")
        self.trust_path = Path(d, "trust")
        self.trust_path.write_text("# trusted signers\n" + self.line, encoding="utf-8")
        self.trust = att.load_trust_file(self.trust_path)
        self.env = att.envelope(att.receipt_statement(self.receipt), att.Ed25519Signer(self.key))

    def test_listed_key_is_trusted(self):
        result = att.verify_envelope(self.env, self.receipt, self.trust)
        self.assertEqual(result["status"], "TRUSTED")
        self.assertEqual(result["principal"], "qa-lab")
        self.assertIs(result["authentication_claimed"], True)

    def test_unlisted_key_or_no_trust_file_is_untrusted(self):
        stranger = att.envelope(att.receipt_statement(self.receipt), att.Ed25519Signer(self.other_key))
        self.assertEqual(att.verify_envelope(stranger, self.receipt, self.trust)["status"], "UNTRUSTED")
        self.assertEqual(att.verify_envelope(self.env, self.receipt)["status"], "UNTRUSTED")

    def test_altered_payload_or_signature_is_invalid(self):
        statement = att.receipt_statement(self.receipt)
        statement["predicate"]["result"] = "FAILED"
        forged = dict(self.env, payload=base64.b64encode(att._canonical(statement)).decode())
        self.assertEqual(att.verify_envelope(forged, self.receipt, self.trust)["status"], "INVALID")
        sig = bytearray(base64.b64decode(self.env["signatures"][0]["sig"]))
        sig[0] ^= 1
        bad = dict(self.env, signatures=[{"keyid": self.env["signatures"][0]["keyid"],
                                          "sig": base64.b64encode(bytes(sig)).decode()}])
        self.assertEqual(att.verify_envelope(bad, self.receipt, self.trust)["status"], "INVALID")

    def test_missing_openssl_is_blocked(self):
        with patch.object(att, "_tool", return_value=None):
            self.assertEqual(att.verify_envelope(self.env, self.receipt, self.trust)["status"], "BLOCKED")

    def test_trust_file_refuses_malformed_or_duplicate_lines(self):
        for text in ("qa-lab ssh-ed25519 AAAA\n", "qa-lab ed25519 notbase64!\n", self.line + self.line,
                     "bad principal ed25519 AAAA\n", "# only comments\n"):
            self.trust_path.write_text(text, encoding="utf-8")
            with self.subTest(text=text[:30]), self.assertRaises(att.AttestationError):
                att.load_trust_file(self.trust_path)

    def test_cli_export_and_verify(self):
        out = Path(self.tmp.name, "receipt.dsse.json")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(att.main(["export", "--data-dir", self.tmp.name, "--run-id", self.rid,
                                       "--key", str(self.key), "--output", str(out)]), 0)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(att.main(["verify", "--data-dir", self.tmp.name, "--run-id", self.rid,
                                       "--envelope", str(out), "--trust", str(self.trust_path)]), 0)
        self.assertEqual(json.loads(buffer.getvalue())["status"], "TRUSTED")


if __name__ == "__main__":
    unittest.main()
