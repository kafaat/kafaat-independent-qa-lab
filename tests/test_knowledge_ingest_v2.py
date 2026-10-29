import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from qa_lab.knowledge_index import KnowledgeIndex, vector


class KnowledgeIngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'index.sqlite'
        self.store = KnowledgeIndex(self.path)
        self.doc = dict(project='lab', document_id='manual', source_sha='a' * 40, text='Worker recovery instructions')

    def test_persist_and_reopen(self):
        result = self.store.ingest(**self.doc)
        self.assertFalse(result['reused'])
        hit = KnowledgeIndex(self.path).retrieve('worker', project='lab', source_versions={'manual': 'a' * 40})['hits'][0]
        self.assertEqual(hit['document_sha256'], result['document_sha256'])

    def test_repeat_is_idempotent(self):
        self.store.ingest(**self.doc)
        self.assertTrue(self.store.ingest(**self.doc)['reused'])

    def test_same_version_different_content_rejected(self):
        self.store.ingest(**self.doc)
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.store.ingest(**(self.doc | {'text': 'Changed worker rules'}))

    def test_chunk_offsets_match_original(self):
        text = 'الاستعادة الآمنة للمهمة ' * 50
        self.store.ingest(**(self.doc | {'text': text}), chunk_size=256)
        hits = self.store.retrieve('الاستعادة', project='lab', source_versions={'manual': 'a' * 40}, limit=20)['hits']
        self.assertGreater(len(hits), 1)
        for hit in hits:
            self.assertEqual(text[hit['offset_start']:hit['offset_end']], hit['text'])

    def test_bad_source_refs_rejected(self):
        for value in ('main', '', '0' * 40, 'a' * 39, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.ingest(**(self.doc | {'source_sha': value}))

    def test_document_boundaries(self):
        for value in ('', ' ', 'x\0y', 'x' * (1024 * 1024 + 1)):
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                self.store.ingest(**(self.doc | {'text': value}))

    def test_chunk_size_bounds(self):
        for value in (True, 0, 255, 2001, '256'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.ingest(**self.doc, chunk_size=value)

    def test_embedding_requires_pinned_model(self):
        with self.assertRaisesRegex(ValueError, 'revision'):
            self.store.ingest(**self.doc, embedder=lambda _: [1, 0], embedding_model='model')

    def test_invalid_vectors_fail_closed(self):
        for value in ([0, 0], [True, 1], [float('nan')], [float('inf')], [10**1000], [], [1] * 4097):
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValueError):
                vector(value)

    def test_inconsistent_vectors_leave_no_partial_document(self):
        values = iter([[1, 0], [1, 0, 0]])
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            self.store.ingest(**(self.doc | {'text': 'x' * 400}), chunk_size=256,
                              embedder=lambda _: next(values), embedding_model='fixture@1')
        with closing(self.store.connect()) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM qa_knowledge_chunks').fetchone()[0], 0)

    def test_tampered_text_is_not_returned(self):
        self.store.ingest(**self.doc)
        with closing(self.store.connect()) as db, db:
            db.execute("UPDATE qa_knowledge_chunks SET text='forged worker result'")
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.store.retrieve('worker', project='lab', source_versions={'manual': 'a' * 40})
