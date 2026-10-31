"""Opt-in Linux/Docker acceptance of the root SQLite stack, using only owned resources."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('api', 'worker', 'evidence', 'triage')


def run(args, *, env=None, cwd=ROOT, timeout=240):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Never copy Docker environment, bearer credentials or raw logs to public evidence.
        raise RuntimeError('command_failed:' + args[0])
    return result.stdout.strip()


def identity():
    return {'commit': run(['git', 'rev-parse', 'HEAD']),
            'tree': run(['git', 'rev-parse', 'HEAD^{tree}']),
            'clean': not run(['git', 'status', '--porcelain', '--untracked-files=all'])}


def receipt_digest(receipt):
    data = {key: value for key, value in receipt.items() if key != 'sha256'}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def published_port(compose):
    """Ask Docker each time: an ephemeral host binding can change on restart."""
    value = compose('port', 'api', '8765')
    try:
        host, port = value.rsplit(':', 1)
        if host != '127.0.0.1' or not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ValueError('invalid binding')
        return int(port)
    except (ValueError, AttributeError):
        raise RuntimeError('expected exactly one localhost TCP binding') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT == output or ROOT in output.parents:
        raise SystemExit('Evidence output must be outside the source checkout')
    if os.environ.get('QA_ROOT_STACK_OPT_IN') != 'isolated-ci':
        raise SystemExit('Set QA_ROOT_STACK_OPT_IN=isolated-ci on an isolated Linux Docker host')
    if os.environ.get('DOCKER_HOST') or os.environ.get('DOCKER_CONTEXT'):
        raise SystemExit('Remote or explicitly selected Docker contexts are not accepted')
    before = identity()
    if not before['clean']:
        raise SystemExit('A clean committed checkout is required')
    endpoint = run(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'])
    if not endpoint.startswith('unix://'):
        raise SystemExit('Local Unix-socket Docker endpoint required')
    project = 'qa-root-' + uuid.uuid4().hex[:16]
    token = secrets.token_urlsafe(32)
    private = Path(tempfile.mkdtemp(prefix='qa-root-'))
    workspace = private / 'workspace'
    env = dict(os.environ, QA_API_TOKEN=token, QA_API_PORT='0', QA_WORKSPACE_PATH=str(workspace))
    command = ['docker', 'compose', '-p', project, '-f', str(ROOT / 'compose.yaml')]
    checks = {}
    proof = {'schema_version': '1', 'status': 'FAIL', 'scope': 'root-sqlite-compose-smoke-only',
             'source_before': before, 'checks': checks, 'production_accepted': False,
             'authority': 'execution-evidence-only',
             'run_id': os.environ.get('GITHUB_RUN_ID'), 'run_attempt': os.environ.get('GITHUB_RUN_ATTEMPT')}
    started = False
    stage = 'workspace'

    def compose(*args, timeout=240):
        return run(command + list(args), env=env, timeout=timeout)

    def require(name, condition):
        checks[name] = condition is True
        if not checks[name]:
            raise RuntimeError(name)

    def resources():
        label = 'label=com.docker.compose.project=' + project
        return {'containers': run(['docker', 'ps', '-aq', '--filter', label]),
                'volumes': run(['docker', 'volume', 'ls', '-q', '--filter', label]),
                'networks': run(['docker', 'network', 'ls', '-q', '--filter', label])}

    try:
        # Keep the enclosing workspace private, but source copied into a non-root
        # container must remain readable. Always restore the caller's umask.
        old = os.umask(0o022)
        try:
            run(['git', 'clone', '--quiet', '--no-hardlinks', str(ROOT), str(workspace)])
            run(['git', 'checkout', '--quiet', '--detach', before['commit']], cwd=workspace)
        finally:
            os.umask(old)
        require('no_preexisting_project_resources', not any(resources().values()))
        proof['docker_version'] = run(['docker', 'version', '--format', '{{.Server.Version}}'])
        proof['compose_version'] = compose('version', '--short')
        stage = 'config'
        compose('config', '--quiet')
        checks['compose_config_valid'] = True
        stage = 'build_and_start'
        started = True
        compose('up', '-d', '--build', '--wait', '--wait-timeout', '120', timeout=600)
        checks['fresh_volume_stack_started'] = True
        port = published_port(compose)
        require('api_published_on_loopback_only', True)
        proof['published_ports'] = [port]

        def request(path, payload=None, credential=token):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
            try:
                headers = {'Authorization': 'Bearer ' + credential} if credential else {}
                if payload is not None:
                    headers['Content-Type'] = 'application/json'
                connection.request('POST' if payload is not None else 'GET', path,
                                   json.dumps(payload) if payload is not None else None, headers)
                response = connection.getresponse()
                body = response.read(1048577)
                if len(body) > 1048576:
                    raise RuntimeError('response_too_large')
                return response.status, json.loads(body)
            finally:
                connection.close()

        def terminal(rid):
            for _ in range(60):
                status, value = request('/v1/runs/' + rid)
                if status == 200 and value['status'] not in ('PENDING', 'RUNNING'):
                    return value
                time.sleep(1)
            raise RuntimeError('terminal_run_timeout')

        stage = 'authentication'
        require('unauthenticated_submission_rejected', request('/v1/runs', {}, None)[0] == 401)
        require('wrong_token_read_rejected', request('/v1/runs/missing', credential='wrong')[0] == 401)
        stage = 'smoke'
        submitted = {'idempotency_key': uuid.uuid4().hex, 'project': 'isolated-root-smoke',
                     'source_sha': before['commit'], 'recipe': 'python-smoke'}
        code, value = request('/v1/runs', submitted)
        require('submission_accepted', code == 202 and value['reused'] is False)
        rid = value['run_id']
        result = terminal(rid)
        receipt = result['receipt']
        require('smoke_passed_with_expected_source', result['status'] == 'PASSED'
                and receipt['source_sha'] == before['commit'] and receipt['exit_code'] == 0
                and receipt['stdout_tail'].strip() == 'qa-smoke-ok')
        require('receipt_integrity_matches', receipt_digest(receipt) == receipt['sha256'])
        code, replay = request('/v1/runs', submitted)
        require('idempotency_replayed', code == 202 and replay == {'run_id': rid, 'reused': True})
        require('changed_payload_conflict_rejected',
                request('/v1/runs', {**submitted, 'project': 'changed'})[0] == 400)
        proof['run_id_smoke'] = rid
        proof['receipt_sha256'] = receipt['sha256']

        stage = 'reader_services'
        # Real HTTP on the Compose network. Tokens are read inside the container,
        # not interpolated into process arguments, logs or the public receipt.
        for service, service_port in (('evidence', 8766), ('triage', 8767)):
            script = ("import json,os,urllib.request; "
                      "r=urllib.request.Request('http://" + service + ':' + str(service_port)
                      + '/v1/' + service + '/' + rid + "',headers={'Authorization':'Bearer '+os.environ['QA_API_TOKEN']}); "
                      "print(urllib.request.urlopen(r,timeout=5).read().decode())")
            data = json.loads(compose('exec', '-T', 'api', 'python', '-c', script))
            require(service + '_http_read_verified',
                    data.get('integrity_verified') is True and data['receipt']['sha256'] == receipt['sha256']
                    if service == 'evidence' else data.get('advisory_only') is True
                    and data['result']['authority'] == 'advisory-only' and data['result']['status'] == 'PASSED')
            probe = """import errno,json,os,sqlite3
