"""Offline creation of a public-docs knowledge index from pinned Git blob hashes.

No downloads, no model calls and no HTTP ingestion. Build inputs must be a
reviewed repository tree and an explicit, immutable corpus manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from .knowledge_index import KnowledgeIndex, label, source_ref

DOC_PATH = re.compile(r'docs/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.md\Z')
MAX_MANIFEST_BYTES = 32 * 1024


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate manifest key')
        result[key] = value
    return result


def git_blob_sha(raw: bytes) -> str:
    """Git's SHA-1 content addressing, not a cryptographic signer identity."""
    return hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()


def seed(*, root, manifest, output):
    root, manifest, output = Path(root).resolve(), Path(manifest), Path(output)
    if not root.is_dir():
        raise ValueError('regular corpus root required')
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError('manifest must be a bounded regular file')
    spec = json.loads(manifest.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
    if not isinstance(spec, dict) or set(spec) != {'project', 'documents'}:
        raise ValueError('exact manifest fields required')
    project = label(spec['project'])
    docs = spec['documents']
    if not isinstance(docs, list) or not 1 <= len(docs) <= 20:
        raise ValueError('bounded document list required')
    if output.is_symlink() or output.exists() or not output.parent.is_dir():
        raise ValueError('new index path required')
    resolved = []
    seen = set()
    for doc in docs:
        if not isinstance(doc, dict) or set(doc) != {'document_id', 'path', 'git_blob_sha'}:
            raise ValueError('exact document fields required')
        doc_id = label(doc['document_id'])
        src = source_ref(doc['git_blob_sha'])
        rel = doc['path']
        if not isinstance(rel, str) or not DOC_PATH.fullmatch(rel) or '..' in Path(rel).parts:
            raise ValueError('unsafe corpus path')
        if doc_id in seen:
            raise ValueError('duplicate document ID')
        seen.add(doc_id)
        candidate = root / rel
        if not candidate.is_file() or candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
            raise ValueError('regular in-root source required')
        at = root
        for part in Path(rel).parts:
            at = at / part
            if at.is_symlink():
                raise ValueError('symlinked corpus path')
        with candidate.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024 or git_blob_sha(raw) != src:
            raise ValueError('source Git blob mismatch or oversized text')
        try:
            text = raw.decode('utf-8')
        except UnicodeError as exc:
            raise ValueError('source must be UTF-8') from exc
        if not text.strip() or '\x00' in text:
            raise ValueError('invalid source text')
        resolved.append((doc_id, src, text))

    with tempfile.TemporaryDirectory(prefix='.qa-index-', dir=output.parent) as tmp:
        temporary = Path(tmp) / 'index.sqlite'
        index = KnowledgeIndex(temporary)
        for doc_id, src, text in resolved:
            index.ingest(project=project, document_id=doc_id, source_sha=src, text=text)
        scope = {doc_id: src for doc_id, src, _ in resolved}
        summary = index.verify_scope(project=project, source_versions=scope)
        os.chmod(temporary, 0o444)
        os.replace(temporary, output)
    KnowledgeIndex(output, read_only=True).verify_scope(project=project, source_versions=scope)
    return {'project': project, 'sources': scope, 'document_count': summary['documents'],
            'chunk_count': summary['chunks'], 'authority': 'reference-only',
            'source_attestation': 'git-blob-content-address-only'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', required=True)
    p.add_argument('--manifest', required=True)
    p.add_argument('--output', required=True)
    args = p.parse_args()
    print(json.dumps(seed(root=args.root, manifest=args.manifest, output=args.output),
                     sort_keys=True, ensure_ascii=False))


if __name__ == '__main__':
    main()
