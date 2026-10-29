"""Durable metadata-only review records, committed BEFORE terminating delivery.

No payload, headers, credentials or raw exception text are persisted or logged.
This is an audit index, not a payload-retaining dead-letter queue. It never
approves releases or automatically replays an untrusted event.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

from .nats_inbox import accept_event

LOG = logging.getLogger(__name__)
MAX_EVENT_BYTES = 131072
REASONS = frozenset({'invalid_json', 'invalid_event', 'event_id_collision', 'oversized_event'})
NAMES = re.compile(r'[A-Za-z0-9_.-]{1,128}\Z')
REJECTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_nats_rejections (
 rejection_id CHAR(64) PRIMARY KEY,
 stream_name TEXT NOT NULL,
 consumer_name TEXT NOT NULL,
 stream_sequence BIGINT NOT NULL CHECK (stream_sequence > 0),
 subject_tag TEXT NOT NULL,
 subject_sha256 CHAR(64) NOT NULL,
 payload_sha256 CHAR(64) NOT NULL,
 payload_size BIGINT NOT NULL CHECK (payload_size >= 0),
 reason_code TEXT NOT NULL CHECK (reason_code IN
     ('invalid_json','invalid_event','event_id_collision','oversized_event')),
 review_state TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED'
     CHECK (review_state = 'REVIEW_REQUIRED'),
 first_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
 last_seen TIMESTAMPTZ NOT NULL DEFAULT now(),
 observations BIGINT NOT NULL DEFAULT 1,
 max_delivery_count BIGINT NOT NULL CHECK (max_delivery_count > 0)
);
"""


class InvalidDelivery(ValueError):
    """Safe fixed reason; never includes the message body or upstream error."""


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _constant(_value):
    raise ValueError('nonfinite JSON')


def decode_event(raw):
    if not isinstance(raw, bytes):
        raise InvalidDelivery('invalid_json')
    if len(raw) > MAX_EVENT_BYTES:
        raise InvalidDelivery('oversized_event')
    try:
        return json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise InvalidDelivery('invalid_json') from None


def rejection_record(message, reason):
    """Use broker identity plus content fingerprint, not untrusted event_id alone."""
    if reason not in REASONS:
        raise ValueError('invalid rejection reason')
    metadata = message.metadata
    stream, consumer = metadata.stream, metadata.consumer
    sequence, deliveries = metadata.sequence.stream, metadata.num_delivered
    if any(not isinstance(name, str) or not NAMES.fullmatch(name)
           for name in (stream, consumer)):
        raise ValueError('invalid broker identity')
    if any(type(n) is not int or not 1 <= n <= 9223372036854775807
           for n in (sequence, deliveries)):
        raise ValueError('invalid broker sequence')
    raw, subject = message.data, message.subject
    if not isinstance(raw, bytes) or not isinstance(subject, str):
        raise ValueError('invalid message representation')
    record = {
        'stream_name': stream, 'consumer_name': consumer,
        'stream_sequence': sequence,
        'subject_sha256': hashlib.sha256(subject.encode('utf-8')).hexdigest(),
        'payload_sha256': hashlib.sha256(raw).hexdigest(),
    }
    encoded = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('utf-8')
    record['rejection_id'] = hashlib.sha256(encoded).hexdigest()
    record.update(subject_tag=subject if subject in ('qa.jobs.created', 'qa.jobs.finished') else 'other',
                  payload_size=len(raw), reason_code=reason, max_delivery_count=deliveries)
    return record


def store_rejection(conn, record):
    """Caller owns transaction/commit; no broker operation may precede that commit."""
    conn.execute("""INSERT INTO qa_nats_rejections
        (rejection_id,stream_name,consumer_name,stream_sequence,subject_tag,
         subject_sha256,payload_sha256,payload_size,reason_code,max_delivery_count)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(rejection_id) DO UPDATE SET
            last_seen=now(), observations=qa_nats_rejections.observations+1,
            max_delivery_count=GREATEST(qa_nats_rejections.max_delivery_count,
                                       EXCLUDED.max_delivery_count)""",
        tuple(record[k] for k in ('rejection_id', 'stream_name', 'consumer_name',
              'stream_sequence', 'subject_tag', 'subject_sha256', 'payload_sha256',
              'payload_size', 'reason_code', 'max_delivery_count')))
    return record['rejection_id']


async def _retry(message, code):
    # Codes are constants chosen by this module, never exceptions or payloads.
    LOG.warning('qa_nats_retry reason=%s', code)
    try:
        await message.nak(delay=2)
    except Exception:
        # Leave unacknowledged if the connection is unavailable; do not TERM.
        LOG.warning('qa_nats_retry reason=nak_unavailable')
    return 'RETRY'


async def _reject(message, connect, reason):
    try:
        record = rejection_record(message, reason)
        with connect() as db:
            with db.transaction():
                rid = store_rejection(db, record)
        # Exiting BOTH contexts above proves the commit returned successfully.
    except Exception:
        return await _retry(message, 'rejection_not_persisted')
    LOG.warning('qa_nats_review_required rejection_id=%s reason=%s', rid, reason)
    try:
        await message.term()
    except Exception:
        # The audit record persists; a replay will update it idempotently.
        return await _retry(message, 'term_unavailable')
    return 'REJECTED_RECORDED'


async def handle_delivery(message, connect):
    """Commit valid inbox or invalid audit before broker ACK or TERM, respectively."""
    try:
        event = decode_event(message.data)
    except InvalidDelivery as exc:
        return await _reject(message, connect, str(exc))
    try:
        with connect() as db:
            with db.transaction():
                try:
                    inserted = accept_event(db, message.subject, event)
                except (ValueError, TypeError) as exc:
                    code = 'event_id_collision' if str(exc) == 'event id collision' else 'invalid_event'
                    raise InvalidDelivery(code) from None
    except InvalidDelivery as exc:
        return await _reject(message, connect, str(exc))
    except Exception:
        return await _retry(message, 'inbox_not_persisted')
    try:
        await message.ack()
    except Exception:
        return await _retry(message, 'ack_unavailable')
    return 'ACCEPTED' if inserted else 'DUPLICATE'
