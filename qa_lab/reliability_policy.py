"""Pure fail-closed admission and retry policy for *read-only* probes.

Does not automatically execute, retry, or modify QA runs.
"""
from dataclasses import dataclass
from math import isfinite

@dataclass(frozen=True)
class Circuit:
    failures: int = 0
    open_until: float = 0.0

def decide(circuit, *, now, outcome=None, threshold=3, cooldown=30):
    """Return (new_state, admitted). Only an explicit success resets the breaker."""
    if not isinstance(circuit, Circuit) or not isinstance(now, (int, float)) or isinstance(now, bool) or not isfinite(now):
        raise ValueError('invalid state/time')
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1 or threshold > 100:
        raise ValueError('invalid threshold')
    if not isinstance(cooldown, (int, float)) or isinstance(cooldown, bool) or not isfinite(cooldown) or cooldown <= 0:
        raise ValueError('invalid cooldown')
    if outcome not in (None, 'success', 'failure'):
        raise ValueError('invalid outcome')
    if circuit.failures < 0 or not isfinite(circuit.open_until):
        raise ValueError('invalid circuit')
    if circuit.open_until > now:
        return circuit, False
    if outcome is None:
        return circuit, True
    if outcome == 'success':
        return Circuit(), True
    failures = circuit.failures + 1
    return Circuit(failures, now + cooldown if failures >= threshold else 0.0), failures < threshold

def retry_read_only(*, method, attempt, max_attempts=3, idempotency_proven=False):
    """Policy decision only; mutation requires independent reconciliation."""
    if not isinstance(method, str) or not isinstance(attempt, int) or isinstance(attempt, bool):
        raise ValueError('invalid retry request')
    if not 1 <= max_attempts <= 10 or not 1 <= attempt:
        raise ValueError('invalid retry limits')
    if method.upper() not in ('GET', 'HEAD') or method != method.upper():
        return {'retry': False, 'reason': 'MUTATION_REQUIRES_RECONCILIATION'}
    if attempt >= max_attempts:
        return {'retry': False, 'reason': 'BUDGET_EXHAUSTED'}
    return {'retry': True, 'reason': 'READ_ONLY_RETRY_PERMITTED'}
