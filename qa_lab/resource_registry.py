"""Offline, fail-closed qualification of external tool and dataset declarations.

This module never downloads, executes or approves external resources.
"""
from urllib.parse import urlsplit, urlunsplit
import re

_HEX = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}\\Z")
_LICENSE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,79}\\Z")


def _canonical_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise ValueError('unsafe URL')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError('malformed URL') from exc
    if parsed.scheme != 'https' or not host or parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError('URL must be HTTPS without credentials or fragment')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', host) or host.startswith('.') or host.endswith('.') or '..' in host:
        raise ValueError('invalid hostname')
    netloc = host.lower() + ((':' + str(port)) if port and port != 443 else '')
    return urlunsplit(('https', netloc, parsed.path or '/', parsed.query, ''))


def evaluate(resources):
    """Return deterministic advisory findings, never an approval to execute."""
    if not isinstance(resources, list) or len(resources) > 1000:
        raise ValueError('resources must be a list of at most 1000 entries')
    seen_ids, seen_urls, findings = set(), set(), []
    for position, item in enumerate(resources):
        errors = []
        if not isinstance(item, dict):
            findings.append({'index': position, 'status': 'REJECTED', 'reasons': ['INVALID_ENTRY']})
            continue
        name, url, license_id, revision = (item.get(k) for k in ('id', 'url', 'license', 'revision'))
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', name):
            errors.append('INVALID_ID')
        elif name.casefold() in seen_ids:
            errors.append('DUPLICATE_ID')
        else:
            seen_ids.add(name.casefold())
        try:
            normalized = _canonical_url(url)
            if normalized in seen_urls:
                errors.append('DUPLICATE_URL')
            else:
                seen_urls.add(normalized)
        except ValueError:
            errors.append('INVALID_URL')
        if not isinstance(license_id, str) or not _LICENSE.fullmatch(license_id) or license_id.upper() in {'UNKNOWN', 'NONE', 'TBD'}:
            errors.append('LICENSE_REVIEW_REQUIRED')
        if not isinstance(revision, str) or not _HEX.fullmatch(revision):
            errors.append('PINNED_REVISION_REQUIRED')
        if item.get('license_reviewed') is not True:
            errors.append('LICENSE_NOT_REVIEWED')
        if item.get('maintenance_reviewed') is not True:
            errors.append('MAINTENANCE_NOT_REVIEWED')
        findings.append({'index': position, 'id': name if isinstance(name, str) else None,
                         'status': 'DECLARATION_COMPLETE' if not errors else 'REJECTED', 'reasons': errors})
    return {'schema_version': 1, 'status': 'DECLARATIONS_COMPLETE' if all(f['status'] == 'DECLARATION_COMPLETE' for f in findings) else 'REVIEW_REQUIRED',
            'network_checked': False, 'license_legality_verified': False, 'execution_approved': False,
            'resources': findings}
