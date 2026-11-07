"""Committed evidence must match its own hash lists and errata must bind to receipts.

Runs in the ordinary test suite (every pull request), so an edit to a
published receipt or to a hashed evidence bundle fails CI instead of merging
silently.
"""
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "evidence"
SUMS_NAME = "SHA256SUMS.sha256"
_SUM_LINE = re.compile(r"^([0-9a-f]{64}) [ *](\S.*)$")

RECEIPT_FORMAT = "kafaat.independent-qa-lab.receipt.v1"
ERRATA_FORMAT = "kafaat.independent-qa-lab.receipt-errata.v1"
KNOWN_FORMATS = {RECEIPT_FORMAT, ERRATA_FORMAT}
RECEIPT_KEYS = {"format", "subject", "requested_by", "measured_by", "measured_at", "access",
                "inputs", "does_not_change"}
ERRATA_KEYS = {"format", "corrects", "policy", "issued_by", "issued_at", "verdict_changed",
               "corrections", "unchanged"}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_sums(path):
    entries = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        match = _SUM_LINE.match(line)
        if not match:
            raise AssertionError(f"{path}:{number}: malformed line")
        digest, name = match.groups()
        if name.startswith("/") or ".." in Path(name).parts:
            raise AssertionError(f"{path}:{number}: path escapes the bundle: {name}")
        if name in entries:
            raise AssertionError(f"{path}:{number}: duplicate entry {name}")
        entries[name] = digest
    return entries


class HashListTests(unittest.TestCase):
    def test_every_hash_list_matches_its_bundle_exactly(self):
        lists = sorted(EVIDENCE.glob(f"*/{SUMS_NAME}"))
        self.assertGreaterEqual(len(lists), 2)
        for sums in lists:
            bundle = sums.parent
            with self.subTest(bundle=bundle.name):
                entries = parse_sums(sums)
                files = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*")
                         if p.is_file() and p.name != SUMS_NAME}
                self.assertEqual(sorted(files - set(entries)), [], "files not in the hash list")
                self.assertEqual(sorted(set(entries) - files), [], "listed files missing")
                for name, digest in entries.items():
                    self.assertEqual(sha256(bundle / name), digest, name)

    def test_parser_rejects_escapes_and_malformed_lines(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            sums = Path(d) / SUMS_NAME
            for text in ("a" * 64 + "  ../x\n", "a" * 64 + "  /etc/passwd\n", "nothex  x\n",
                         "a" * 64 + "  x\n" + "b" * 64 + "  x\n"):
                sums.write_text(text, encoding="utf-8")
                with self.subTest(text=text[:20]), self.assertRaises(AssertionError):
                    parse_sums(sums)


class ReceiptAndErrataTests(unittest.TestCase):
    def load(self, path):
        return json.loads(path.read_text(encoding="utf-8"))

    def formatted_documents(self):
        for path in sorted(EVIDENCE.glob("*.json")):
            data = self.load(path)
            if isinstance(data, dict) and str(data.get("format", "")).startswith("kafaat.independent-qa-lab."):
                yield path, data

    def test_lab_documents_use_known_formats_and_required_fields(self):
        seen = set()
        for path, data in self.formatted_documents():
            with self.subTest(file=path.name):
                self.assertIn(data["format"], KNOWN_FORMATS)
                required = RECEIPT_KEYS if data["format"] == RECEIPT_FORMAT else ERRATA_KEYS
                self.assertEqual(sorted(required - set(data)), [])
                seen.add(data["format"])
                if data["format"] == RECEIPT_FORMAT:
                    self.assertIs(data["does_not_change"].get("production_accepted"), False)
                    for name, digest in data["inputs"].items():
                        if name != "repo_head":
                            self.assertRegex(digest, r"^[0-9a-f]{64}$", name)
        self.assertEqual(seen, KNOWN_FORMATS)

    def test_errata_bind_to_the_exact_receipt_bytes(self):
        errata = [(p, d) for p, d in self.formatted_documents() if d["format"] == ERRATA_FORMAT]
        self.assertTrue(errata)
        for path, data in errata:
            with self.subTest(file=path.name):
                target = ROOT / data["corrects"]["path"]
                self.assertTrue(target.is_file(), data["corrects"]["path"])
                self.assertTrue(path.name.startswith(target.stem + "."),
                                "errata file is named after its receipt")
                self.assertEqual(sha256(target), data["corrects"]["sha256"])
                self.assertIs(data["verdict_changed"], False)
                self.assertTrue(data["corrections"])
                for item in data["corrections"]:
                    self.assertEqual(sorted({"field", "as_published", "correct"} - set(item)), [])

    def test_published_db_obs_receipt_is_unchanged(self):
        # Pinned so the receipt verified in PR #57 can only change with this test.
        receipt = EVIDENCE / "db-obs-001-lab-receipt-20261107.json"
        self.assertEqual(sha256(receipt),
                         "da5efe88edf3630b880504ca8540a4ec1e93ee95914e69c2e688f59aeb1df1df")


if __name__ == "__main__":
    unittest.main()
