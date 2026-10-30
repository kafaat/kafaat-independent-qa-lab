"""Data contract, worker dispatch and real loopback HTTP regressions."""
import base64
import hashlib
import http.client
import json
import os
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from audio_fixtures import fixture
from qa_lab import audio_jobs as jobs
from qa_lab.pg_api import PostgreSQLHandler, validate_submission as smoke_submission
from qa_lab.pg_worker import execute_one

TOKEN = 'audio-test-only-token-not-a-secret-123456'
SOURCE = 'a' * 40


def request_for(raw=None, **changes):
    raw = fixture(frames=64) if raw is None else raw
    result = {'recipe': jobs.RECIPE, 'idempotency_key': str(uuid.uuid4()),
              'project': 'audio-tests', 'source_sha': SOURCE,
              'audio_base64': base64.b64encode(raw).decode(),
              'audio_sha256': hashlib.sha256(raw).hexdigest()}
    return {**result, **changes}


class MemoryQueue:
    def __init__(self):
        self.items = {}
        self.claimed = set()
        self.requests = {}

    def submit(self, request, digest):
        key = request['idempotency_key']
        if key in self.requests:
            rid, old = self.requests[key]
            if old != digest:
                raise ValueError('idempotency key conflict')
            return {'run_id': rid, 'reused': True}
        rid = str(uuid.uuid4())
        self.items[rid] = {'run_id': rid, 'request': request, 'status': 'PENDING'}
        self.requests[key] = (rid, digest)
        return {'run_id': rid, 'reused': False}

    def claim(self, worker):
        for rid, item in self.items.items():
            if rid not in self.claimed:
                self.claimed.add(rid)
                item['status'] = 'RUNNING'
                return {'run_id': rid, 'request': item['request']}
        return None

    def finish(self, rid, worker, status, receipt):
        self.items[rid].update(status=status, receipt=receipt)
        return {'run_id': rid, 'status': status}

    def get(self, rid):
        item = self.items[rid]
        return {key: item[key] for key in ('run_id', 'status', 'receipt') if key in item}


