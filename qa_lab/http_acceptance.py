"""Bounded private-network HTTP acceptance; records evidence, never release approval.

Exercises the deployed API and dashboard, not a direct queue submission.
Tokens are referenced at runtime, never included in receipts or exception text.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

MAX_RESPONSE = 262144
TERMINAL = frozenset({'PASSED', 'FAILED', 'BLOCKED', 'TIMED_OUT'})


class AcceptanceFailure(RuntimeError):
    """Safe, bounded diagnostic; no upstream response bodies or credentials."""


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def sha256(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def private_origin(value):
    """Refuse public, credential-bearing, query-bearing or non-HTTP targets."""
    parts = urlsplit(value)
    host = parts.hostname or ''
    if (parts.scheme not in ('http', 'https') or parts.username or parts.password
            or parts.path not in ('', '/') or parts.query or parts.fragment
            or not (host.endswith('.railway.internal') or host in ('localhost', '127.0.0.1', '::1'))):
        raise ValueError('private HTTP origin required')
    try:
        port = parts.port or (443 if parts.scheme == 'https' else 80)
    except ValueError:
        raise ValueError('invalid private HTTP port') from None
    return parts.scheme, host, port


def validate_token(token):
    if (not isinstance(token, str) or len(token) < 24 or not token.isascii()
            or any(c.isspace() for c in token) or '${{' in token
            or token.lower().startswith(('replace-', 'placeholder'))):
        raise ValueError('resolved non-placeholder service token required')
    return token


@dataclass(frozen=True)
class Config:
    api: str
    dashboard: str
    api_token: str
    dashboard_token: str
    requested_source_sha: str
    timeout: float = 90

    def __post_init__(self):
        private_origin(self.api)
        private_origin(self.dashboard)
        validate_token(self.api_token)
        validate_token(self.dashboard_token)
        if not re.fullmatch(r'[0-9a-f]{40}', self.requested_source_sha):
            raise ValueError('full requested source SHA required')
        if not 1 <= self.timeout <= 180:
            raise ValueError('timeout outside acceptance limits')


def request(origin, path, *, method='GET', token=None, payload=None):
    """One request only: no proxy, redirects or automatic POST retries."""
    scheme, host, port = private_origin(origin)
    if not path.startswith('/') or path.startswith('//'):
        raise ValueError('relative HTTP path required')
    cls = http.client.HTTPSConnection if scheme == 'https' else http.client.HTTPConnection
    conn = cls(host, port, timeout=8)
    headers = {'Accept': 'application/json'}
    if token is not None:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if payload is not None:
        data = canonical(payload).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    try:
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        body = response.read(MAX_RESPONSE + 1)
        if len(body) > MAX_RESPONSE:
            raise AcceptanceFailure('response_size_limit')
        # http.client does not follow redirects or forward bearer tokens.
        if 300 <= response.status < 400:
            raise AcceptanceFailure('redirect_refused')
        return response.status, dict(response.getheaders()), body
    finally:
        conn.close()


def decode(body):
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise AcceptanceFailure('invalid_json_response') from None
    if not isinstance(value, dict):
        raise AcceptanceFailure('non_object_response')
    return value


def exact_events(rows):
    """Rows bind each inbox entry to the actual outbox ID/subject/payload."""
    if len(rows) != 2:
        return False
    if {row[1] for row in rows} != {'qa.jobs.created', 'qa.jobs.finished'}:
        return False
    return all(row[3] is not None and row[4] == row[1] and row[5] == row[2]
               for row in rows)


def run_checks(config, observe, persist=lambda result: None, *, identity=None):
    """observe(run_id) reads DB only; persist receives safe partial/final receipts."""
    probe_id = str(uuid.uuid4())
    result = {
        'schema_version': '2.0', 'probe_id': probe_id, 'status': 'RUNNING',
        'scope': 'http-api-worker-outbox-inbox-dashboard',
        'started_at': datetime.now(timezone.utc).isoformat(),
        'requested_source_sha': config.requested_source_sha,
        'source_attestation': 'request-reference-only',
        'deployment_identity': identity or {}, 'checks': [],
        'authority': 'execution-evidence-only', 'production_accepted': False,
    }

    def check(name, condition):
        result['checks'].append({'name': name, 'passed': bool(condition)})
        persist(result)
        if not condition:
            raise AcceptanceFailure(name)

    def call(origin, path, **kwargs):
        try:
            return request(origin, path, **kwargs)
        except AcceptanceFailure:
            raise
        except (OSError, http.client.HTTPException, ValueError):
            raise AcceptanceFailure('http_transport_failed') from None

    persist(result)
    try:
        health, _, body = call(config.api, '/healthz')
        check('api_health', health == 200 and decode(body).get('backend') == 'postgres')
        missing_id = '00000000-0000-0000-0000-000000000000'
        status, _, _ = call(config.api, '/v1/runs/' + missing_id)
        check('api_get_no_token_rejected', status == 401)
        status, _, _ = call(config.api, '/v1/runs/' + missing_id, token='deliberately-wrong')
        check('api_get_wrong_token_rejected', status == 401)
        req = {'idempotency_key': 'http-acceptance-' + probe_id, 'project': 'http-acceptance',
               'source_sha': config.requested_source_sha, 'recipe': 'python-smoke'}
        status, _, _ = call(config.api, '/v1/runs', method='POST', payload=req)
        check('api_post_no_token_rejected', status == 401)
        status, _, body = call(config.api, '/v1/runs', method='POST', token=config.api_token, payload=req)
        check('authenticated_submit', status == 202)
        submitted = decode(body)
        try:
            run_id = str(uuid.UUID(submitted['run_id']))
        except (KeyError, ValueError, TypeError, AttributeError):
            raise AcceptanceFailure('invalid_run_identity') from None
        result['run_id'] = run_id
        status, _, body = call(config.api, '/v1/runs', method='POST', token=config.api_token, payload=req)
        reused = decode(body)
        check('idempotent_same_request', status == 202 and reused.get('run_id') == run_id
              and reused.get('reused') is True)
        status, _, body = call(config.api, '/v1/runs', method='POST', token=config.api_token,
                               payload={**req, 'project': 'changed-project'})
        # The deployed API currently uses 400. A future 409 is also a conflict.
        check('idempotency_conflict_rejected', status in (400, 409)
              and decode(body).get('error') == 'idempotency key conflict')
        status, _, body = call(config.api, '/v1/runs', method='POST', token=config.api_token,
                               payload={**req, 'idempotency_key': req['idempotency_key'] + '-invalid', 'recipe': 'shell'})
        check('unapproved_recipe_rejected', status == 400 and decode(body).get('error') == 'recipe not allowed')
        deadline = time.monotonic() + config.timeout
        while True:
            status, headers, body = call(config.api, '/v1/runs/' + run_id, token=config.api_token)
            run = decode(body)
            if status != 200 or run.get('run_id') != run_id:
                raise AcceptanceFailure('run_read_failed')
            if run.get('status') in TERMINAL:
                break
            if time.monotonic() >= deadline:
                raise AcceptanceFailure('worker_timeout_unknown_outcome')
            time.sleep(0.4)
        receipt = run.get('receipt') or {}
        check('worker_smoke_receipt', run.get('status') == 'PASSED'
              and receipt.get('exit_code') == 0 and receipt.get('stdout_tail', '').strip() == 'qa-smoke-ok'
              and receipt.get('source_sha') == config.requested_source_sha
              and receipt.get('authority') == 'execution-evidence-only')
        check('api_cache_headers', headers.get('Cache-Control') == 'no-store'
              and headers.get('X-Content-Type-Options') == 'nosniff')
        result['worker_receipt_sha256'] = sha256(receipt)
        deadline = time.monotonic() + config.timeout
        while True:
            rows = observe(run_id)
            if exact_events(rows):
                break
            if time.monotonic() >= deadline:
                result['event_counts'] = {'outbox': len(rows), 'inbox_matches': sum(
                    row[4] == row[1] and row[5] == row[2] for row in rows)}
                raise AcceptanceFailure('event_identity_or_delivery_incomplete')
            time.sleep(0.4)
        result['event_ids'] = sorted(str(row[0]) for row in rows)
        check('exact_outbox_inbox_pair', True)
        for label, token in [('no_token', None), ('wrong_token', 'deliberately-wrong')]:
            status, _, _ = call(config.dashboard, '/', token=token)
            check('dashboard_' + label + '_rejected', status == 401)
        status, headers, page = call(config.dashboard, '/', token=config.dashboard_token)
        text = page.decode('utf-8', errors='strict')
        check('dashboard_authenticated_run_visible', status == 200 and run_id in text and 'PASSED' in text)
        check('dashboard_cache_headers', headers.get('Cache-Control') == 'no-store'
              and headers.get('X-Content-Type-Options') == 'nosniff')
        result['dashboard_sha256'] = hashlib.sha256(page).hexdigest()
        result['status'] = 'PASSED'
    except Exception as exc:
        result['status'] = 'FAILED'
        result['failure'] = str(exc) if isinstance(exc, AcceptanceFailure) else type(exc).__name__
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        result['receipt_sha256'] = sha256(result)
        persist(result)
    return result


SCHEMA = """CREATE TABLE IF NOT EXISTS qa_acceptance_receipts (
 probe_id UUID PRIMARY KEY, status TEXT NOT NULL, result JSONB NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""


def main():
    import psycopg
    config = Config(os.environ['QA_API_URL'], os.environ['QA_DASHBOARD_URL'],
                    os.environ['QA_API_TOKEN'], os.environ['QA_DASHBOARD_TOKEN'],
                    os.environ['QA_SOURCE_SHA'])
    dsn = os.environ['QA_POSTGRES_DSN']

    def connect():
        return psycopg.connect(dsn, connect_timeout=5, options='-c statement_timeout=5000')

    with connect() as db:
        db.execute(SCHEMA)

    def persist(result):
        with connect() as db:
            db.execute('INSERT INTO qa_acceptance_receipts(probe_id,status,result) VALUES (%s,%s,%s::jsonb) '
                       'ON CONFLICT(probe_id) DO UPDATE SET status=excluded.status,result=excluded.result,updated_at=now()',
                       (uuid.UUID(result['probe_id']), result['status'], canonical(result)))

    def observe(run_id):
        with connect() as db:
            return db.execute("SELECT o.id,o.subject,o.payload,o.published_at,i.subject,i.payload "
                              "FROM qa_outbox o LEFT JOIN qa_inbox i ON i.event_id=o.id "
                              "WHERE o.payload->>'run_id'=%s", (run_id,)).fetchall()

    identity = {name: os.environ.get(name) for name in
                ('RAILWAY_GIT_COMMIT_SHA', 'RAILWAY_DEPLOYMENT_ID', 'RAILWAY_ENVIRONMENT_ID')}
    result = run_checks(config, observe, persist, identity=identity)
    print(canonical(result), flush=True)
    if result['status'] != 'PASSED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
