"""Read-only estimated compute cost from stored QA run receipts.

Caller-supplied rates are estimates, not provider invoices or true resource usage.
"""
import math
from decimal import Decimal
from .cost_accounting import _amount, summarize


def estimate(lab, run_ids, *, usd_per_hour):
    if not isinstance(run_ids, list) or not 1 <= len(run_ids) <= 1000:
        raise ValueError('bounded nonempty run IDs required')
    rate = _amount(usd_per_hour)
    if len(set(run_ids)) != len(run_ids):
        raise ValueError('duplicate run IDs')
    items = []
    evidence = []
    for run_id in run_ids:
        if not isinstance(run_id, str) or not 1 <= len(run_id) <= 128:
            raise ValueError('invalid run ID')
        run = lab.get(run_id)
        receipt = run['receipt']
        if not receipt or not lab.verify(run_id) or receipt.get('run_id') != run_id:
            raise ValueError('missing or inconsistent receipt')
        if run['status'] != receipt.get('status') or run['status'] not in ('PASSED', 'FAILED', 'TIMED_OUT'):
            raise ValueError('terminal executed receipt required')
        start, finish = receipt.get('started_at'), receipt.get('finished_at')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, finish)) or finish < start:
            raise ValueError('invalid execution interval')
        seconds = Decimal(str(finish)) - Decimal(str(start))
        estimated = (seconds / Decimal(3600) * rate).quantize(Decimal('0.000001'))
        items.append({'event_id': run_id, 'category': 'compute', 'amount': str(estimated), 'currency': 'USD'})
        evidence.append({'run_id': run_id, 'receipt_sha256': receipt['sha256'],
                         'duration_seconds': str(seconds)})
    totals = summarize(items)
    return {'schema_version': 1, 'status': 'ESTIMATE_ONLY', 'pricing_basis': 'caller_supplied_usd_per_wall_hour',
            'usd_per_hour': str(rate), 'cost': totals, 'receipts': evidence,
            'compute_usage_measured': False, 'invoice_verified': False, 'production_accepted': False}
