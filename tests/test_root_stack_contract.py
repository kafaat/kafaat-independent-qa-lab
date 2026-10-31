"""Preflight/contract checks, not substitutes for a successful Docker build."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('root_stack_probe', ROOT / 'scripts/verify_root_stack.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class RootStackContractTests(unittest.TestCase):
    def test_opt_in_required_before_docker(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'receipt.json'
            env = dict(os.environ); env.pop('QA_ROOT_STACK_OPT_IN', None)
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify_root_stack.py'),
                                     '--output', str(path)], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('QA_ROOT_STACK_OPT_IN', result.stderr)
            self.assertFalse(path.exists())

    def test_source_output_rejected_before_docker(self):
        path = ROOT / 'never-create-receipt.json'
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify_root_stack.py'),
                                 '--output', str(path)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside the source', result.stderr)
        self.assertFalse(path.exists())

    def test_remote_docker_refused_before_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('DOCKER_HOST', 'DOCKER_CONTEXT'):
                env = dict(os.environ, QA_ROOT_STACK_OPT_IN='isolated-ci')
                env[name] = 'never-contact-this'
                result = subprocess.run([sys.executable, str(ROOT / 'scripts/verify_root_stack.py'),
                                         '--output', str(Path(tmp) / 'receipt.json')],
                                        env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Docker contexts', result.stderr)

    def test_receipt_digest_ignores_only_its_own_digest(self):
        value = {'status': 'PASSED', 'source_sha': 'a' * 40}
        initial = probe.receipt_digest(value)
        self.assertEqual(initial, probe.receipt_digest({**value, 'sha256': 'wrong'}))
        self.assertNotEqual(initial, probe.receipt_digest({**value, 'status': 'FAILED'}))

    def test_default_dockerfile_is_nonroot_and_copies_only_package(self):
        source = (ROOT / 'Dockerfile').read_text()
        self.assertIn('USER 10001:10001', source)
        self.assertIn('git config --system --add safe.directory /workspace', source)
        copies = [line for line in source.splitlines() if line.startswith('COPY ')]
        self.assertEqual(copies, ['COPY qa_lab /app/qa_lab'])

    def test_compose_keeps_reader_mounts_and_loopback(self):
        source = (ROOT / 'compose.yaml').read_text()
        self.assertEqual(source.count('condition: service_completed_successfully'), 4)
        self.assertEqual(source.count('- qa-data:/data:ro'), 2)
        self.assertIn('127.0.0.1:${QA_API_PORT:-8765}:8765', source)
        self.assertIn('create_host_path: false', source)
        self.assertIn('QA_REQUIRE_GIT_SHA: "1"', source)


    def test_only_api_has_edge_network(self):
        source = (ROOT / 'compose.yaml').read_text()
        api = source.split('  api:\n', 1)[1].split('  worker:\n', 1)[0]
        self.assertIn('networks: [qa-internal, qa-edge]', api)
        for name, next_name in (('worker', 'evidence'), ('evidence', 'triage')):
            block = source.split('  ' + name + ':\n', 1)[1].split('  ' + next_name + ':\n', 1)[0]
            self.assertIn('networks: [qa-internal]', block)
            self.assertNotIn('qa-edge', block)
        self.assertEqual(source.count('networks: [qa-internal, qa-edge]'), 1)
        self.assertIn('  qa-internal:\n    internal: true', source)


    def test_port_lookup_is_refreshed_after_restart(self):
        values = iter(('127.0.0.1:30101', '127.0.0.1:30202'))
        calls = []
        def compose(*args):
            calls.append(args)
            return next(values)
        self.assertEqual(probe.published_port(compose), 30101)
        self.assertEqual(probe.published_port(compose), 30202)
        self.assertEqual(calls, [('port', 'api', '8765')] * 2)

    def test_port_lookup_refuses_nonlocal_or_ambiguous_binding(self):
        for invalid in ('', '0.0.0.0:8765', '127.0.0.1:0', '127.0.0.1:65536',
                        '127.0.0.1:abc', '127.0.0.1:123\n127.0.0.1:124', '[::]:8765'):
            with self.subTest(binding=invalid), self.assertRaises(RuntimeError):
                probe.published_port(lambda *args: invalid)


if __name__ == '__main__':
    unittest.main()
