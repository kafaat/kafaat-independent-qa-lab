"""Isolated real-loopback acceptance of the private knowledge probe."""
import hashlib
import http.server
import json
import os
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from qa_lab.knowledge_index import KnowledgeIndex
from qa_lab.knowledge_service import KnowledgeHandler
from qa_lab.knowledge_live_probe import (
    PrivateClient, ProbeFailure, check_answer, decode, main, pinned_corpus,
    run_probe, validate_token, validate_url,
)

TOKEN = 'local-qa-knowledge-test-token-with-32-characters'


def git_blob(raw):
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


class LivePrivateKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'docs').mkdir()
        self.docs = (
            ('service-boundary', 'BOUNDARY.md', 'Worker is only a source of execution evidence. ' * 3),
            ('postgres-acceptance', 'POSTGRES_ACCEPTANCE.md', 'PostgreSQL acceptance checks event delivery and evidence. ' * 3),
            ('legacy-parity-requirements', 'LEGACY_PARITY_EVIDENCE_REQUIREMENTS.md', 'Legacy source comparisons require pinned evidence. ' * 3),
        )
        manifest = {'project': 'qa-public-docs', 'documents': []}
        self.index = KnowledgeIndex(self.root / 'knowledge.sqlite')
        self.scope = {}
        for doc_id, name, content in self.docs:
            raw = content.encode('utf-8')
            path = self.root / 'docs' / name
            path.write_bytes(raw)
            sha = git_blob(raw)
            self.index.ingest(project='qa-public-docs', document_id=doc_id, source_sha=sha, text=content)
            self.scope[doc_id] = sha
            manifest['documents'].append({'document_id': doc_id, 'path': 'docs/' + name, 'git_blob_sha': sha})
        (self.root / 'docs' / 'QA_KNOWLEDGE_SEED.json').write_text(json.dumps(manifest))
        handler = type('TestKnowledge', (KnowledgeHandler,), {'index': self.index,
                                                             'scope': {'project': 'qa-public-docs', 'sources': self.scope}})
        self.environment = patch.dict(os.environ, {'QA_KNOWLEDGE_TOKEN': TOKEN})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.port = self.server.server_port
        self.client = PrivateClient(f'http://127.0.0.1:{self.port}', TOKEN,
                                    allowed_host='127.0.0.1', allowed_port=self.port)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_all_eleven_private_http_contracts(self):
        corpus, manifest_sha = pinned_corpus(self.root)
        receipt = run_probe(self.client, corpus, source_sha='a' * 40, manifest_sha256=manifest_sha)
        self.assertEqual(receipt['status'], 'PASSED')
        self.assertEqual(receipt['checks_passed'], 11)
        self.assertEqual(len(set(receipt['checks'])), 11)
        self.assertIn('postgres-acceptance', receipt['query_citation_proof']['documents'])
        self.assertFalse(receipt['production_accepted'])
        self.assertNotIn(TOKEN, json.dumps(receipt))
        expected = dict(receipt)
        expected.pop('receipt_sha256')
        self.assertEqual(receipt['receipt_sha256'], hashlib.sha256(json.dumps(
            expected, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest())

    def test_invalid_citation_source_version_is_rejected(self):
        corpus, _ = pinned_corpus(self.root)
        _, reply = self.client.exchange('/v1/answer', payload={'query': 'PostgreSQL'})
        reply['citations'][0]['source_sha'] = 'b' * 40
        with self.assertRaisesRegex(ProbeFailure, 'source-version-mismatch'):
            check_answer(reply, corpus)

    def test_tampered_citation_excerpt_is_rejected(self):
        corpus, _ = pinned_corpus(self.root)
        _, reply = self.client.exchange('/v1/answer', payload={'query': 'PostgreSQL'})
        reply['citations'][0]['text'] = 'forged result'
        with self.assertRaisesRegex(ProbeFailure, 'citation-text-mismatch'):
            check_answer(reply, corpus)

    def test_corpus_mutation_fails_before_network_probe(self):
        (self.root / 'docs' / 'POSTGRES_ACCEPTANCE.md').write_text('tampered PostgreSQL')
        with self.assertRaisesRegex(ProbeFailure, 'corpus-source-mismatch'):
            pinned_corpus(self.root)

    def test_missing_manifest_document_is_rejected(self):
        p = self.root / 'docs' / 'QA_KNOWLEDGE_SEED.json'
        data = json.loads(p.read_text())
        data['documents'].pop()
        p.write_text(json.dumps(data))
        with self.assertRaisesRegex(ProbeFailure, 'unexpected-corpus-scope'):
            pinned_corpus(self.root)

    def test_auth_errors_cannot_reveal_token(self):
        code, data = self.client.exchange('/healthz', auth='wrong')
        self.assertEqual(code, 401)
        self.assertNotIn(TOKEN, json.dumps(data))

    def test_rejects_external_and_untrusted_targets(self):
        urls = ('https://qa-knowledge.railway.internal:8781',
                'http://example.org:8781', 'http://localhost:8781',
                'http://qa-knowledge.railway.internal:1',
                'http://username:password@qa-knowledge.railway.internal:8781',
                'http://qa-knowledge.railway.internal:8781/a',
                'http://qa-knowledge.railway.internal:8781/#x',
                'http://qa-knowledge.railway.internal:8781/?query=1',
                'http://qa-knowledge.railway.internal:8781@evil.com:8781')
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ProbeFailure):
                validate_url(url)

    def test_rejects_unsafe_and_unresolved_tokens(self):
        for token in ('', 'short', 'x' * 600, 'x ' * 25, '${{qa-knowledge.QA_KNOWLEDGE_TOKEN}}'):
            with self.subTest(token=token[:12]), self.assertRaises(ProbeFailure):
                validate_token(token)

    def test_duplicate_response_keys_are_rejected(self):
        with self.assertRaisesRegex(ProbeFailure, 'invalid-json-response'):
            decode(b'{"status":"PASSED","status":"FAILED"}')

    def test_nonfinite_json_response_is_rejected(self):
        with self.assertRaisesRegex(ProbeFailure, 'invalid-json-response'):
            decode(b'{"answer":NaN}')

    def test_rejects_unexpected_route(self):
        with self.assertRaisesRegex(ProbeFailure, 'invalid-route'):
            self.client.exchange('/admin')

    def test_rejects_invalid_origin_even_before_credential_use(self):
        with self.assertRaisesRegex(ProbeFailure, 'unsafe-target'):
            PrivateClient('http://example.org:8781', TOKEN)


if __name__ == '__main__':
    unittest.main()
