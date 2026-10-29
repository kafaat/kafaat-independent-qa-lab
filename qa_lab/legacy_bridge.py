"""Read-only, bounded adapter for the imported Unified Local Lab CLI."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent / 'legacy' / 'unified-local-lab'
ALLOWED = {'catalog': ('catalog',), 'version': ('--version',)}

def probe(action='catalog', *, root=ROOT, timeout=15):
    if action not in ALLOWED:
        raise ValueError('unsupported legacy action')
    root = Path(root).resolve(strict=True)
    entry = root / 'bin' / 'local-lab'
    if not entry.is_file() or entry.is_symlink():
        raise ValueError('legacy entrypoint unavailable')
    env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
           'PYTHONPATH': str(root / 'src'), 'PYTHONDONTWRITEBYTECODE': '1'}
    try:
        result = subprocess.run([sys.executable, "-B", "-m", "unified_lab", *ALLOWED[action]], cwd=root, env=env,
                                capture_output=True, timeout=min(timeout, 30))
    except subprocess.TimeoutExpired:
        return {'action': action, 'status': 'TIMED_OUT', 'exit_code': None,
                'authority': 'diagnostic-only'}
    output = result.stdout[:65536]
    return {'action': action, 'status': 'PASSED' if result.returncode == 0 else 'FAILED',
            'exit_code': result.returncode, 'stdout_sha256': hashlib.sha256(output).hexdigest(),
            'stdout_tail': output[-2048:].decode('utf-8', errors='replace'),
            'stderr_tail': result.stderr[-1024:].decode('utf-8', errors='replace'),
            'authority': 'diagnostic-only'}
