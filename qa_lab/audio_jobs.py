"""Opt-in, data-only PCM jobs. No caller commands, paths, URLs or code execution.

The full base64 input is retained in qa_jobs.request; responses and events expose
only bounded metrics and digests. The token is environment-wide, not tenant ACL.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import audio_pcm

RECIPE = 'audio-pcm-v1'
MAX_AUDIO_BYTES = 65536
MAX_BODY_BYTES = 92160
PARSER_SHA256 = '2ec8dde53cb0afde7f2b1090dad6d14be8e408a108315486dbc38d5a8100f9b0'
FIELDS = frozenset({'idempotency_key', 'project', 'source_sha', 'recipe',
                    'audio_base64', 'audio_sha256'})


class AudioSubmissionError(ValueError):
    """A fixed, non-sensitive request rejection code."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def enabled():
    return os.environ.get('QA_ENABLE_AUDIO_PCM') == '1'


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AudioSubmissionError('audio_duplicate_json_key')
        result[key] = value
    return result


def _nonfinite(_value):
    raise AudioSubmissionError('audio_invalid_json')


def decode_submission(body):
    try:
        request = json.loads(body, object_pairs_hook=unique_object,
                             parse_constant=_nonfinite)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AudioSubmissionError('audio_invalid_json') from None
    validate_submission(request)
    return request


def validate_submission(request):
    if type(request) is not dict or set(request) != FIELDS:
        raise AudioSubmissionError('audio_exact_fields_required')
    if any(type(value) is not str for value in request.values()):
        raise AudioSubmissionError('audio_string_fields_required')
    for key in ('idempotency_key', 'project'):
        value = request[key]
        if (not value.strip() or len(value) > 128 or
                any(unicodedata.category(c).startswith('C') for c in value)):
            raise AudioSubmissionError('audio_invalid_identifier')
    if (not re.fullmatch(r'[0-9a-f]{40}', request['source_sha'])
            or request['source_sha'] == '0' * 40):
        raise AudioSubmissionError('audio_invalid_source_reference')
    if request['recipe'] != RECIPE:
        raise AudioSubmissionError('audio_recipe_not_allowed')
    encoded = request['audio_base64']
    if not 1 <= len(encoded) <= 4 * ((MAX_AUDIO_BYTES + 2) // 3):
        raise AudioSubmissionError('audio_size_limit')
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise AudioSubmissionError('audio_invalid_base64') from None
    if base64.b64encode(raw).decode('ascii') != encoded:
        raise AudioSubmissionError('audio_noncanonical_base64')
    if not 1 <= len(raw) <= MAX_AUDIO_BYTES:
        raise AudioSubmissionError('audio_size_limit')
    expected = request['audio_sha256']
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise AudioSubmissionError('audio_invalid_digest')
    if hashlib.sha256(raw).hexdigest() != expected:
        raise AudioSubmissionError('audio_digest_mismatch')
    return raw


def execute(request):
    """Revalidate stored input before calling the pinned, bounded data parser."""
    receipt = {
        'schema_version': 'audio-job-1.0', 'recipe': RECIPE, 'status': 'BLOCKED',
        'scope': 'submitted-integer-pcm-integrity',
        'authority': 'execution-evidence-only', 'production_accepted': False,
        'environment_claim': 'unattested-worker',
        'source_attestation': 'request-reference-only',
        'started_at': datetime.now(timezone.utc).isoformat(),
    }
    try:
        raw = validate_submission(request)
        receipt.update(source_sha=request['source_sha'],
                       request_sha256=digest(request),
                       input_sha256=hashlib.sha256(raw).hexdigest(),
                       input_size_bytes=len(raw))
        # Content identity is useful evidence, NOT a signature/host attestation.
        with Path(audio_pcm.__file__).open('rb') as source:
            parser_source = source.read(131073)
        if hashlib.sha256(parser_source).hexdigest() != PARSER_SHA256:
            raise AudioSubmissionError('audio_parser_identity_mismatch')
        receipt['parser_sha256'] = PARSER_SHA256
        receipt['parser_identity_kind'] = 'local-source-file-content-only'
        try:
            metrics = audio_pcm.validate_pcm(raw)
            output = {'status': 'PASSED', 'audio_metrics': metrics}
        except audio_pcm.AudioContractError as exc:
            output = {'status': 'FAILED', 'reason': str(exc)}
        receipt.update(output)
        receipt['output_sha256'] = digest(output)
        receipt['exit_code'] = 0 if receipt['status'] == 'PASSED' else 1
    except AudioSubmissionError as exc:
        receipt['reason'] = str(exc)
    except Exception:
        # No payload, path, credentials or exception strings in evidence.
        receipt['status'] = 'BLOCKED'
        receipt['reason'] = 'audio_checker_unavailable'
    receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
    receipt['receipt_sha256'] = digest(receipt)
    return receipt


def post(handler):
    """Serve only the fixed authenticated audio route; queue owns persistence."""
    if not enabled():
        return handler.reply(404, {'error': 'not_found'})
    if handler.path != '/v1/audio-runs':
        return handler.reply(400, {'error': 'audio_invalid_route'})
    if (handler.headers.get_all('Transfer-Encoding', []) or
            len(handler.headers.get_all('Content-Length', [])) != 1):
        return handler.reply(400, {'error': 'audio_invalid_framing'})
    lengths = handler.headers['Content-Length']
    if not lengths.isascii() or not lengths.isdecimal() or len(lengths) > 6:
        return handler.reply(400, {'error': 'audio_invalid_framing'})
    length = int(lengths)
    if not 1 <= length <= MAX_BODY_BYTES:
        return handler.reply(413, {'error': 'size_limit'})
    if (len(handler.headers.get_all('Content-Type', [])) != 1 or
            handler.headers.get_content_type() != 'application/json'):
        return handler.reply(415, {'error': 'json_required'})
    try:
        body = handler.rfile.read(length)
        if len(body) != length:
            raise AudioSubmissionError('audio_truncated_request')
        request = decode_submission(body)
    except AudioSubmissionError as exc:
        return handler.reply(400, {'error': str(exc)})
    except (TimeoutError, OSError):
        return handler.reply(408, {'error': 'audio_request_timeout'})
    try:
        result = handler.queue.submit(request, digest(request))
    except ValueError as exc:
        if str(exc) == 'idempotency key conflict':
            return handler.reply(409, {'error': 'idempotency key conflict'})
        return handler.reply(400, {'error': 'audio_invalid_request'})
    except Exception:
        return handler.reply(503, {'error': 'storage_unavailable'})
    return handler.reply(202, result)