from contextlib import closing
from qa_lab.reader import ReadOnlyLab
reader=ReadOnlyLab('/data')
flags={'nonroot':os.getuid()==10001,'reader_ready':True}
try:
    with closing(reader.connect()) as db:
        db.execute('PRAGMA query_only=OFF')
        db.execute("UPDATE runs SET status='FAILED'")
except sqlite3.OperationalError as exc:
    flags['sqlite_write_rejected']=getattr(exc,'sqlite_errorcode',None)==sqlite3.SQLITE_READONLY
try:
    fd=os.open('/data/forbidden-write',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    os.close(fd)
except OSError as exc:
    flags['mount_write_rejected']=exc.errno==errno.EROFS
print(json.dumps(flags))
"""
            flags = json.loads(compose('exec', '-T', service, 'python', '-c', probe))
            require(service + '_readonly_mount_and_sql', flags == {
                'nonroot': True, 'reader_ready': True, 'sqlite_write_rejected': True, 'mount_write_rejected': True})
        stage = 'source_rejection'
        code, bad = request('/v1/runs', {**submitted, 'idempotency_key': uuid.uuid4().hex, 'source_sha': '0' * 40})
        blocked = terminal(bad['run_id'])
        require('wrong_source_blocked', code == 202 and blocked['status'] == 'BLOCKED'
                and blocked['receipt']['reason'] == 'checkout SHA mismatch or dirty tree')
        require('blocked_receipt_integrity_matches',
                receipt_digest(blocked['receipt']) == blocked['receipt']['sha256'])
        count_script = "import sqlite3; c=sqlite3.connect('file:/data/lab.sqlite3?mode=ro',uri=True); print(c.execute('SELECT count(*) FROM runs').fetchone()[0]); c.close()"
        require('exactly_two_runs_no_duplicate_effect', compose('exec', '-T', 'evidence', 'python', '-c', count_script) == '2')
        stage = 'restart'
        compose('restart', *SERVICES)
        port = published_port(compose)
        proof['published_ports'].append(port)
        require('api_republished_on_loopback_after_restart', True)
        for _ in range(60):
            try:
                if request('/healthz')[0] == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(1)
        else:
            raise RuntimeError('api_restart_readiness_timeout')
        code, restored = request('/v1/runs/' + rid)
        require('receipt_persisted_after_restart', code == 200 and restored == result)
        for service, service_port in (('evidence', 8766), ('triage', 8767)):
            health = "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:" + str(service_port) + "/healthz',timeout=5).status)"
            require(service + '_ready_after_restart', compose('exec', '-T', service, 'python', '-c', health) == '200')
        require('workspace_unchanged', not run(['git', 'status', '--porcelain', '--untracked-files=all'], cwd=workspace))
        proof['image_ids'] = sorted(set(compose('images', '-q').splitlines()))
        proof['status'] = 'PASS'
    except Exception as exc:
        proof['failure'] = {'stage': stage, 'type': type(exc).__name__}
    finally:
        try:
            if started:
                compose('down', '-v', '--remove-orphans', timeout=120)
            checks['owned_resources_removed'] = not any(resources().values())
        except Exception:
            checks['owned_resources_removed'] = False
        shutil.rmtree(private)
        checks['private_workspace_removed'] = not private.exists()
        proof['source_after'] = identity()
        checks['source_unchanged'] = proof['source_after'] == before
        if not all(checks.values()):
            proof['status'] = 'FAIL'
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(proof, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': proof['status'], 'checks_passed': sum(checks.values()),
                      'checks_total': len(checks), 'failure': proof.get('failure')}))
    return 0 if proof['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
