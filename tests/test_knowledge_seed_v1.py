"""Offline tests for reproducible Git-blob-bound, immutable demo corpus."""
import json
import tempfile
import unittest
from pathlib import Path

from qa_lab.knowledge_index import KnowledgeIndex
from qa_lab.knowledge_seed import git_blob_sha, seed


class KnowledgeSeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'docs').mkdir()
        self.doc = self.root / 'docs' / 'manual.md'
        self.doc.write_text('# Worker safety\nNever auto-replay RUNNING jobs.\n', encoding='utf-8')
        self.manifest = self.root / 'manifest.json'
        self.output = self.root / 'index.sqlite'
        self.payload = {'project': 'public', 'documents': [
            {'document_id': 'manual', 'path': 'docs/manual.md', 'git_blob_sha': git_blob_sha(self.doc.read_bytes())}]}
        self.write()

    def write(self):
        self.manifest.write_text(json.dumps(self.payload))

    def ingest(self):
        return seed(root=self.root, manifest=self.manifest, output=self.output)

    def test_roundtrip_immutable_scoped_retrieval(self):
        result = self.ingest()
        self.assertEqual(result['document_count'], 1)
        self.assertEqual(result['authority'], 'reference-only')
        store = KnowledgeIndex(self.output, read_only=True)
        self.assertEqual(store.answer('worker', project='public',
                                       source_versions=result['sources'])['status'], 'EVIDENCE_FOUND')
        self.assertEqual(self.output.stat().st_mode & 0o222, 0)

    def test_reject_git_blob_mismatch_without_output(self):
        self.payload['documents'][0]['git_blob_sha'] = 'a' * 40
        self.write()
        with self.assertRaisesRegex(ValueError, 'blob mismatch'):
            self.ingest()
        self.assertFalse(self.output.exists())

    def test_reject_changed_source_after_manifest_creation(self):
        self.doc.write_text('# Changed content')
        with self.assertRaises(ValueError):
            self.ingest()
        self.assertFalse(self.output.exists())

    def test_reject_absolute_and_traversal_paths(self):
        for bad in ('/tmp/manual.md', '../manual.md', 'docs/../manual.md', 'docs/manual.txt'):
            with self.subTest(bad=bad):
                self.payload['documents'][0]['path'] = bad
                self.write()
                with self.assertRaises(ValueError):
                    self.ingest()
                self.assertFalse(self.output.exists())

    def test_reject_symlink(self):
        alias = self.root / 'docs' / 'shortcut.md'
        alias.symlink_to(self.doc)
        self.payload['documents'][0]['path'] = 'docs/shortcut.md'
        self.write()
        with self.assertRaisesRegex(ValueError, 'regular in-root'):
            self.ingest()

    def test_reject_duplicate_document_id(self):
        self.payload['documents'].append(dict(self.payload['documents'][0]))
        self.write()
        with self.assertRaisesRegex(ValueError, 'duplicate document ID'):
            self.ingest()

    def test_reject_duplicate_json_manifest_keys(self):
        self.manifest.write_text('{"project":"public","project":"other","documents":[]}')
        with self.assertRaisesRegex(ValueError, 'duplicate manifest'):
            self.ingest()

    def test_reject_existing_index_without_overwrite(self):
        self.output.write_bytes(b'existing')
        with self.assertRaisesRegex(ValueError, 'new index path required'):
            self.ingest()
        self.assertEqual(self.output.read_bytes(), b'existing')

    def test_reject_oversize_document(self):
        self.doc.write_bytes(b'x' * (1024 * 1024 + 1))
        self.payload['documents'][0]['git_blob_sha'] = git_blob_sha(self.doc.read_bytes())
        self.write()
        with self.assertRaisesRegex(ValueError, 'oversized'):
            self.ingest()

    def test_reject_invalid_encoding(self):
        self.doc.write_bytes(b'\xffinvalid')
        self.payload['documents'][0]['git_blob_sha'] = git_blob_sha(self.doc.read_bytes())
        self.write()
        with self.assertRaisesRegex(ValueError, 'UTF-8'):
            self.ingest()

    def test_reject_incomplete_source_manifest(self):
        self.payload['documents'].append({'document_id':'missing','path':'docs/missing.md','git_blob_sha':'b'*40})
        self.write()
        with self.assertRaisesRegex(ValueError, 'regular in-root'):
            self.ingest()
        self.assertFalse(self.output.exists())

    def test_reject_unreadable_manifest_path(self):
        self.manifest.unlink()
        with self.assertRaises(ValueError):
            self.ingest()
