"""Run the pinned offline EO checks with source-bound, fail-closed receipts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
from acceptance_contract import (
    EXPECTED_INDEPENDENT_IDS, EXPECTED_UNIT_IDS, REQUIRED_FILES,
)

ROOT = Path(__file__).resolve().parent
INDEPENDENT_BOOTSTRAP = '''import gc, json, runpy, sys
errors = []
sys.unraisablehook = lambda e: errors.append(type(e.exc_value).__name__)
sys.argv = sys.argv[1:]
code = 0
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
except SystemExit as exc:
    code = exc.code
gc.collect()
if errors:
    print(json.dumps({'unraisable_errors': errors}), file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(code)
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=unique)


def verify_manifest(root=ROOT):
    manifest = root / 'manifest.json'
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError('Missing or symlinked manifest')
    manifest_bytes = manifest.read_bytes()
    declaration = read_json(manifest)
    expected = declaration.get('files') if isinstance(declaration, dict) else None
    if not isinstance(expected, dict) or not REQUIRED_FILES.issubset(expected):
        raise ValueError('Manifest must include every required source file')
    actual_names = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Symlinked package entry: ' + str(path.relative_to(root)))
        if path.is_file():
            actual_names.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise ValueError('Nonregular package entry: ' + str(path.relative_to(root)))
    if set(expected) != actual_names - {'manifest.json'}:
        raise ValueError('Manifest file coverage mismatch')
    actual = {}
    for name, digest in expected.items():
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or '\\' in name
                or relative.as_posix() != name or name == 'manifest.json'
                or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)):
            raise ValueError('Invalid manifest entry: ' + name)
        path = root / name
        actual[name] = sha256(path.read_bytes())
        if actual[name] != digest:
            raise ValueError('Input hash mismatch: ' + name)
    if manifest.read_bytes() != manifest_bytes:
        raise ValueError('Manifest changed while verifying inputs')
    snapshot = {'files': actual, 'manifest_sha256': sha256(manifest_bytes)}
    snapshot['package_sha256'] = sha256(canonical(snapshot))
    return snapshot


def git_identity(root=ROOT):
    """HEAD identifies the checkout; dirty tested bytes are identified separately."""
    def git(*args):
        result = subprocess.run(['git', '-C', str(root), *args],
                                text=True, capture_output=True, timeout=5, check=False,
                                env=dict(os.environ, GIT_OPTIONAL_LOCKS='0'))
        if result.returncode:
            raise ValueError('git identity unavailable')
        return result.stdout.strip()
    try:
        top = Path(git('rev-parse', '--show-toplevel'))
        relative = root.relative_to(top).as_posix()
        status = git('status', '--porcelain=v1', '--untracked-files=all')
        package_status = git('status', '--porcelain=v1', '--untracked-files=all',
                             '--ignored=matching', '--', ':(top,literal)' + relative)
        return {
            'available': True, 'checkout_commit': git('rev-parse', 'HEAD'),
            'checkout_tree': git('rev-parse', 'HEAD^{tree}'),
            'repository_dirty': bool(status), 'package_dirty': bool(package_status),
            'status_sha256': sha256(status.encode()),
            'tested_clean_commit': not status and not package_status,
            'scope': 'Checkout identity; tested bytes are bound separately by package hashes',
        }
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'available': False, 'tested_clean_commit': False,
                'scope': 'No Git identity available; tested bytes bound by package hashes'}


def as_text(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')


def run_stage(name, command, cwd, environment, output, timeout=120):
    started = now()
    tick = time.monotonic()
    reason = None
    try:
        result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True,
                                text=True, timeout=timeout, check=False)
        stdout, stderr, code = result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code = as_text(exc.stdout), as_text(exc.stderr), None
        reason = 'timeout'
    except OSError as exc:
        stdout, stderr, code, reason = '', str(exc), None, 'launch_failed'
    artifacts = {}
    for suffix, content in (('.stdout.log', stdout), ('.stderr.log', stderr),
                            ('.log', stdout + stderr)):
        path = output / (name + suffix)
        path.write_text(content, encoding='utf-8')
        artifacts[path.name] = sha256(path.read_bytes())
    return {'stage': name, 'command': command, 'cwd': str(cwd),
            'started_at': started, 'finished_at': now(),
            'duration_seconds': time.monotonic() - tick, 'timeout_seconds': timeout,
            'exit_code': code, 'reason': reason, 'log_sha256': artifacts}


def validate_unit(data, before):
    if not isinstance(data, dict) or data.get('schema') != 'eo-unit-result-1':
        raise ValueError('Invalid unit result schema')
    for field in ('expected_ids', 'collected_ids', 'executed_ids'):
        ids = data.get(field)
        if (not isinstance(ids, list) or len(ids) != len(EXPECTED_UNIT_IDS)
                or any(not isinstance(value, str) for value in ids)
                or len(set(ids)) != len(ids) or set(ids) != EXPECTED_UNIT_IDS):
            raise ValueError('Unit test identity/count mismatch: ' + field)
    for name, expected in (('tests_run', len(EXPECTED_UNIT_IDS)), ('failures', 0),
                           ('errors', 0), ('skips', 0), ('expected_failures', 0),
                           ('unexpected_successes', 0)):
        if type(data.get(name)) is not int or data[name] != expected:
            raise ValueError('Unit result count mismatch: ' + name)
    if (data.get('status') != 'PASSED' or data.get('collection_matches') is not True
            or data.get('unraisable_errors') != []):
        raise ValueError('Unit execution did not fully pass')
    pinned = {name: before['files'][name] for name in (
        'encoding_guard/guard.py', 'encoding_guard/test_guard.py',
        'run_unit_checks.py', 'acceptance_contract.py')}
    if data.get('source_sha256') != pinned:
        raise ValueError('Unit source identity mismatch')


def validate_independent(data, before):
    if not isinstance(data, dict) or data.get('all_passed') is not True:
        raise ValueError('Independent checks did not fully pass')
    if data.get('guard_sha256') != before['files']['encoding_guard/guard.py']:
        raise ValueError('Independent guard identity mismatch')
    rows = data.get('cases')
    if (not isinstance(rows, list) or len(rows) != len(EXPECTED_INDEPENDENT_IDS)
            or any(not isinstance(row, dict) or not isinstance(row.get('case'), str)
                   or row.get('passed') is not True for row in rows)):
        raise ValueError('Independent case results/count mismatch')
    names = [row['case'] for row in rows]
    if len(set(names)) != len(names) or set(names) != EXPECTED_INDEPENDENT_IDS:
        raise ValueError('Independent case identity mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='New output directory outside this package')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    started = now()
    tick = time.monotonic()
    receipt = {
        'schema': 'eo-offline-run-2', 'run_id': str(uuid.uuid4()),
        'started_at': started, 'status': 'BLOCKED', 'production_approved': False,
        'invocation': {'interpreter': sys.executable, 'argv': sys.argv},
        'environment': {'python': platform.python_version(), 'platform': platform.platform(),
                        'numpy': None},
        'ci_context': {
            'scope': 'Declared CI environment metadata; not host attestation',
            'values': {name: os.environ[name] for name in (
                'GITHUB_REPOSITORY', 'GITHUB_SHA', 'GITHUB_REF',
                'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT', 'GITHUB_SERVER_URL')
                if name in os.environ},
        },
        'expected_unit_ids': sorted(EXPECTED_UNIT_IDS),
        'expected_independent_ids': sorted(EXPECTED_INDEPENDENT_IDS),
        'stages': [], 'reasons': [],
        'scope': 'Offline research: three archived public satellite windows and adversarial fixtures; no network/API/PostGIS benchmark',
    }
    try:
        if output == ROOT or ROOT in output.parents:
            raise ValueError('Output directory must be outside the package')
        before = verify_manifest()
        receipt['source_before'] = before
        import numpy
        receipt['environment']['numpy'] = numpy.__version__
        receipt['environment']['numpy_module_path'] = str(numpy.__file__)
        if sys.version_info[:2] != (3, 12) or numpy.__version__ != '2.5.4':
            raise ValueError('Use Python 3.12 and the tested dependency numpy==2.5.4')
        receipt['git_before'] = git_identity()
        output.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError, ImportError, KeyError, TypeError) as exc:
        receipt['reasons'].append(str(exc))
        receipt['finished_at'] = now()
        receipt['duration_seconds'] = time.monotonic() - tick
        print(json.dumps(receipt, sort_keys=True, allow_nan=False))
        return 2
    temp = output / 'temporary'
    temp.mkdir()
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1',
                       PYTHONHASHSEED='0', TMPDIR=str(temp))
    receipt['environment']['subprocess_overrides'] = {
        name: environment[name] for name in (
            'PYTHONDONTWRITEBYTECODE', 'PYTHONNOUSERSITE', 'PYTHONHASHSEED', 'TMPDIR')}
    stages = [
        ('unit', [sys.executable, '-B', '-W', 'error::ResourceWarning',
                  str(ROOT / 'run_unit_checks.py'), '--output', str(output / 'unit.json')]),
        ('independent', [sys.executable, '-B', '-W', 'error::ResourceWarning', '-c',
                         INDEPENDENT_BOOTSTRAP, str(ROOT / 'encoding_guard_review/review.py'),
                         '--evidence-root', str(ROOT), '--output', str(output / 'independent.json')]),
    ]
    for name, command in stages:
        cwd = output / ('work-' + name)
        cwd.mkdir()
        stage = run_stage(name, command, cwd, environment, output)
        receipt['stages'].append(stage)
        if stage['exit_code'] != 0:
            receipt['reasons'].append(name + ': execution did not pass')
            break
        try:
            data = read_json(output / (name + '.json'))
            if name == 'unit':
                validate_unit(data, before)
            else:
                validate_independent(data, before)
            stage['result_validated'] = True
        except (OSError, ValueError, KeyError, TypeError) as exc:
            stage['result_validated'] = False
            receipt['reasons'].append(name + ': ' + str(exc))
            break
    for name in ('unit', 'independent'):
        try:
            receipt[name + '_result'] = read_json(output / (name + '.json'))
        except (OSError, ValueError):
            receipt[name + '_result'] = None
    try:
        receipt['source_after'] = verify_manifest()
        receipt['source_unchanged'] = before == receipt['source_after']
        if not receipt['source_unchanged']:
            receipt['reasons'].append('Package or manifest changed during execution')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        receipt['source_unchanged'] = False
        receipt['reasons'].append('Post-run integrity failed: ' + str(exc))
    receipt['git_after'] = git_identity()
    receipt['temporary_files_remaining'] = sorted(
        p.relative_to(temp).as_posix() for p in temp.rglob('*') if p.is_file())
    if receipt['temporary_files_remaining']:
        receipt['reasons'].append('Temporary fixture cleanup incomplete')
    passed = (len(receipt['stages']) == 2 and not receipt['reasons']
              and all(stage.get('result_validated') is True for stage in receipt['stages']))
    receipt['status'] = 'PASSED_OFFLINE_RESEARCH_ONLY' if passed else 'FAILED'
    receipt['finished_at'] = now()
    receipt['duration_seconds'] = time.monotonic() - tick
    receipt['artifact_sha256'] = {
        p.name: sha256(p.read_bytes()) for p in output.iterdir() if p.is_file()}
    (output / 'result.json').write_text(json.dumps(receipt, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': receipt['status'], 'run_id': receipt['run_id'],
                      'output_dir': str(output), 'reasons': receipt['reasons']}))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
