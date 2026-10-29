"""Recompute a parity report from bounded JSON evidence; never execute source code."""
from __future__ import annotations

import json
from pathlib import Path
from .parity import content_digest, evaluate, summary


def report(bundle):
    if not isinstance(bundle, dict) or set(bundle) != {'cases', 'receipts'}:
        raise ValueError('expected cases and receipts')
    if not isinstance(bundle['cases'], list) or not 1 <= len(bundle['cases']) <= 1000:
        raise ValueError('bounded cases required')
    if not isinstance(bundle['receipts'], list) or len(bundle['receipts']) > 2000:
        raise ValueError('bounded receipt list required')
    receipts = {}
    case_ids = {case.get('id') for case in bundle['cases'] if isinstance(case, dict) and isinstance(case.get('id'), str)}
    for item in bundle['receipts']:
        if (not isinstance(item, dict) or set(item) != {'case_id', 'role', 'receipt'}
                or not isinstance(item['case_id'], str) or item['case_id'] not in case_ids
                or item['role'] not in ('legacy', 'new')):
            raise ValueError('invalid receipt binding')
        key = (item['case_id'], item['role'])
        if key in receipts:
            raise ValueError('duplicate receipt slot')
        receipts[key] = item['receipt']
    rows = evaluate(bundle['cases'], receipts)
    result = {'schema_version': '1.0', 'results': rows, 'counts': summary(rows),
              'input_sha256': content_digest(bundle), 'authority': 'receipt-consistency-only',
              'new_execution_performed': False, 'host_attested': False,
              'full_capability_parity': False, 'production_accepted': False}
    result['report_sha256'] = content_digest(result)
    return result


def load(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('regular evidence file required')
    with path.open('rb') as stream:
        data = stream.read(8 * 1024 * 1024 + 1)
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('evidence file too large')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return report(json.loads(data, object_pairs_hook=unique))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence_json')
    args = parser.parse_args()
    result = load(args.evidence_json)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    if result['counts']['FAILED'] or result['counts']['PARTIAL'] or result['counts']['NOT_RUN']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
