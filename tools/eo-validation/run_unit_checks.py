"""Run the exact reviewed guard suite and emit observed results, including skips."""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import sys
import unittest
import warnings

sys.dont_write_bytecode = True
from acceptance_contract import EXPECTED_UNIT_IDS

ROOT = Path(__file__).resolve().parent


def test_ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_ids(item)
        else:
            yield item.id()


class ObservedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.executed_ids = []

    def startTest(self, test):
        self.executed_ids.append(test.id())
        super().startTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT in output.parents or output.exists():
        parser.error('Unit receipt must be a new file outside the package')
    warnings.simplefilter('error', ResourceWarning)
    unraisable = []
    sys.unraisablehook = lambda event: unraisable.append({
        'type': type(event.exc_value).__name__, 'message': str(event.exc_value)})
    sys.path.insert(0, str(ROOT / 'encoding_guard'))
    suite = unittest.defaultTestLoader.loadTestsFromName('test_guard')
    collected = list(test_ids(suite))
    collection_matches = (
        len(collected) == len(EXPECTED_UNIT_IDS)
        and len(set(collected)) == len(collected)
        and set(collected) == EXPECTED_UNIT_IDS
    )
    result = None
    if collection_matches:
        result = unittest.TextTestRunner(
            verbosity=2, resultclass=ObservedResult).run(suite)
    gc.collect()
    counts = {
        'tests_run': result.testsRun if result else 0,
        'failures': len(result.failures) if result else 0,
        'errors': len(result.errors) if result else 0,
        'skips': len(result.skipped) if result else 0,
        'expected_failures': len(result.expectedFailures) if result else 0,
        'unexpected_successes': len(result.unexpectedSuccesses) if result else 0,
    }
    passed = (collection_matches and result is not None and result.wasSuccessful()
              and counts['tests_run'] == len(EXPECTED_UNIT_IDS)
              and all(counts[key] == 0 for key in counts if key != 'tests_run')
              and not unraisable)
    data = {
        'schema': 'eo-unit-result-1', 'status': 'PASSED' if passed else 'FAILED',
        'expected_ids': sorted(EXPECTED_UNIT_IDS),
        'collected_ids': collected,
        'executed_ids': result.executed_ids if result else [],
        'collection_matches': collection_matches,
        **counts, 'unraisable_errors': unraisable,
        'source_sha256': {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ('encoding_guard/guard.py', 'encoding_guard/test_guard.py',
                         'run_unit_checks.py', 'acceptance_contract.py')
        },
    }
    with output.open('x', encoding='utf-8') as stream:
        json.dump(data, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps(data, sort_keys=True, allow_nan=False))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
