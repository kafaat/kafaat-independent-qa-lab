"""One-shot experiments acceptance: synthetic rejection -> durable audit -> HTTP.

Publishes exactly one harmless malformed JSON event. Never creates a stream,
consumer or database table, never writes SQL, and never erases audit evidence.
Only fixed metadata and hashes are emitted; credentials and bodies are not.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

PROJECT = '883c38a4-9e14-47be-8fd2-c4377192da81'
ENVIRONMENT = '4dfcdb2c-7377-4321-9793-527a111784bc'
STREAM, CONSUMER, SUBJECT = 'QA_JOBS', 'qa-audit-inbox', 'qa.jobs.created'
FIELDS = ('rejection_id', 'stream_name', 'consumer_name', 'stream_sequence',
          'subject_tag', 'subject_sha256', 'payload_sha256', 'payload_size',
          'reason_code', 'review_state', 'first_seen', 'last_seen',
          'observations', 'max_delivery_count')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
MAX_BODY = 262144


class ProbeFailure(ValueError):
    """Fixed diagnostic codes, never upstream body/exception text."""


def require(condition, code):
    if not condition:
        raise ProbeFailure(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    def nonfinite(_):
        raise ProbeFailure('nonfinite_json')
    try:
        return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ProbeFailure('invalid_json_response') from None


class Client:
    """Direct bounded HTTP; proxies and redirects are never followed."""
    def __init__(self, origin, token, *, allow_loopback=False):
        try:
            target = urlsplit(origin)
            allowed = target.hostname == 'qa-dashboard.railway.internal' and target.port == 8780
            if allow_loopback and target.hostname == '127.0.0.1':
                allowed = type(target.port) is int and 1 <= target.port <= 65535
            require(allowed and target.scheme == 'http' and target.path in ('', '/')
                    and not target.query and not target.fragment
                    and not target.username and not target.password, 'unsafe_target')
        except (TypeError, ValueError):
            raise ProbeFailure('unsafe_target') from None
        require(isinstance(token, str) and 24 <= len(token) <= 512 and token.isascii()
                and '${{' not in token and all(33 <= ord(c) <= 126 for c in token), 'invalid_token')
        self.host, self.port, self.token = target.hostname, target.port, token

    def call(self, path, *, auth='valid', method='GET'):
        require(method in ('GET', 'POST') and path.startswith(('/api/rejections', '/rejections'))
                and len(path) < 2048, 'invalid_probe_request')
        headers = {'Accept': 'application/json'}
        require(auth in ('valid', 'none', 'wrong'), 'invalid_probe_auth')
        if auth != 'none':
            headers['Authorization'] = 'Bearer ' + (self.token if auth == 'valid' else 'wrong-probe-token')
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            conn.request(method, path, body=b'' if method == 'POST' else None, headers=headers)
            response = conn.getresponse()
            body = response.read(MAX_BODY + 1)
            require(len(body) <= MAX_BODY, 'oversized_response')
            require(response.status not in (301, 302, 303, 307, 308), 'redirect_refused')
            require(response.getheader('Cache-Control') == 'no-store'
                    and response.getheader('X-Content-Type-Options') == 'nosniff', 'unsafe_cache_headers')
            require(response.getheader('Content-Security-Policy') is not None, 'missing_csp')
            media = (response.getheader('Content-Type') or '').split(';')[0]
            require(media in ('application/json', 'text/html'), 'invalid_content_type')
            return response.status, body, media
        except (OSError, http.client.HTTPException):
            raise ProbeFailure('http_transport_failed') from None
        finally:
            conn.close()


def expected_record(raw, sequence):
    require(isinstance(raw, bytes) and len(raw) <= 1024, 'invalid_synthetic_body')
    require(type(sequence) is int and 1 <= sequence <= 9223372036854775807, 'invalid_publish_sequence')
    binding = {'stream_name': STREAM, 'consumer_name': CONSUMER, 'stream_sequence': sequence,
               'subject_sha256': hashlib.sha256(SUBJECT.encode()).hexdigest(),
               'payload_sha256': hashlib.sha256(raw).hexdigest()}
    return {**binding, 'rejection_id': digest(binding), 'subject_tag': SUBJECT,
            'payload_size': len(raw), 'reason_code': 'invalid_json', 'review_state': 'REVIEW_REQUIRED'}


def validated_record(item, expected):
    require(isinstance(item, dict) and set(item) == set(FIELDS), 'invalid_metadata_projection')
    require(all(type(item.get(k)) is type(v) and item.get(k) == v
                for k, v in expected.items()), 'rejection_identity_mismatch')
    for key in ('observations', 'max_delivery_count'):
        require(type(item[key]) is int and 1 <= item[key] <= 9223372036854775807, 'invalid_audit_count')
    try:
        dates = [datetime.fromisoformat(item[k]) for k in ('first_seen', 'last_seen')]
        require(all(d.tzinfo is not None and d.utcoffset() is not None for d in dates)
                and dates[0] <= dates[1], 'invalid_audit_timestamp')
    except (ValueError, TypeError):
        raise ProbeFailure('invalid_audit_timestamp') from None
    return item


def read_record(dsn, rid):
    """Independent SQL observation, no initializer or mutating statement."""
    import psycopg
    require(isinstance(rid, str) and SHA256.fullmatch(rid), 'invalid_rejection_id')
    with psycopg.connect(dsn, connect_timeout=5) as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute("SET LOCAL statement_timeout = '3000ms'")
        row = db.execute('SELECT ' + ','.join(FIELDS) +
                         ' FROM qa_nats_rejections WHERE rejection_id=%s', (rid,)).fetchone()
    if row is None:
        return None
    item = dict(zip(FIELDS, row))
    for key in ('first_seen', 'last_seen'):
        item[key] = item[key].isoformat()
    return item


def preflight(client, check):
    rid = '0' * 64
    for path, label in [('/api/rejections', 'list'), ('/api/rejections/' + rid, 'detail'),
                        ('/rejections', 'html')]:
        for auth in ('none', 'wrong'):
            status, _, _ = client.call(path, auth=auth)
            check(label + '_' + auth + '_token_rejected', status == 401)
    for suffix, label in [('?reason=NOT_ALLOWED', 'invalid_reason'),
                          ('?limit=1&limit=2', 'duplicate_filter')]:
        status, _, _ = client.call('/api/rejections' + suffix)
        check(label + '_rejected', status == 400)
    status, _, _ = client.call('/api/rejections', method='POST')
    check('review_write_rejected', status == 405)
    status, raw, media = client.call('/api/rejections?limit=1')
    data = decode(raw)
    check('review_storage_available', status == 200 and media == 'application/json'
          and isinstance(data, dict) and isinstance(data.get('rejections'), list)
          and data.get('authority') == 'read-only' and data.get('production_accepted') is False)


def review_checks(client, snapshot, expected, marker, observe, check):
    rid = expected['rejection_id']
    status, raw, media = client.call('/api/rejections/' + rid)
    detail = decode(raw)
    require(isinstance(detail, dict), 'invalid_detail_envelope')
    check('detail_authority', status == 200 and media == 'application/json'
          and set(detail) == {'rejection', 'authority', 'scope', 'production_accepted',
                              'payload_retained', 'replay_available'}
          and detail.get('authority') == 'read-only' and detail.get('scope') == 'environment-operator'
          and detail.get('production_accepted') is False and detail.get('payload_retained') is False
          and detail.get('replay_available') is False)
    item = validated_record(detail.get('rejection'), expected)
    check('detail_matches_committed_audit', item == snapshot)
    # An adjacent exclusive fingerprint bound finds this exact item even if
    # unrelated records exist before it; no unbounded scan or page guessing.
    options = {'stream': STREAM, 'reason': 'invalid_json', 'limit': 1}
    if int(rid, 16) < 2 ** 256 - 1:
        options['after'] = f'{int(rid, 16) + 1:064x}'
    status, raw_list, _ = client.call('/api/rejections?' + urlencode(options))
    listing = decode(raw_list)
    require(isinstance(listing, dict), 'invalid_list_envelope')
    items = listing.get('rejections')
    check('filtered_keyset_exact_record', status == 200 and isinstance(items, list)
          and len(items) == 1 and items[0] == snapshot and listing.get('page_count') == 1
          and listing.get('ordering') == 'rejection-id-desc-not-chronological'
          and listing.get('authority') == 'read-only' and listing.get('scope') == 'environment-operator'
          and listing.get('production_accepted') is False
          and listing.get('next_cursor') in (None, rid))
    # With an exclusive cursor at our own ID, the record cannot appear again.
    options['after'] = rid
    status, raw_next, _ = client.call('/api/rejections?' + urlencode(options))
    nxt = decode(raw_next)
    require(isinstance(nxt, dict) and isinstance(nxt.get('rejections'), list), 'invalid_next_page')
    check('keyset_excludes_previous_record', status == 200 and len(nxt['rejections']) <= 1
          and all(isinstance(r, dict) and isinstance(r.get('rejection_id'), str)
                  and r['rejection_id'] < rid for r in nxt['rejections']))
    status, html, media = client.call('/rejections/' + rid)
    check('html_detail_visible', status == 200 and media == 'text/html'
          and rid.encode() in html and b'REVIEW_REQUIRED' in html)
    status, html_list, media = client.call('/rejections?' + urlencode({**options, 'after':
                              f'{min(int(rid,16)+1,2**256-1):064x}'}))
    check('html_list_visible', status == 200 and media == 'text/html' and rid.encode() in html_list)
    check('synthetic_payload_not_exposed', all(marker.encode() not in body
          for body in (raw, raw_list, html, html_list)))
    check('review_reads_leave_audit_unchanged', observe(rid) == snapshot)


async def run_probe(client, dsn, nats_url, *, identity=None, timeout=30,
                    publish=None, observe=None):
    require(type(timeout) in (int, float) and 0 < timeout <= 90, 'invalid_timeout')
    result = {'schema_version': '1.0', 'probe_id': str(uuid.uuid4()), 'status': 'RUNNING',
              'started_at': datetime.now(timezone.utc).isoformat(),
              'scope': 'synthetic-nats-rejection-audit-http-review',
              'authority': 'execution-evidence-only', 'production_accepted': False,
              'source_attestation': 'deployment-reference-only',
              'deployment_identity': identity or {}, 'checks': [],
              'synthetic_messages_published': 0, 'publish_outcome': 'NOT_ATTEMPTED',
              'sql_write_operations': 0,
              'audit_records_deleted': 0}
    def check(name, passed):
        result['checks'].append({'name': name, 'passed': bool(passed)})
        require(passed, name)
    connection = None
    try:
        preflight(client, check)
        if publish is None:
            import nats
            connection = await nats.connect(nats_url, connect_timeout=5,
                                            max_reconnect_attempts=0, allow_reconnect=False)
            js = connection.jetstream()
            info = await js.stream_info(STREAM)
            require(info.config.storage == 'file', 'stream_not_persistent')
            await js.consumer_info(STREAM, CONSUMER)
            async def publish(raw):
                ack = await js.publish(SUBJECT, raw, timeout=5,
                    headers={'Nats-Msg-Id': 'review-probe-' + result['probe_id'],
                             'Nats-Expected-Stream': STREAM})
                require(ack.stream == STREAM and not ack.duplicate, 'invalid_publish_ack')
                return ack.seq
        observe = observe or (lambda rid: read_record(dsn, rid))
        marker = 'qa-review-probe-' + result['probe_id']
        raw = ('{"synthetic_review_probe":"' + marker + '",').encode()
        result['publish_outcome'] = 'UNKNOWN'
        sequence = await publish(raw)
        result['publish_outcome'] = 'CONFIRMED'
        result['synthetic_messages_published'] = 1
        expected = expected_record(raw, sequence)
        result['rejection_id'] = expected['rejection_id']
        result['payload_sha256'] = expected['payload_sha256']
        result['stream_sequence'] = sequence
        check('synthetic_event_persisted', True)
        deadline = time.monotonic() + timeout
        while True:
            snapshot = observe(expected['rejection_id'])
            if snapshot is not None:
                validated_record(snapshot, expected)
                break
            require(time.monotonic() < deadline, 'audit_not_observed')
            await asyncio.sleep(0.2)
        check('consumer_recorded_rejection', True)
        review_checks(client, snapshot, expected, marker, observe, check)
        result['audit_snapshot_sha256'] = digest(snapshot)
        result['status'] = 'PASSED'
    except Exception as exc:
        result['status'] = 'FAILED'
        result['failure_code'] = str(exc) if isinstance(exc, ProbeFailure) else 'probe_dependency_failed'
    finally:
        if connection is not None:
            try:
                await connection.close()
            except Exception:
                result['status'] = 'FAILED'
                result['failure_code'] = 'nats_close_failed'
    result['finished_at'] = datetime.now(timezone.utc).isoformat()
    result['checks_passed'] = sum(x['passed'] for x in result['checks'])
    result['receipt_sha256'] = digest(result)
    return result


def main():
    try:
        require(os.environ.get('RAILWAY_PROJECT_ID') == PROJECT
                and os.environ.get('RAILWAY_ENVIRONMENT_ID') == ENVIRONMENT
                and os.environ.get('QA_REJECTION_SYNTHETIC') == '1', 'experiments_opt_in_required')
        sha = os.environ.get('QA_SOURCE_SHA', '')
        require(re.fullmatch(r'[0-9a-f]{40}', sha) and sha != '0' * 40
                and sha == os.environ.get('RAILWAY_GIT_COMMIT_SHA'), 'probe_source_mismatch')
        require(os.environ['QA_NATS_URL'] == 'nats://qa-nats.railway.internal:4222', 'unsafe_nats_target')
        from psycopg.conninfo import conninfo_to_dict
        pg = conninfo_to_dict(os.environ['QA_POSTGRES_DSN'])
        require(pg.get('host', '').endswith('.railway.internal')
                and ',' not in pg['host'], 'unsafe_database_target')
        client = Client(os.environ['QA_DASHBOARD_URL'], os.environ['QA_DASHBOARD_TOKEN'])
        if os.environ.get('QA_REJECTION_INCLUDE_HTTP') == '1':
            from .http_acceptance import main as http_main
            http_main()
        identity = {k: os.environ.get(k) for k in ('RAILWAY_DEPLOYMENT_ID', 'RAILWAY_PROJECT_ID',
                      'RAILWAY_ENVIRONMENT_ID', 'RAILWAY_GIT_COMMIT_SHA')}
        result = asyncio.run(run_probe(client, os.environ['QA_POSTGRES_DSN'],
                                       os.environ['QA_NATS_URL'], identity=identity))
    except Exception:
        # Never print DSNs, headers, responses or exception strings.
        result = {'status': 'FAILED', 'failure_code': 'configuration_or_http_acceptance_failed',
                  'scope': 'synthetic-nats-rejection-audit-http-review', 'production_accepted': False}
        result['receipt_sha256'] = digest(result)
    print(canonical(result), flush=True)
    if result['status'] != 'PASSED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
