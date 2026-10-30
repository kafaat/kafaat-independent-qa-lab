"""Read-only progress audit; never upgrades parity claims or executes legacy source."""
from collections import Counter

def audit(progress):
    if not isinstance(progress, dict) or not isinstance(progress.get('profiles'), list):
        raise ValueError('profiles list required')
    profiles = progress['profiles']
    if not profiles or len(profiles) > 1000:
        raise ValueError('bounded nonempty profiles required')
    seen = set()
    counts = Counter()
    errors = []
    for idx, profile in enumerate(profiles):
        if not isinstance(profile, dict) or not isinstance(profile.get('id'), str) or not profile['id']:
            errors.append({'index': idx, 'reason': 'INVALID_PROFILE'})
            continue
        name = profile['id']
        if name in seen:
            errors.append({'index': idx, 'reason': 'DUPLICATE_PROFILE'})
        seen.add(name)
        local = profile.get('local_coverage')
        deployed = profile.get('deployed_parity')
        for scope, state in (('local', local), ('deployed', deployed)):
            if state not in ('VERIFIED', 'PARTIAL', 'FAILED', 'NOT_RUN'):
                errors.append({'index': idx, 'reason': 'INVALID_' + scope.upper() + '_STATE'})
            else:
                counts[scope + '_' + state] += 1
    return {'status': 'INVALID' if errors else 'INVENTORY_ONLY',
            'total': len(profiles), 'counts': dict(sorted(counts.items())), 'errors': errors,
            'new_execution_performed': False, 'full_capability_parity': False,
            'production_accepted': False}
