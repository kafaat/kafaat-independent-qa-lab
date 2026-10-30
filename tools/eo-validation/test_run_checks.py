"""Bounded runner regressions; mutations exist only in disposable package copies."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
import run_checks
from acceptance_contract import EXPECTED_INDEPENDENT_IDS, EXPECTED_UNIT_IDS

ROOT = Path(__file__).resolve().parent


class RunnerAcceptanceTests(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix='eo-runner-regression-')
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        self.assertNotIn(ROOT, directory.parents)
        package = directory / 'package'
        shutil.copytree(ROOT, package)
        return package, directory / 'result'

    def repin_mutated_file(self, package, relative):
        """Only deliberately changed negative-control copies are re-pinned."""
        path = package / 'manifest.json'
        data = json.loads(path.read_text())
        data['files'][relative] = hashlib.sha256((package / relative).read_bytes()).hexdigest()
        path.write_text(json.dumps(data, indent=2) + '\n')

    def invoke(self, package, output):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        result = subprocess.run(
            [sys.executable, '-B', str(package / 'run_checks.py'), '--output-dir', str(output)],
            env=environment, capture_output=True, text=True, timeout=20, check=False)
        if (output / 'result.json').exists():
            receipt = json.loads((output / 'result.json').read_text())
        else:
            receipt = json.loads(result.stdout)
        return result, receipt

    def independent_stub(self, package, *, variant=None, change_manifest=False):
        data = {
            'all_passed': True,
            'guard_sha256': hashlib.sha256((package / 'encoding_guard/guard.py').read_bytes()).hexdigest(),
            'cases': [{'case': name, 'passed': True} for name in sorted(EXPECTED_INDEPENDENT_IDS)],
        }
        if variant == 'false_rows':
            for row in data['cases']:
                row['passed'] = False
        elif variant == 'duplicate_ids':
            data['cases'][-1]['case'] = data['cases'][0]['case']
        elif variant == 'wrong_guard':
            data['guard_sha256'] = '0' * 64
        elif variant == 'unexpected_id':
            data['cases'][-1]['case'] = 'unreviewed-case'
        script = (
            'import argparse\nfrom pathlib import Path\n'
            'p=argparse.ArgumentParser();p.add_argument("--evidence-root",type=Path);'
            'p.add_argument("--output",type=Path);a=p.parse_args()\n'
            'a.output.write_text(' + repr(json.dumps(data)) + ')\n'
        )
        if change_manifest:
            script += 'm=a.evidence_root/"manifest.json";m.write_text(m.read_text()+"\\n")\n'
        target = package / 'encoding_guard_review/review.py'
        target.write_text(script)
        self.repin_mutated_file(package, 'encoding_guard_review/review.py')

    def test_complete_run_has_bound_observed_results(self):
        package, output = self.fixture()
        result, receipt = self.invoke(package, output)
        self.assertEqual(result.returncode, 0, (result.stderr, receipt))
        self.assertEqual(receipt['status'], 'PASSED_OFFLINE_RESEARCH_ONLY')
        self.assertEqual(receipt['unit_result']['tests_run'], len(EXPECTED_UNIT_IDS))
        self.assertEqual(len(receipt['independent_result']['cases']), len(EXPECTED_INDEPENDENT_IDS))
        self.assertTrue(receipt['source_unchanged'])
        self.assertEqual(receipt['source_before'], receipt['source_after'])
        self.assertEqual(receipt['temporary_files_remaining'], [])
        self.assertFalse(receipt['production_approved'])
        self.assertTrue(receipt['run_id'])
        self.assertLessEqual(receipt['started_at'], receipt['finished_at'])
        for stage in receipt['stages']:
            self.assertTrue(stage['result_validated'])
            self.assertTrue(Path(stage['cwd']).is_relative_to(output))
            for relative, expected in stage['log_sha256'].items():
                self.assertEqual(hashlib.sha256((output / relative).read_bytes()).hexdigest(), expected)

    def test_one_unit_test_does_not_satisfy_fixed_contract(self):
        package, output = self.fixture()
        (package / 'encoding_guard/test_guard.py').write_text(
            'import unittest\nclass Probe(unittest.TestCase):\n'
            '    def test_only_one(self):\n        self.assertTrue(True)\n')
        self.repin_mutated_file(package, 'encoding_guard/test_guard.py')
        result, receipt = self.invoke(package, output)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['status'], 'FAILED')
        self.assertEqual(len(receipt['unit_result']['collected_ids']), 1)
        self.assertFalse(receipt['unit_result']['collection_matches'])
        self.assertIsNone(receipt['independent_result'])

    def test_skipping_entire_unit_suite_fails(self):
        package, output = self.fixture()
        target = package / 'encoding_guard/test_guard.py'
        text = target.read_text().replace(
            'class GuardTests(unittest.TestCase):',
            '@unittest.skip("intentional runner negative control")\nclass GuardTests(unittest.TestCase):')
        target.write_text(text)
        self.repin_mutated_file(package, 'encoding_guard/test_guard.py')
        result, receipt = self.invoke(package, output)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['unit_result']['skips'], len(EXPECTED_UNIT_IDS))
        self.assertEqual(receipt['status'], 'FAILED')

    def test_unraisable_resource_warning_fails(self):
        package, output = self.fixture()
        target = package / 'encoding_guard/test_guard.py'
        target.write_text(target.read_text() + (
            '\nimport warnings\n'
            'class _ReviewResourceProbe:\n'
            '    def __del__(self):\n'
            '        warnings.warn("intentional runner resource probe", ResourceWarning)\n'
            '_resource_probe = _ReviewResourceProbe()\ndel _resource_probe\n'))
        self.repin_mutated_file(package, 'encoding_guard/test_guard.py')
        result, receipt = self.invoke(package, output)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(receipt['unit_result']['tests_run'], len(EXPECTED_UNIT_IDS))
        self.assertTrue(any(row['type'] == 'ResourceWarning'
                            for row in receipt['unit_result']['unraisable_errors']))

    def test_independent_aggregate_cannot_hide_invalid_rows_or_identity(self):
        for variant in ('false_rows', 'duplicate_ids', 'wrong_guard', 'unexpected_id'):
            with self.subTest(variant=variant):
                package, output = self.fixture()
                self.independent_stub(package, variant=variant)
                result, receipt = self.invoke(package, output)
                self.assertEqual(result.returncode, 1, receipt)
                self.assertEqual(receipt['status'], 'FAILED')
                self.assertEqual(receipt['stages'][-1]['exit_code'], 0)
                self.assertFalse(receipt['stages'][-1]['result_validated'])

    def test_omitted_guard_manifest_entry_blocks_before_output(self):
        package, output = self.fixture()
        path = package / 'manifest.json'
        data = json.loads(path.read_text())
        del data['files']['encoding_guard/guard.py']
        path.write_text(json.dumps(data))
        guard = package / 'encoding_guard/guard.py'
        guard.write_text(guard.read_text() + '\n# intentionally untracked negative-control change\n')
        result, receipt = self.invoke(package, output)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(receipt['status'], 'BLOCKED')
        self.assertFalse(output.exists())

    def test_unlisted_file_blocks_before_output(self):
        package, output = self.fixture()
        (package / 'unlisted.txt').write_text('intentional file coverage negative control\n')
        result, receipt = self.invoke(package, output)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(receipt['status'], 'BLOCKED')
        self.assertIn('Manifest file coverage mismatch', receipt['reasons'])
        self.assertFalse(output.exists())

    def test_manifest_bytes_cannot_change_during_run(self):
        package, output = self.fixture()
        self.independent_stub(package, change_manifest=True)
        result, receipt = self.invoke(package, output)
        self.assertEqual(result.returncode, 1)
        self.assertTrue(receipt['stages'][-1]['result_validated'])
        self.assertFalse(receipt['source_unchanged'])
        self.assertNotEqual(receipt['source_before']['manifest_sha256'],
                            receipt['source_after']['manifest_sha256'])

    def test_output_inside_package_is_rejected_before_writes(self):
        package, unused = self.fixture()
        output = package / 'forbidden-output'
        result, receipt = self.invoke(package, output)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(receipt['status'], 'BLOCKED')
        self.assertFalse(output.exists())

    def test_existing_output_is_preserved(self):
        package, output = self.fixture()
        output.mkdir()
        sentinel = output / 'keep.txt'
        sentinel.write_text('owned existing data\n')
        result, receipt = self.invoke(package, output)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(receipt['status'], 'BLOCKED')
        self.assertEqual(sentinel.read_text(), 'owned existing data\n')
        self.assertEqual({p.name for p in output.iterdir()}, {'keep.txt'})

    def test_timeout_preserves_partial_output(self):
        with tempfile.TemporaryDirectory(prefix='eo-timeout-review-') as temporary:
            output = Path(temporary)
            failure = subprocess.TimeoutExpired(
                ['owned-test'], 0.01, output=b'partial stdout\n', stderr=b'partial stderr\n')
            with patch.object(run_checks.subprocess, 'run', side_effect=failure):
                stage = run_checks.run_stage('unit', ['owned-test'], output, {}, output, timeout=0.01)
            self.assertEqual(stage['reason'], 'timeout')
            self.assertIsNone(stage['exit_code'])
            self.assertEqual((output / 'unit.stdout.log').read_text(), 'partial stdout\n')
            self.assertEqual((output / 'unit.stderr.log').read_text(), 'partial stderr\n')
            for relative, expected in stage['log_sha256'].items():
                self.assertEqual(hashlib.sha256((output / relative).read_bytes()).hexdigest(), expected)

    def test_nested_package_git_dirty_identity(self):
        with tempfile.TemporaryDirectory(prefix='eo-git-identity-review-') as temporary:
            repository = Path(temporary)
            package = repository / 'tools/eo-validation'
            package.mkdir(parents=True)
            tracked = package / 'tracked.py'
            tracked.write_text('VALUE = 1\n')
            commands = [
                ['git', 'init', '-q', str(repository)],
                ['git', '-C', str(repository), 'add', 'tools/eo-validation/tracked.py'],
                ['git', '-C', str(repository), '-c', 'user.name=EO Runner Tests',
                 '-c', 'user.email=eo-runner-tests@example.invalid',
                 'commit', '-q', '-m', 'Disposable identity regression baseline'],
            ]
            for command in commands:
                subprocess.run(command, capture_output=True, text=True, timeout=5, check=True)
            clean = run_checks.git_identity(package)
            self.assertTrue(clean['available'])
            self.assertFalse(clean['repository_dirty'])
            self.assertFalse(clean['package_dirty'])
            self.assertTrue(clean['tested_clean_commit'])
            tracked.write_text('VALUE = 2\n')
            changed = run_checks.git_identity(package)
            self.assertEqual(changed['checkout_commit'], clean['checkout_commit'])
            self.assertTrue(changed['repository_dirty'])
            self.assertTrue(changed['package_dirty'])
            self.assertFalse(changed['tested_clean_commit'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
