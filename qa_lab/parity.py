"""Fail-closed comparison of source-bound execution receipts.

VERIFIED means matching, internally consistent receipts for this exact case;
it is not a signature, independent host attestation, or production approval.
Legacy v1/unpinned cases remain NOT_RUN until their acceptance inputs are pinned.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

STATES = frozenset({'VERIFIED', 'PARTIAL', 'FAILED', 'NOT_RUN'})
_HEX = re.compile(r'[0-9a-f]{64}\Z')
_REF = re.compile(r'(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})\Z')
_REQUIRED = frozenset({
    'schema_version', 'case_id', 'role', 'run_id', 'source_ref',
    'source_tree_sha256', 'input_digest', 'output_digest', 'recipe_id',
    'status', 'exit_code', 'executed', 'source_unchanged',
    'started_at', 'finished_at', 'receipt_sha256', 'input', 'output',
})


def canonical(value: Any) -> bytes:
    """Versioned Python JSON encoding, not a claim of RFC 8785 conformance."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def content_digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def receipt_digest(receipt: Mapping[str, Any]) -> str:
    return content_digest({k: v for k, v in receipt.items() if k != 'receipt_sha256'})


def _digest(value: Any) -> bool:
    return isinstance(value, str) and bool(_HEX.fullmatch(value)) and value != '0' * 64


def _ref(value: Any) -> bool:
    return (isinstance(value, str) and bool(_REF.fullmatch(value))
            and set(value.removeprefix('sha256:')) != {'0'})


def _case_gaps(case: Mapping[str, Any]) -> list[str]:
    gaps = []
    if not _digest(case.get('input_digest')):
        gaps.append('case:input_digest_not_pinned')
    for role in ('legacy', 'new'):
        if not _ref(case.get(role + '_ref')):
            gaps.append(role + ':source_ref_not_pinned')
        if not _digest(case.get(role + '_tree_sha256')):
            gaps.append(role + ':source_tree_not_pinned')
        recipe = case.get(role + '_recipe')
        if not isinstance(recipe, str) or not recipe or len(recipe) > 128:
            gaps.append(role + ':recipe_not_pinned')
    if case.get('comparator') != 'exact-output-sha256-v1':
        gaps.append('case:comparator_not_pinned')
    return gaps


def _receipt_errors(receipt: Any, case: Mapping[str, Any], role: str) -> list[str]:
    errors = []
    if not isinstance(receipt, dict) or not _REQUIRED.issubset(receipt):
        return [role + ':incomplete_receipt']
    if receipt['schema_version'] != '2.0':
        errors.append('unsupported_schema')
    if receipt['case_id'] != case['id'] or receipt['role'] != role:
        errors.append('case_or_role_mismatch')
    try:
        rid = receipt['run_id']
        if not isinstance(rid, str) or str(uuid.UUID(rid)) != rid or uuid.UUID(rid).int == 0:
            raise ValueError('invalid run id')
    except (ValueError, TypeError, AttributeError):
        errors.append('invalid_run_id')
    for field in ('input_digest', 'output_digest', 'source_tree_sha256', 'receipt_sha256'):
        if not _digest(receipt.get(field)):
            errors.append('invalid_' + field)
    if not _ref(receipt.get('source_ref')) or receipt['source_ref'] != case[role + '_ref']:
        errors.append('source_ref_mismatch')
    if receipt['source_tree_sha256'] != case[role + '_tree_sha256']:
        errors.append('source_tree_mismatch')
    if receipt['recipe_id'] != case[role + '_recipe']:
        errors.append('recipe_mismatch')
    if receipt['input_digest'] != case['input_digest']:
        errors.append('input_mismatch')
    if receipt['executed'] is not True or receipt['source_unchanged'] is not True:
        errors.append('execution_or_source_not_proven')
    if receipt['status'] != 'PASSED' or type(receipt['exit_code']) is not int or receipt['exit_code'] != 0:
        errors.append('execution_not_passed')
    try:
        start = datetime.fromisoformat(receipt['started_at'])
        finish = datetime.fromisoformat(receipt['finished_at'])
        if start.utcoffset() is None or finish.utcoffset() is None or finish < start:
            raise ValueError('invalid execution interval')
    except (ValueError, TypeError, OverflowError):
        errors.append('invalid_execution_interval')
    try:
        if content_digest(receipt['input']) != receipt['input_digest']:
            errors.append('input_artifact_mismatch')
        if content_digest(receipt['output']) != receipt['output_digest']:
            errors.append('output_artifact_mismatch')
        if not hmac.compare_digest(receipt_digest(receipt), receipt['receipt_sha256']):
            errors.append('receipt_digest_mismatch')
    except (ValueError, TypeError, OverflowError, UnicodeError):
        errors.append('invalid_receipt_encoding')
    return [role + ':' + error for error in errors]


def evaluate(cases, receipts):
    """Keep the original call shape; fail closed on missing or stale evidence."""
    if not isinstance(cases, (list, tuple)) or not isinstance(receipts, Mapping):
        raise ValueError('cases must be a sequence and receipts a mapping')
    seen = set()
    for case in cases:
        if (not isinstance(case, dict) or not isinstance(case.get('id'), str)
                or not case['id'] or len(case['id']) > 128
                or not {'legacy_ref', 'new_ref'}.issubset(case)):
            raise ValueError('invalid parity case')
        if case['id'] in seen:
            raise ValueError('duplicate parity case')
        seen.add(case['id'])
    execution_counts = {}
    for case in cases:
        for role in ('legacy', 'new'):
            item = receipts.get((case['id'], role))
            if isinstance(item, dict) and isinstance(item.get('run_id'), str):
                rid = item['run_id']
                execution_counts[rid] = execution_counts.get(rid, 0) + 1
    results = []
    for case in cases:
        reasons = _case_gaps(case)
        old = receipts.get((case['id'], 'legacy'))
        new = receipts.get((case['id'], 'new'))
        if reasons:
            state = 'NOT_RUN'
        elif old is None or new is None:
            state = 'NOT_RUN'
            reasons = ['missing_' + role + '_receipt' for role, item in
                       (('legacy', old), ('new', new)) if item is None]
        else:
            reasons = _receipt_errors(old, case, 'legacy') + _receipt_errors(new, case, 'new')
            if isinstance(old, dict) and isinstance(new, dict) and old.get('run_id') == new.get('run_id'):
                reasons.append('reused_execution_identity')
            for item in (old, new):
                if isinstance(item, dict) and isinstance(item.get('run_id'), str) and execution_counts.get(item['run_id'], 0) > 1:
                    if 'execution_identity_reused' not in reasons:
                        reasons.append('execution_identity_reused')
            if reasons:
                state = 'FAILED'
            elif old['output_digest'] != new['output_digest']:
                state, reasons = 'PARTIAL', ['output_digest_mismatch']
            else:
                state = 'VERIFIED'
        results.append({
            'id': case['id'], 'status': state, 'legacy_ref': case['legacy_ref'],
            'new_ref': case['new_ref'], 'reasons': reasons,
            'evidence_scope': 'source-bound-receipt-consistency',
            'host_attested': False, 'production_accepted': False,
        })
    return results


def summary(rows):
    counts = {state: 0 for state in sorted(STATES)}
    for row in rows:
        if not isinstance(row, dict) or row.get('status') not in STATES:
            raise ValueError('invalid parity result')
        counts[row['status']] += 1
    return counts
