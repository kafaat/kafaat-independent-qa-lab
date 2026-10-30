"""Real loopback HTTP checks with an independently modelled broker/audit boundary."""
import asyncio
import copy
import hashlib
import json
import os
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from qa_lab import rejection_live_probe as probe

TOKEN = 'local-test-only-0123456789abcdef0123456789'


class Fixture:
    def __init__(self, fault=''):
        self.fault, self.record, self.marker = fault, None, None
        self.publishes, self.observations = 0, 0
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def send(self, status, value, media='application/json'):
                raw = json.dumps(value).encode() if media == 'application/json' else value.encode()
                self.send_response(status)
                self.send_header('Content-Type', media)
                if fixture.fault != 'headers':
                    self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'none'")
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def do_POST(self):
                self.send(405 if fixture.fault != 'writes' else 200, {'error':'read_only'})
            def do_GET(self):
                if fixture.fault == 'redirect':
                    return self.send(302, {})
                if fixture.fault == 'oversize':
                    return self.send(200, {'data':'x' * probe.MAX_BODY})
                if self.headers.get('Authorization') != 'Bearer ' + TOKEN:
                    return self.send(200 if fixture.fault == 'auth' else 401, {'error':'unauthorized'})
                target = urlsplit(self.path)
                options = parse_qs(target.query)
                if options.get('reason') == ['NOT_ALLOWED'] or len(options.get('limit', [])) > 1:
                    return self.send(400, {'error':'invalid_request'})
                if fixture.fault == 'no_table':
                    return self.send(503, {'error':'storage_unavailable'})
                if target.path.startswith('/rejections'):
                    text = '<html>REVIEW_REQUIRED ' + (fixture.record or {}).get('rejection_id','') + '</html>'
                    if fixture.fault == 'html':
                        text = '<html>missing</html>'
                    if fixture.fault == 'leak':
                        text += fixture.marker or ''
                    return self.send(200, text, 'text/html')
                common = {'authority':'read-only', 'scope':'environment-operator','production_accepted':False}
                if target.path == '/api/rejections':
                    records = [copy.deepcopy(fixture.record)] if fixture.record else []
                    if options.get('after') and fixture.fault != 'cursor':
                        records = [r for r in records if r['rejection_id'] < options['after'][0]]
                    return self.send(200, {**common,'rejections':records,'next_cursor':None,
                        'page_count':len(records), 'ordering':'rejection-id-desc-not-chronological'})
                if fixture.record:
                    item = copy.deepcopy(fixture.record)
                    if fixture.fault == 'identity':
                        item['payload_sha256'] = '0' * 64
                    if fixture.fault == 'extra_field':
                        item['payload'] = 'should-never-be-exposed'
                    if fixture.fault == 'bool_count':
                        item['observations'] = True
                    if fixture.fault == 'timestamp':
                        item['last_seen'] = '2000-01-01'
                    if fixture.fault == 'claim':
                        common['production_accepted'] = True
                    return self.send(200, {**common, 'rejection':item,
                                           'payload_retained':False, 'replay_available':False})
                return self.send(404, {'error':'not_found'})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = probe.Client('http://127.0.0.1:' + str(self.server.server_port),
                                   TOKEN, allow_loopback=True)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    async def publish(self, raw):
        self.publishes += 1
        # Recompute the broker/content binding separately, without the probe helper.
        binding = {'stream_name':'QA_JOBS','consumer_name':'qa-audit-inbox','stream_sequence':81,
            'subject_sha256':hashlib.sha256(b'qa.jobs.created').hexdigest(),
            'payload_sha256':hashlib.sha256(raw).hexdigest()}
        rid = hashlib.sha256(json.dumps(binding, sort_keys=True, separators=(',',':')).encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        self.record = {**binding, 'rejection_id':rid, 'subject_tag':'qa.jobs.created',
            'payload_size':len(raw), 'reason_code':'invalid_json', 'review_state':'REVIEW_REQUIRED',
            'first_seen':now,'last_seen':now, 'observations':1,'max_delivery_count':1}
        self.marker = raw.decode().split('"')[3]
        if self.fault == 'db_identity':
            self.record['stream_sequence'] += 1
        return 81

    def observe(self, rid):
        self.observations += 1
        if self.fault == 'missing_audit':
            return None
        if self.fault == 'read_mutation' and self.observations > 1:
            self.record['observations'] += 1
        return copy.deepcopy(self.record)


class HTTPProbeTests(unittest.TestCase):
    def scenario(self, fault=''):
        fixture = Fixture(fault)
        self.addCleanup(fixture.close)
        result = asyncio.run(probe.run_probe(fixture.client, 'unused', 'unused',
            publish=fixture.publish, observe=fixture.observe, timeout=0.02))
        return result, fixture

    def test_full_loopback_receipt_and_digest(self):
        result, fixture = self.scenario()
        self.assertEqual(result['status'], 'PASSED', result)
        self.assertEqual(result['checks_passed'], 20)
        self.assertEqual(fixture.publishes, 1)
        claimed = result.pop('receipt_sha256')
        self.assertEqual(claimed, probe.digest(result))
        self.assertNotIn(TOKEN, json.dumps(result))
        self.assertNotIn(fixture.marker, json.dumps(result))
        self.assertFalse(result['production_accepted'])

    def assert_failure(self, fault, code=None):
        result, fixture = self.scenario(fault)
        self.assertEqual(result['status'], 'FAILED')
        if code:
            self.assertEqual(result['failure_code'], code)
        return fixture

    def test_missing_storage_never_publishes(self):
        self.assertEqual(self.assert_failure('no_table','review_storage_available').publishes,0)
    def test_auth_bypass_prevents_publication(self):
        self.assertEqual(self.assert_failure('auth').publishes,0)
    def test_missing_headers_prevents_publication(self):
        self.assertEqual(self.assert_failure('headers','unsafe_cache_headers').publishes,0)
    def test_redirect_not_followed(self):
        self.assertEqual(self.assert_failure('redirect','redirect_refused').publishes,0)
    def test_body_bound_enforced(self):
        self.assertEqual(self.assert_failure('oversize','oversized_response').publishes,0)
    def test_mutating_endpoint_is_failure(self):
        self.assertEqual(self.assert_failure('writes','review_write_rejected').publishes,0)
    def test_detail_hash_mismatch(self):
        self.assert_failure('identity','rejection_identity_mismatch')
    def test_extra_payload_field_rejected(self):
        self.assert_failure('extra_field','invalid_metadata_projection')
    def test_bool_is_not_count(self):
        self.assert_failure('bool_count','invalid_audit_count')
    def test_invalid_audit_timestamp(self):
        self.assert_failure('timestamp','invalid_audit_timestamp')
    def test_false_production_claim_rejected(self):
        self.assert_failure('claim','detail_authority')
    def test_exclusive_cursor_required(self):
        self.assert_failure('cursor','keyset_excludes_previous_record')
    def test_html_must_show_record(self):
        self.assert_failure('html','html_detail_visible')
    def test_raw_payload_marker_must_not_leak(self):
        self.assert_failure('leak','synthetic_payload_not_exposed')
    def test_missing_audit_is_failure(self):
        self.assert_failure('missing_audit','audit_not_observed')
    def test_db_identity_not_trusted_blindly(self):
        self.assert_failure('db_identity','rejection_identity_mismatch')
    def test_reading_does_not_mutate_audit(self):
        self.assert_failure('read_mutation','review_reads_leave_audit_unchanged')


class ValidationTests(unittest.TestCase):
    def test_public_or_credential_origin_rejected(self):
        for value in ('https://example.com', 'http://evil.railway.internal:8780',
                      'http://qa-dashboard.railway.internal:8780/?x=1',
                      'http://user@qa-dashboard.railway.internal:8780',
                      'http://qa-dashboard.railway.internal:8780/path',
                      'http://127.0.0.1:8780'):
            with self.subTest(value=value), self.assertRaises(probe.ProbeFailure):
                probe.Client(value, TOKEN)
    def test_private_origin_accepted(self):
        self.assertEqual(probe.Client('http://qa-dashboard.railway.internal:8780', TOKEN).port,8780)
    def test_bad_tokens_rejected(self):
        for value in ('x', 'x'*513, '${{qa-dashboard.QA_DASHBOARD_TOKEN}}', 'x'*24+'\n'):
            with self.subTest(value=value), self.assertRaises(probe.ProbeFailure):
                probe.Client('http://qa-dashboard.railway.internal:8780', value)
    def test_duplicate_keys_rejected(self):
        with self.assertRaises(probe.ProbeFailure):
            probe.decode(b'{"status":"PASSED","status":"FAILED"}')
    def test_nonfinite_json_rejected(self):
        with self.assertRaises(probe.ProbeFailure):
            probe.decode(b'{"v":NaN}')
    def test_puback_sequence_is_strict(self):
        for value in (True, 0, -1, '1', 2**63):
            with self.subTest(value=value), self.assertRaises(probe.ProbeFailure):
                probe.expected_record(b'{', value)
    def test_binding_changes_with_content_and_sequence(self):
        a = probe.expected_record(b'{', 1)
        self.assertNotEqual(a['rejection_id'], probe.expected_record(b'[',1)['rejection_id'])
        self.assertNotEqual(a['rejection_id'], probe.expected_record(b'{',2)['rejection_id'])
    def test_main_refuses_unscoped_execution(self):
        with patch.dict(os.environ, {}, clear=True), patch('builtins.print') as output:
            with self.assertRaises(SystemExit):
                probe.main()
        self.assertIn('configuration_or_http_acceptance_failed', output.call_args.args[0])
    def test_failure_never_includes_dependency_secrets(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        async def bad(raw):
            raise RuntimeError('postgresql://private-password-secret')
        result = asyncio.run(probe.run_probe(fixture.client, 'unused', 'unused', publish=bad))
        self.assertEqual(result['failure_code'], 'probe_dependency_failed')
        self.assertNotIn('private-password', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
