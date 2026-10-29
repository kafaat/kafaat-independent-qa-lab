"""Behavioural commit-before-TERM tests; fake DB/broker, real consumer handler."""
import asyncio
import copy
import json
import unittest
import uuid
from types import SimpleNamespace

from qa_lab.nats_rejections import (
    MAX_EVENT_BYTES, decode_event, handle_delivery, rejection_record,
)

SECRET = 'private-payload-and-credential-sentinel'


class Store:
    def __init__(self):
        self.inbox = {}
        self.rejections = {}
        self.trace = []
        self.fault = None

    def connect(self):
        if self.fault == 'connect':
            raise OSError(SECRET)
        return Connection(self)


class Transaction:
    def __enter__(self): return self
    def __exit__(self, *args): return False


class Connection:
    def __init__(self, store):
        self.store = store
        self.inbox = copy.deepcopy(store.inbox)
        self.rejections = copy.deepcopy(store.rejections)
        self.kind = None
        self.row = None

    def __enter__(self): return self

    def __exit__(self, typ, value, tb):
        if typ is not None:
            self.store.trace.append('rollback')
            return False
        if self.store.fault == 'commit_' + str(self.kind):
            raise OSError(SECRET)
        self.store.inbox = self.inbox
        self.store.rejections = self.rejections
        self.store.trace.append('commit_' + str(self.kind))
        return False

    def transaction(self): return Transaction()

    def execute(self, sql, args):
        if sql.startswith('INSERT INTO qa_inbox'):
            self.kind = 'inbox'
            if self.store.fault == 'inbox_write':
                raise OSError(SECRET)
            eid, subject, payload = args
            self.row = None if eid in self.inbox else (eid,)
            if self.row:
                self.inbox[eid] = (subject, json.loads(payload))
        elif sql.startswith('SELECT subject,payload FROM qa_inbox'):
            self.row = self.inbox.get(args[0])
        elif sql.startswith('INSERT INTO qa_nats_rejections'):
            self.kind = 'audit'
            if self.store.fault == 'audit_write':
                raise OSError(SECRET)
            fields = ('rejection_id', 'stream_name', 'consumer_name', 'stream_sequence',
                      'subject_tag', 'subject_sha256', 'payload_sha256', 'payload_size',
                      'reason_code', 'max_delivery_count')
            record = dict(zip(fields, args))
            previous = self.rejections.get(record['rejection_id'])
            record['observations'] = previous['observations'] + 1 if previous else 1
            record['review_state'] = 'REVIEW_REQUIRED'
            self.rejections[record['rejection_id']] = record
            self.store.trace.append('audit_insert')
        else:
            raise AssertionError('unhandled SQL')
        return self

    def fetchone(self): return self.row


class Message:
    def __init__(self, store, data=None, *, subject='qa.jobs.created', sequence=1):
        self.store = store
        self.data = data if data is not None else json.dumps({
            'event_id': str(uuid.uuid4()), 'payload': {'run_id': 'r'}}).encode()
        self.subject = subject
        self.metadata = SimpleNamespace(stream='QA_JOBS', consumer='qa-audit-inbox',
                                        sequence=SimpleNamespace(stream=sequence), num_delivered=1)
        self.actions = []
        self.fault = None

    async def ack(self):
        self.store.trace.append('ack')
        self.actions.append('ack')
        if self.fault == 'cancel': raise asyncio.CancelledError()
        if self.fault == 'ack': raise OSError(SECRET)

    async def term(self):
        # The invalid delivery must already be visible in committed state.
        assert self.store.rejections, 'TERM before durable rejection'
        self.store.trace.append('term')
        self.actions.append('term')
        if self.fault == 'term': raise OSError(SECRET)

    async def nak(self, *, delay):
        assert delay == 2
        self.actions.append('nak')
        if self.fault == 'nak': raise OSError(SECRET)


class RejectionAuditTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self): self.store = Store()

    async def deliver(self, msg):
        return await handle_delivery(msg, self.store.connect)

    async def test_valid_event_ack_after_commit(self):
        msg = Message(self.store)
        self.assertEqual(await self.deliver(msg), 'ACCEPTED')
        self.assertEqual(self.store.trace, ['commit_inbox', 'ack'])
        self.assertFalse(self.store.rejections)

    async def test_valid_duplicate_preserves_inbox(self):
        msg = Message(self.store)
        await self.deliver(msg)
        self.assertEqual(await self.deliver(msg), 'DUPLICATE')
        self.assertEqual(len(self.store.inbox), 1)
        self.assertFalse(self.store.rejections)

    async def test_collision_recorded_before_term(self):
        msg = Message(self.store)
        await self.deliver(msg)
        event = json.loads(msg.data)
        event['payload']['run_id'] = 'changed'
        collision = Message(self.store, json.dumps(event).encode(), sequence=2)
        self.assertEqual(await self.deliver(collision), 'REJECTED_RECORDED')
        record = next(iter(self.store.rejections.values()))
        self.assertEqual(record['reason_code'], 'event_id_collision')
        self.assertEqual(list(self.store.inbox.values())[0][1]['run_id'], 'r')
        self.assertLess(self.store.trace.index('commit_audit'), self.store.trace.index('term'))
        self.assertEqual(collision.actions, ['term'])

    async def test_invalid_json_is_recorded(self):
        msg = Message(self.store, b'{invalid')
        self.assertEqual(await self.deliver(msg), 'REJECTED_RECORDED')
        self.assertFalse(self.store.inbox)
        self.assertEqual(next(iter(self.store.rejections.values()))['reason_code'], 'invalid_json')

    async def test_invalid_utf8_is_recorded(self):
        msg = Message(self.store, b'\xff')
        self.assertEqual(await self.deliver(msg), 'REJECTED_RECORDED')

    async def test_duplicate_json_keys_are_rejected(self):
        msg = Message(self.store, b'{"event_id":"a","event_id":"b","payload":{}}')
        self.assertEqual(await self.deliver(msg), 'REJECTED_RECORDED')
        self.assertFalse(self.store.inbox)

    async def test_nonfinite_json_is_rejected(self):
        for value in (b'NaN', b'Infinity', b'-Infinity'):
            with self.subTest(value=value):
                msg = Message(self.store, b'{"payload":' + value + b'}')
                self.assertEqual(await self.deliver(msg), 'REJECTED_RECORDED')

    async def test_deep_json_is_rejected(self):
        msg = Message(self.store, b'[' * 2000 + b']' * 2000)
        self.assertEqual(await self.deliver(msg), 'REJECTED_RECORDED')

    async def test_invalid_envelope_is_recorded(self):
        msg = Message(self.store, b'[]')
        self.assertEqual(await self.deliver(msg), 'REJECTED_RECORDED')
        self.assertEqual(next(iter(self.store.rejections.values()))['reason_code'], 'invalid_event')

    async def test_oversized_envelope_is_fingerprinted_without_parsing(self):
        msg = Message(self.store, b'x' * (MAX_EVENT_BYTES + 1))
        self.assertEqual(await self.deliver(msg), 'REJECTED_RECORDED')
        record = next(iter(self.store.rejections.values()))
        self.assertEqual(record['payload_size'], MAX_EVENT_BYTES + 1)
        self.assertEqual(record['reason_code'], 'oversized_event')

    async def test_audit_write_failure_never_terms(self):
        self.store.fault = 'audit_write'
        msg = Message(self.store, b'bad')
        self.assertEqual(await self.deliver(msg), 'RETRY')
        self.assertEqual(msg.actions, ['nak'])
        self.assertFalse(self.store.rejections)

    async def test_audit_commit_failure_never_terms(self):
        self.store.fault = 'commit_audit'
        msg = Message(self.store, b'bad')
        self.assertEqual(await self.deliver(msg), 'RETRY')
        self.assertEqual(msg.actions, ['nak'])
        self.assertFalse(self.store.rejections)

    async def test_connect_failure_never_terms(self):
        self.store.fault = 'connect'
        msg = Message(self.store, b'bad')
        self.assertEqual(await self.deliver(msg), 'RETRY')
        self.assertEqual(msg.actions, ['nak'])

    async def test_transient_inbox_error_is_not_invalid_evidence(self):
        self.store.fault = 'inbox_write'
        msg = Message(self.store)
        self.assertEqual(await self.deliver(msg), 'RETRY')
        self.assertEqual(msg.actions, ['nak'])
        self.assertFalse(self.store.rejections)

    async def test_inbox_commit_failure_never_acks(self):
        self.store.fault = 'commit_inbox'
        msg = Message(self.store)
        self.assertEqual(await self.deliver(msg), 'RETRY')
        self.assertEqual(msg.actions, ['nak'])
        self.assertFalse(self.store.inbox)

    async def test_term_failure_keeps_durable_record(self):
        msg = Message(self.store, b'bad'); msg.fault = 'term'
        self.assertEqual(await self.deliver(msg), 'RETRY')
        self.assertTrue(self.store.rejections)
        self.assertEqual(msg.actions, ['term', 'nak'])

    async def test_ack_failure_keeps_inbox_for_safe_replay(self):
        msg = Message(self.store); msg.fault = 'ack'
        self.assertEqual(await self.deliver(msg), 'RETRY')
        self.assertTrue(self.store.inbox)
        self.assertFalse(self.store.rejections)

    async def test_nak_failure_is_logged_not_term(self):
        msg = Message(self.store, b'bad'); msg.fault = 'nak'
        self.store.fault = 'connect'
        self.assertEqual(await self.deliver(msg), 'RETRY')
        self.assertEqual(msg.actions, ['nak'])

    async def test_redelivery_coalesces_metadata_record(self):
        msg = Message(self.store, b'bad')
        await self.deliver(msg)
        msg.metadata.num_delivered = 2
        await self.deliver(msg)
        self.assertEqual(len(self.store.rejections), 1)
        record = next(iter(self.store.rejections.values()))
        self.assertEqual(record['observations'], 2)
        self.assertEqual(record['max_delivery_count'], 2)

    async def test_distinct_sequences_are_distinct_review_items(self):
        for sequence in (1, 2):
            await self.deliver(Message(self.store, b'bad', sequence=sequence))
        self.assertEqual(len(self.store.rejections), 2)

    async def test_changed_bytes_same_sequence_cannot_replace_review(self):
        for raw in (b'bad-one', b'bad-two'):
            await self.deliver(Message(self.store, raw))
        self.assertEqual(len(self.store.rejections), 2)

    async def test_missing_metadata_causes_retry(self):
        msg = Message(self.store, b'bad'); msg.metadata = None
        self.assertEqual(await self.deliver(msg), 'RETRY')
        self.assertEqual(msg.actions, ['nak'])

    async def test_sensitive_data_not_persisted_or_logged(self):
        msg = Message(self.store, SECRET.encode(), subject='private.' + SECRET)
        with self.assertLogs('qa_lab.nats_rejections', level='WARNING') as logs:
            await self.deliver(msg)
        self.assertNotIn(SECRET, json.dumps(self.store.rejections))
        self.assertNotIn(SECRET, '\n'.join(logs.output))
        self.assertEqual(next(iter(self.store.rejections.values()))['subject_tag'], 'other')

    async def test_exception_text_is_never_logged(self):
        self.store.fault = 'connect'
        with self.assertLogs('qa_lab.nats_rejections', level='WARNING') as logs:
            await self.deliver(Message(self.store, b'bad'))
        self.assertNotIn(SECRET, '\n'.join(logs.output))

    async def test_cancellation_propagates(self):
        msg = Message(self.store); msg.fault = 'cancel'
        with self.assertRaises(asyncio.CancelledError):
            await self.deliver(msg)
        self.assertEqual(msg.actions, ['ack'])


class RejectionRecordTests(unittest.TestCase):
    def test_bad_metadata_and_reason_are_rejected(self):
        msg = Message(Store(), b'bad')
        for value in (0, -1, True, '1', 2**63):
            with self.subTest(value=value):
                msg.metadata.sequence.stream = value
                with self.assertRaises(ValueError): rejection_record(msg, 'invalid_json')
        with self.assertRaises(ValueError): rejection_record(msg, SECRET)

    def test_raw_representation_must_be_bytes(self):
        with self.assertRaises(ValueError): decode_event('{}')

    def test_delivery_counter_not_part_of_stable_key(self):
        msg = Message(Store(), b'bad')
        first = rejection_record(msg, 'invalid_json')
        msg.metadata.num_delivered = 3
        second = rejection_record(msg, 'invalid_json')
        self.assertEqual(first['rejection_id'], second['rejection_id'])

    def test_hashes_have_full_length(self):
        record = rejection_record(Message(Store(), b'bad'), 'invalid_json')
        for key in ('rejection_id', 'subject_sha256', 'payload_sha256'):
            self.assertRegex(record[key], '^[0-9a-f]{64}$')


if __name__ == '__main__': unittest.main()
