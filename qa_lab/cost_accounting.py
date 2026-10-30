"""Deterministic, caller-supplied cost accounting. No billing/provider authority."""
from decimal import Decimal, InvalidOperation

def _amount(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError('decimal cost required')
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid decimal') from exc
    if not number.is_finite() or number < 0 or number.as_tuple().exponent < -6 or number > Decimal('1000000000'):
        raise ValueError('invalid nonnegative bounded cost')
    return number

def summarize(items, *, currency='USD'):
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha() or not currency.isupper():
        raise ValueError('invalid currency')
    if not isinstance(items, list) or len(items) > 10000:
        raise ValueError('invalid cost list')
    totals = {}
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {'event_id', 'category', 'amount', 'currency'}:
            raise ValueError('invalid event')
        event_id, category = item['event_id'], item['category']
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128 or event_id in seen:
            raise ValueError('missing/duplicate event identity')
        seen.add(event_id)
        if not isinstance(category, str) or category not in ('compute', 'storage', 'network', 'model', 'other'):
            raise ValueError('invalid category')
        if item['currency'] != currency:
            raise ValueError('mixed currency unsupported')
        totals[category] = totals.get(category, Decimal(0)) + _amount(item['amount'])
    return {'currency': currency, 'by_category': {k: str(v) for k, v in sorted(totals.items())},
            'total': str(sum(totals.values(), Decimal(0))), 'event_count': len(items),
            'pricing_verified': False, 'billing_reconciled': False, 'production_accepted': False}