class AudioJobContractTests(unittest.TestCase):
    def test_valid_request_returns_exact_bytes(self):
        raw = fixture()
        self.assertEqual(jobs.validate_submission(request_for(raw)), raw)

    def test_missing_or_extra_fields_rejected(self):
        req = request_for()
        for key in req:
            with self.subTest(key=key), self.assertRaises(jobs.AudioSubmissionError):
                jobs.validate_submission({k: v for k, v in req.items() if k != key})
        for key in ('command', 'path', 'url', 'shell', 'expectations'):
            with self.subTest(key=key), self.assertRaises(jobs.AudioSubmissionError):
                jobs.validate_submission({**req, key: 'not permitted'})

    def test_nonobject_rejected(self):
        for req in (None, [], 'text', 3):
            with self.subTest(req=req), self.assertRaises(jobs.AudioSubmissionError):
                jobs.validate_submission(req)

    def test_nonstring_fields_rejected(self):
        req = request_for()
        for key in req:
            with self.subTest(key=key), self.assertRaises(jobs.AudioSubmissionError):
                jobs.validate_submission({**req, key: {'unexpected': 'object'}})

    def test_identifier_controls_and_size_rejected(self):
        for value in ('', ' ', 'x' * 129, 'x\ny', 'x\u202ey', '\ud800'):
            for field in ('project', 'idempotency_key'):
                with self.subTest(value=repr(value), field=field), self.assertRaises(jobs.AudioSubmissionError):
                    jobs.validate_submission(request_for(**{field: value}))

    def test_bad_source_references_rejected(self):
        for value in ('main', '0' * 40, 'A' * 40, 'g' * 40, 'a' * 39, 'a' * 41):
            with self.subTest(value=value), self.assertRaises(jobs.AudioSubmissionError):
                jobs.validate_submission(request_for(source_sha=value))

    def test_unapproved_recipe_rejected(self):
        for value in ('python-smoke', 'shell', 'audio-pcm-v2'):
            with self.subTest(value=value), self.assertRaises(jobs.AudioSubmissionError):
                jobs.validate_submission(request_for(recipe=value))

    def test_digest_tampering_rejected(self):
        req = request_for()
        with self.assertRaisesRegex(jobs.AudioSubmissionError, 'digest_mismatch'):
            jobs.validate_submission({**req, 'audio_sha256': '0' * 64})

    def test_invalid_digest_format(self):
        for value in ('', 'x', 'G' * 64, 'A' * 64):
            with self.subTest(value=value), self.assertRaisesRegex(jobs.AudioSubmissionError, 'invalid_digest'):
                jobs.validate_submission(request_for(audio_sha256=value))

    def test_base64_rejects_invalid_and_noncanonical_data(self):
        for value in ('!', ' AA==', 'AA==\n', 'AA===', '/x==', 'é', 'AAAA_'):
            with self.subTest(value=value), self.assertRaises(jobs.AudioSubmissionError):
                jobs.validate_submission(request_for(audio_base64=value, **(
                    {'audio_sha256': hashlib.sha256(b'\xff').hexdigest()} if value == '/x==' else {})))

    def test_size_is_checked_before_decoding(self):
        with patch.object(jobs.base64, 'b64decode') as decoder:
            with self.assertRaisesRegex(jobs.AudioSubmissionError, 'size_limit'):
                jobs.validate_submission(request_for(audio_base64='A' * 100000))
            decoder.assert_not_called()

    def test_decoded_size_limit_and_boundary(self):
        for count in (0, 65537):
            with self.subTest(count=count), self.assertRaisesRegex(jobs.AudioSubmissionError, 'size_limit'):
                jobs.validate_submission(request_for(b'x' * count))
        self.assertEqual(len(jobs.validate_submission(request_for(b'x' * jobs.MAX_AUDIO_BYTES))), jobs.MAX_AUDIO_BYTES)

    def test_duplicate_json_key_rejected(self):
        text = jobs.canonical(request_for())
        duplicate = text[:-1] + ',"recipe":"audio-pcm-v1"}'
        with self.assertRaises(jobs.AudioSubmissionError):
            jobs.decode_submission(duplicate)

    def test_nonfinite_or_malformed_json_rejected(self):
        for raw in (b'{', b'\xff', b'{"n":NaN}', b'[' * 2000):
            with self.subTest(size=len(raw)), self.assertRaises(jobs.AudioSubmissionError):
                jobs.decode_submission(raw)

    def test_receipt_binds_bytes_output_and_request(self):
        req = request_for()
        receipt = jobs.execute(req)
        self.assertEqual(receipt['status'], 'PASSED')
        self.assertEqual(receipt['input_sha256'], req['audio_sha256'])
        self.assertEqual(receipt['audio_metrics']['sha256'], req['audio_sha256'])
        self.assertEqual(receipt['request_sha256'], jobs.digest(req))
        self.assertEqual(receipt['output_sha256'], jobs.digest({'status': 'PASSED', 'audio_metrics': receipt['audio_metrics']}))
        self.assertEqual(receipt['receipt_sha256'], jobs.digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'}))
        self.assertEqual(receipt['parser_sha256'], jobs.PARSER_SHA256)
        self.assertFalse(receipt['production_accepted'])
        self.assertEqual(receipt['source_attestation'], 'request-reference-only')
        self.assertNotIn(req['audio_base64'], jobs.canonical(receipt))

    def test_malformed_audio_is_failed_not_a_transport_success(self):
        receipt = jobs.execute(request_for(b'not a waveform'))
        self.assertEqual(receipt['status'], 'FAILED')
        self.assertEqual(receipt['reason'], 'invalid_size')
        self.assertEqual(receipt['exit_code'], 1)

    def test_silent_audio_explicit_policy_failure(self):
        receipt = jobs.execute(request_for(fixture(silent=True)))
        self.assertEqual(receipt['status'], 'FAILED')
        self.assertEqual(receipt['reason'], 'silent_pcm')

    def test_worker_rechecks_input_digest_before_parser(self):
        with patch.object(jobs.audio_pcm, 'validate_pcm') as parse:
            receipt = jobs.execute(request_for(audio_sha256='0' * 64))
            self.assertEqual(receipt['status'], 'BLOCKED')
            parse.assert_not_called()

    def test_parser_content_mismatch_fails_closed(self):
        with patch.object(jobs, 'PARSER_SHA256', '0' * 64), patch.object(jobs.audio_pcm, 'validate_pcm', return_value={}) as parse:
            receipt = jobs.execute(request_for())
            self.assertEqual(receipt['status'], 'BLOCKED')
            self.assertEqual(receipt['reason'], 'audio_parser_identity_mismatch')
            parse.assert_not_called()

    def test_unexpected_parser_errors_are_sanitized(self):
        with patch.object(jobs.audio_pcm, 'validate_pcm', side_effect=RuntimeError('private-password')):
            receipt = jobs.execute(request_for())
            self.assertEqual(receipt['status'], 'BLOCKED')
            self.assertNotIn('private-password', jobs.canonical(receipt))

    def test_smoke_contract_has_not_become_an_audio_route(self):
        with self.assertRaises(ValueError):
            smoke_submission(request_for())
        req = {k: v for k, v in request_for().items() if k not in ('audio_base64', 'audio_sha256')}
        req['recipe'] = 'python-smoke'
        self.assertEqual(smoke_submission(req), req)


class AudioWorkerTests(unittest.TestCase):
    def make_queue(self, request=None):
        queue = MemoryQueue()
        req = request_for() if request is None else request
        rid = queue.submit(req, jobs.digest(req))['run_id']
        return queue, rid

    def test_disabled_by_default(self):
        queue, rid = self.make_queue()
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(execute_one(queue, 'worker')['status'], 'BLOCKED')
        self.assertEqual(queue.items[rid]['receipt']['reason'], 'audio recipe disabled')

    def test_valid_audio_uses_no_subprocess(self):
        queue, rid = self.make_queue()
        with patch.dict(os.environ, {'QA_ENABLE_AUDIO_PCM': '1'}), patch('qa_lab.pg_worker.subprocess.run') as proc:
            self.assertEqual(execute_one(queue, 'worker')['status'], 'PASSED')
            proc.assert_not_called()
        self.assertEqual(queue.items[rid]['receipt']['recipe'], jobs.RECIPE)

    def test_invalid_persisted_envelope_is_blocked(self):
        queue, rid = self.make_queue(request_for(audio_sha256='0' * 64))
        with patch.dict(os.environ, {'QA_ENABLE_AUDIO_PCM': '1'}):
            self.assertEqual(execute_one(queue, 'worker')['status'], 'BLOCKED')

    def test_invalid_wave_reaches_terminal_failed(self):
        queue, rid = self.make_queue(request_for(fixture(silent=True)))
        with patch.dict(os.environ, {'QA_ENABLE_AUDIO_PCM': '1'}):
            self.assertEqual(execute_one(queue, 'worker')['status'], 'FAILED')

    def test_unknown_recipe_still_blocked(self):
        queue, rid = self.make_queue(request_for(recipe='shell'))
        with patch.dict(os.environ, {'QA_ENABLE_AUDIO_PCM': '1'}):
            self.assertEqual(execute_one(queue, 'worker')['status'], 'BLOCKED')

    def test_no_pending_job_is_idle(self):
        self.assertIsNone(execute_one(MemoryQueue(), 'worker'))

    def test_claimed_job_not_reexecuted(self):
        queue, rid = self.make_queue()
        with patch.dict(os.environ, {'QA_ENABLE_AUDIO_PCM': '1'}):
            execute_one(queue, 'worker')
            self.assertIsNone(execute_one(queue, 'worker'))


class AudioHTTPTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'QA_ENABLE_AUDIO_PCM': '1', 'QA_API_TOKEN': TOKEN})
        self.env.start()
        self.queue = MemoryQueue()
        handler = type('AudioHandler', (PostgreSQLHandler,), {'queue': self.queue})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(3)
        self.server.server_close()
        self.env.stop()

    def call(self, payload=None, *, path='/v1/audio-runs', token=TOKEN,
             method='POST', headers=None, duplicate_auth=False, duplicate_length=False):
        body = jobs.canonical(request_for() if payload is None else payload).encode() if not isinstance(payload, bytes) else payload
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.putrequest(method, path)
            if token is not None:
                conn.putheader('Authorization', 'Bearer ' + token)
            if duplicate_auth:
                conn.putheader('Authorization', 'Bearer ' + TOKEN)
            hs = {'Content-Type': 'application/json', 'Content-Length': str(len(body))}
            hs.update(headers or {})
            for k, v in hs.items():
                conn.putheader(k, v)
            if duplicate_length:
                conn.putheader('Content-Length', str(len(body)))
            conn.endheaders(body)
            response = conn.getresponse()
            data = response.read()
            return response.status, json.loads(data), dict(response.getheaders())
        finally:
            conn.close()

    def test_valid_submission_and_get_receipt(self):
        req = request_for()
        status, result, headers = self.call(req)
        self.assertEqual(status, 202)
        execute_one(self.queue, 'worker')
        status, detail, _ = self.call(path='/v1/runs/' + result['run_id'], method='GET')
        self.assertEqual(status, 200)
        self.assertEqual(detail['status'], 'PASSED')
        self.assertNotIn(req['audio_base64'], jobs.canonical(detail))
        self.assertEqual(headers['Cache-Control'], 'no-store')

    def test_unauthorized_rejected(self):
        for token in (None, 'wrong'):
            with self.subTest(token=token):
                self.assertEqual(self.call(token=token)[0], 401)
        self.assertFalse(self.queue.items)

    def test_duplicate_auth_rejected(self):
        self.assertEqual(self.call(duplicate_auth=True)[0], 401)

    def test_disabled_route_is_not_found(self):
        with patch.dict(os.environ, {'QA_ENABLE_AUDIO_PCM': '0'}):
            self.assertEqual(self.call()[0], 404)
        self.assertFalse(self.queue.items)

    def test_idempotency_same_input_and_changed_input(self):
        req = request_for()
        first = self.call(req)
        replay = self.call(req)
        self.assertEqual(replay[1], {'run_id': first[1]['run_id'], 'reused': True})
        changed = request_for(fixture(amplitude=24), idempotency_key=req['idempotency_key'])
        self.assertEqual(self.call(changed)[0], 409)
        self.assertEqual(len(self.queue.items), 1)

    def test_bad_digest_never_queued(self):
        self.assertEqual(self.call(request_for(audio_sha256='0' * 64))[0], 400)
        self.assertFalse(self.queue.items)

    def test_duplicate_keys_never_queued(self):
        raw = jobs.canonical(request_for())[:-1] + ',"recipe":"audio-pcm-v1"}'
        self.assertEqual(self.call(raw.encode())[0], 400)
        self.assertFalse(self.queue.items)

    def test_body_limit(self):
        self.assertEqual(self.call(b'x', headers={'Content-Length': '92161'})[0], 413)

    def test_invalid_framing(self):
        self.assertEqual(self.call(duplicate_length=True)[0], 400)
        self.assertEqual(self.call(headers={'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(self.call(headers={'Content-Length': '+10'})[0], 400)

    def test_json_media_type_required(self):
        self.assertEqual(self.call(headers={'Content-Type': 'text/plain'})[0], 415)

    def test_invalid_json_is_sanitized(self):
        status, data, _ = self.call(b'{"privatepassword":')
        self.assertEqual(status, 400)
        self.assertNotIn('privatepassword', jobs.canonical(data))

    def test_no_file_url_or_command_fields(self):
        for field in ('path', 'url', 'command'):
            with self.subTest(field=field):
                self.assertEqual(self.call(request_for(**{field: '/private'}))[0], 400)
        self.assertFalse(self.queue.items)

    def test_query_override_rejected(self):
        self.assertEqual(self.call(path='/v1/audio-runs?recipe=shell')[0], 400)

    def test_smoke_route_unchanged(self):
        req = {'idempotency_key': 'smoke', 'project': 'tests', 'source_sha': SOURCE, 'recipe': 'python-smoke'}
        self.assertEqual(self.call(req, path='/v1/runs')[0], 202)
        self.assertEqual(self.call(request_for(), path='/v1/runs')[0], 400)

    def test_database_errors_do_not_expose_credentials(self):
        with patch.object(self.queue, 'submit', side_effect=RuntimeError('postgres-password')):
            status, body, _ = self.call()
        self.assertEqual(status, 503)
        self.assertNotIn('postgres-password', jobs.canonical(body))
