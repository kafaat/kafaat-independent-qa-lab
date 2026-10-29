"""Version-scoped retrieval with optional externally supplied, versioned embeddings.

Default retrieval is BM25, not a semantic model. Answers are verbatim evidence
excerpts, not LLM synthesis. Document text is untrusted data and never executed.
Project/source scopes must be provided by a trusted caller, not an end-user ACL.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter
from contextlib import closing
from pathlib import Path

SCHEMA = """CREATE TABLE IF NOT EXISTS qa_knowledge_chunks (
 project TEXT NOT NULL, document_id TEXT NOT NULL, source_sha TEXT NOT NULL,
 ordinal INTEGER NOT NULL, offset_start INTEGER NOT NULL, offset_end INTEGER NOT NULL,
 text TEXT NOT NULL, document_sha256 TEXT NOT NULL, integrity_sha256 TEXT NOT NULL,
 embedding_model TEXT, embedding_json TEXT,
 PRIMARY KEY(project, document_id, source_sha, ordinal))"""
FIELDS = ('project', 'document_id', 'source_sha', 'ordinal', 'offset_start',
          'offset_end', 'text', 'document_sha256', 'embedding_model', 'embedding_json')
SHA = re.compile(r'[0-9a-f]{40}\Z')


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def label(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError('invalid scope identifier')
    if any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('control characters in identifier')
    return value


def source_ref(value):
    if not isinstance(value, str) or not SHA.fullmatch(value) or value == '0' * 40:
        raise ValueError('source SHA must be pinned')
    return value


def tokens(text):
    # Normalize Arabic diacritics/tatweel and alef variants without modifying evidence.
    value = unicodedata.normalize('NFKD', text.casefold())
    value = ''.join(c for c in value if not unicodedata.combining(c) and c != '\u0640')
    value = value.translate(str.maketrans({'أ': 'ا', 'إ': 'ا', 'آ': 'ا'}))
    return re.findall(r'[^\W_]+', value, re.UNICODE)


def vector(value):
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 4096:
        raise ValueError('invalid embedding dimension')
    try:
        if any(type(x) not in (int, float) for x in value):
            raise ValueError('invalid embedding type')
        numbers = [float(x) for x in value]
        norm = math.hypot(*numbers)
        if not all(math.isfinite(x) for x in numbers) or not math.isfinite(norm) or norm == 0:
            raise ValueError('embedding must be finite and nonzero')
    except (OverflowError, TypeError):
        raise ValueError('invalid embedding') from None
    return [x / norm for x in numbers]


class KnowledgeIndex:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute(SCHEMA)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def ingest(self, *, project, document_id, source_sha, text,
               chunk_size=1000, embedder=None, embedding_model=None):
        project, document_id = label(project), label(document_id)
        source_sha = source_ref(source_sha)
        if not isinstance(text, str) or not text.strip():
            raise ValueError('document text required')
        try:
            raw = text.encode('utf-8')
        except UnicodeError:
            raise ValueError('invalid text encoding') from None
        if len(raw) > 1024 * 1024 or '\x00' in text:
            raise ValueError('document too large or contains NUL')
        if type(chunk_size) is not int or not 256 <= chunk_size <= 2000:
            raise ValueError('invalid chunk size')
        if (embedder is None) != (embedding_model is None):
            raise ValueError('embedder and versioned model ID required together')
        if embedding_model is not None:
            label(embedding_model)
            if '@' not in embedding_model:
                raise ValueError('embedding model revision required')
        chunks = [(i, text[i:i + chunk_size]) for i in range(0, len(text), chunk_size)]
        if len(chunks) > 512:
            raise ValueError('too many document chunks')
        doc_hash = hashlib.sha256(raw).hexdigest()
        records = []
        dimensions = set()
        for ordinal, (offset, chunk) in enumerate(chunks):
            encoded = None
            if embedder is not None:
                embedding = vector(embedder(chunk))
                dimensions.add(len(embedding))
                encoded = json.dumps(embedding, separators=(',', ':'), allow_nan=False)
            record = dict(zip(FIELDS, (project, document_id, source_sha, ordinal, offset,
                          offset + len(chunk), chunk, doc_hash, embedding_model, encoded)))
            record['integrity_sha256'] = digest(record)
            records.append(record)
        if len(dimensions) > 1:
            raise ValueError('inconsistent embedding dimensions')
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM qa_knowledge_chunks WHERE project=? AND document_id=? '
                             'AND source_sha=? ORDER BY ordinal',
                             (project, document_id, source_sha)).fetchall()
            if old:
                # Source content is immutable. Rebuild a separate index to change model/chunking.
                if [dict(row) for row in old] != records:
                    raise ValueError('immutable source version conflict')
                return {'reused': True, 'chunks': len(records), 'document_sha256': doc_hash}
            db.executemany('INSERT INTO qa_knowledge_chunks (' + ','.join(records[0]) + ') '
                           'VALUES (' + ','.join('?' for _ in records[0]) + ')',
                           [tuple(record.values()) for record in records])
        return {'reused': False, 'chunks': len(records), 'document_sha256': doc_hash}

    def _scoped(self, project, source_versions):
        label(project)
        if not isinstance(source_versions, dict) or not 1 <= len(source_versions) <= 100:
            raise ValueError('explicit source versions required')
        rows = []
        with closing(self.connect()) as db:
            for document_id, sha in source_versions.items():
                label(document_id)
                source_ref(sha)
                rows.extend(db.execute('SELECT * FROM qa_knowledge_chunks WHERE project=? '
                                       'AND document_id=? AND source_sha=? ORDER BY ordinal LIMIT 2001',
                                       (project, document_id, sha)).fetchall())
                if len(rows) > 2000:
                    raise ValueError('retrieval scope too large')
        records = sorted((dict(row) for row in rows), key=lambda row: (row['document_id'], row['ordinal']))
        for row in records:
            if digest({key: row[key] for key in FIELDS}) != row['integrity_sha256']:
                raise ValueError('knowledge integrity failure')
        return records

    def retrieve(self, query, *, project, source_versions, limit=5,
                 query_vector=None, embedding_model=None, min_cosine=0.5):
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError('bounded query required')
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError('invalid limit')
        if (query_vector is None) != (embedding_model is None):
            raise ValueError('query vector and model ID required together')
        if type(min_cosine) not in (int, float) or not math.isfinite(min_cosine) or not 0 <= min_cosine <= 1:
            raise ValueError('invalid cosine threshold')
        qv = vector(query_vector) if query_vector is not None else None
        if embedding_model is not None:
            label(embedding_model)
            if '@' not in embedding_model:
                raise ValueError('embedding model revision required')
        rows = self._scoped(project, source_versions)
        counts = [Counter(tokens(row['text'])) for row in rows]
        lengths = [sum(count.values()) for count in counts]
        avg = sum(lengths) / max(len(lengths), 1) or 1
        lexical = []
        for i, count in enumerate(counts):
            score = 0.0
            for term in set(tokens(query)):
                tf = count[term]
                if tf:
                    df = sum(term in item for item in counts)
                    idf = math.log(1 + (len(rows) - df + .5) / (df + .5))
                    score += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * lengths[i] / avg))
            if score > 0:
                lexical.append((score, i))
        semantic = []
        if qv is not None:
            for i, row in enumerate(rows):
                if row['embedding_model'] != embedding_model:
                    continue
                dv = vector(json.loads(row['embedding_json']))
                if len(dv) != len(qv):
                    raise ValueError('query/model embedding dimension mismatch')
                similarity = sum(a * b for a, b in zip(qv, dv))
                if similarity >= min_cosine:
                    semantic.append((similarity, i))
        scores = {}
        for ranking in (lexical, semantic):
            for rank, (_, i) in enumerate(sorted(ranking, key=lambda pair: (-pair[0], pair[1])), 1):
                scores[i] = scores.get(i, 0) + 1 / (60 + rank)
        hits = []
        for i in sorted(scores, key=lambda i: (-scores[i], rows[i]['document_id'], rows[i]['ordinal']))[:limit]:
            row = rows[i]
            hits.append({key: row[key] for key in FIELDS if key not in ('embedding_json', 'embedding_model')}
                        | {'integrity_sha256': row['integrity_sha256'], 'score': scores[i]})
        return {'mode': 'hybrid-supplied-vectors' if qv is not None else 'bm25',
                'candidate_count': len(rows), 'hits': hits}

    def answer(self, query, *, project, source_versions, **kwargs):
        result = self.retrieve(query, project=project, source_versions=source_versions, **kwargs)
        hits = result['hits']
        return {'status': 'EVIDENCE_FOUND' if hits else 'INSUFFICIENT_EVIDENCE',
                'answer_mode': 'extractive-no-llm', 'retrieval_mode': result['mode'],
                'answer': '\n\n'.join(f'[{i}] {hit["text"]}' for i, hit in enumerate(hits, 1)),
                'citations': [{key: value for key, value in hit.items() if key != 'score'} for hit in hits],
                'authority': 'reference-only', 'production_accepted': False}
