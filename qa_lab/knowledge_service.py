"""Opt-in read-only knowledge HTTP service; no model downloads or remote ingestion."""
from __future__ import annotations

import json
import os
from http.server import ThreadingHTTPServer
from .knowledge_index import KnowledgeIndex
from .pg_dashboard import Dashboard, valid_token


def unique_object(pairs):
    """Reject ambiguous JSON objects, including duplicate query/limit keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


class KnowledgeHandler(Dashboard):
    index = None
    scope = None

    def authorized(self):
        # Reuse protected bytewise token comparison, with a separate service token.
        import hmac
        token = os.environ.get('QA_KNOWLEDGE_TOKEN', '')
        values = self.headers.get_all('Authorization', [])
        return valid_token(token) and len(values) == 1 and hmac.compare_digest(values[0].encode(), ('Bearer ' + token).encode())

    def do_GET(self):
        if not self.authorized():
            return self.reply(401, {'error': 'unauthorized'})
        if self.path == '/healthz':
            return self.reply(200, {'status': 'ok', 'mode': 'extractive-no-llm'})
        return self.reply(404, {'error': 'not_found'})

    def do_POST(self):
        if not self.authorized():
            return self.reply(401, {'error': 'unauthorized'})
        if self.path not in ('/v1/search', '/v1/answer'):
            return self.reply(404, {'error': 'not_found'})
        if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
            return self.reply(400, {'error': 'invalid_framing'})
        if self.headers.get_content_type() != 'application/json':
            return self.reply(415, {'error': 'json_required'})
        try:
            length = int(self.headers['Content-Length'])
            if not 1 <= length <= 8192:
                return self.reply(413, {'error': 'size_limit'})
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError('truncated request')
            body = json.loads(raw, object_pairs_hook=unique_object)
            if not isinstance(body, dict) or set(body) - {'query', 'limit'} or 'query' not in body:
                raise ValueError('invalid query contract')
            method = self.index.answer if self.path == '/v1/answer' else self.index.retrieve
            result = method(body['query'], project=self.scope['project'],
                            source_versions=self.scope['sources'], limit=body.get('limit', 5))
            return self.reply(200, result)
        except (ValueError, TypeError, UnicodeError):
            return self.reply(400, {'error': 'invalid_request_or_evidence'})
        except Exception:
            return self.reply(503, {'error': 'knowledge_unavailable'})


def main():
    if not valid_token(os.environ.get('QA_KNOWLEDGE_TOKEN', '')):
        raise RuntimeError('valid QA_KNOWLEDGE_TOKEN required')
    scope = json.loads(os.environ['QA_KNOWLEDGE_SCOPE'], object_pairs_hook=unique_object)
    if not isinstance(scope, dict) or set(scope) != {'project', 'sources'}:
        raise ValueError('server-side project and source scope required')
    index = KnowledgeIndex(os.environ['QA_KNOWLEDGE_DB'], read_only=True)
    index.verify_scope(project=scope['project'], source_versions=scope['sources'])  # Fail at startup on an invalid/tampered scope.
    handler = type('ScopedKnowledge', (KnowledgeHandler,), {'index': index, 'scope': scope})
    with ThreadingHTTPServer((os.environ.get('QA_BIND_HOST', '127.0.0.1'),
                              int(os.environ.get('PORT', '8781'))), handler) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
