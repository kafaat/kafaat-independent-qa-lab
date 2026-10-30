"""Read-only audit of the stored HTTP acceptance result; no test re-execution."""
import hashlib
import json
import os
import re
from .http_acceptance import canonical

_HEX = re.compile(r'[0-9a-f]{64}\Z')


_SOURCE = re.compile(r'[0-9a-f]{40}\Z')


def audit_record(record, expected_sha, expected_deployment):
    if not isinstance(record, dict) or not isinstance(expected_sha, str) or not _SOURCE.fullmatch(expected_sha):
        raise ValueError('invalid receipt or expected SHA')
    if not isinstance(expected_deployment, str) or not expected_deployment:
        raise ValueError('deployment ID required')
    result = dict(record)
    digest = result.pop('receipt_sha256', None)
    checks = result.get('checks')
    identity = result.get('deployment_identity')
    reasons = []
    if not isinstance(digest, str) or not _HEX.fullmatch(digest) or hashlib.sha256(canonical(result).encode()).hexdigest() != digest:
        reasons.append('RECEIPT_DIGEST_MISMATCH')
    if result.get('requested_source_sha') != expected_sha:
        reasons.append('SOURCE_MISMATCH')
    if not isinstance(identity, dict) or identity.get('RAILWAY_DEPLOYMENT_ID') != expected_deployment:
        reasons.append('DEPLOYMENT_MISMATCH')
    if result.get('status') != 'PASSED':
        reasons.append('ACCEPTANCE_NOT_PASSED')
    if not isinstance(checks, list) or not checks or any(
            not isinstance(c, dict) or c.get('passed') is not True or not isinstance(c.get('name'), str)
            for c in checks):
        reasons.append('CHECKS_INCOMPLETE')
    return {'status': 'CONSISTENT' if not reasons else 'REJECTED',
            'reasons': reasons, 'check_count': len(checks) if isinstance(checks, list) else 0,
            'receipt_sha256': digest, 'source_sha': result.get('requested_source_sha'),
            'deployment_id': identity.get('RAILWAY_DEPLOYMENT_ID') if isinstance(identity, dict) else None,
            'source_attested': False, 'production_accepted': False}


def read_latest(dsn, expected_sha, expected_deployment):
    """Read only, bounded to five records; never print raw payload or secrets."""
    import psycopg
    with psycopg.connect(dsn, connect_timeout=5, options='-c statement_timeout=5000 default_transaction_read_only=on') as db:
        rows = db.execute('SELECT result FROM qa_acceptance_receipts ORDER BY updated_at DESC LIMIT 5').fetchall()
    for (record,) in rows:
        if isinstance(record, str):
            record = json.loads(record)
        if isinstance(record, dict) and isinstance(record.get('deployment_identity'), dict) and record['deployment_identity'].get('RAILWAY_DEPLOYMENT_ID') == expected_deployment:
            return audit_record(record, expected_sha, expected_deployment)
    return {'status': 'NOT_FOUND', 'reasons': ['NO_MATCHING_DEPLOYMENT_RECEIPT'], 'production_accepted': False}


def main():
    sha = os.environ['QA_EXPECTED_SOURCE_SHA']
    deployment = os.environ['QA_EXPECTED_DEPLOYMENT_ID']
    result = read_latest(os.environ['QA_POSTGRES_DSN'], sha, deployment)
    print(json.dumps(result, sort_keys=True, allow_nan=False), flush=True)
    if result['status'] != 'CONSISTENT':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
