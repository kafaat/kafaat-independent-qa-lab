import copy
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from qa_lab.parity import content_digest, receipt_digest
from qa_lab.parity_report import load, report


def evidence():
    case = {'id': 'sample', 'legacy_ref': 'a' * 40, 'new_ref': 'b' * 40,
            'legacy_tree_sha256': 'c' * 64, 'new_tree_sha256': 'd' * 64,
            'legacy_recipe': 'sample', 'new_recipe': 'sample', 'input_digest': content_digest({'x': 1}),
            'comparator': 'exact-output-sha256-v1'}
    receipts = []
    for role in ('legacy', 'new'):
        receipt = {'schema_version': '2.0', 'case_id': case['id'], 'role': role,
                   'run_id': str(uuid.uuid4()), 'source_ref': case[role + '_ref'],
                   'source_tree_sha256': case[role + '_tree_sha256'],
                   'input': {'x': 1}, 'input_digest': case['input_digest'], 'output': {'y': 2},
                   'output_digest': content_digest({'y': 2}), 'recipe_id': 'sample',
                   'executed': True, 'source_unchanged': True, 'status': 'PASSED', 'exit_code': 0,
                   'started_at': '2026-10-29T00:00:00+00:00', 'finished_at': '2026-10-29T00:00:01+00:00'}
        receipt['receipt_sha256'] = receipt_digest(receipt)
        receipts.append({'case_id': case['id'], 'role': role, 'receipt': receipt})
    return {'cases': [case], 'receipts': receipts}


class ParityReportTests(unittest.TestCase):
    def test_complete_receipts_consistency_only(self):
        result = report(evidence())
        self.assertEqual(result['counts']['VERIFIED'], 1)
        self.assertFalse(result['new_execution_performed'])
        self.assertFalse(result['full_capability_parity'])
        self.assertFalse(result['production_accepted'])
        saved = result.pop('report_sha256')
        self.assertEqual(saved, content_digest(result))
    def test_missing_receipt_not_run(self):
        item = evidence()
        item['receipts'].pop()
        self.assertEqual(report(item)['counts']['NOT_RUN'], 1)
    def test_status_only_is_failed(self):
        item = evidence()
        for receipt in item['receipts']:
            receipt['receipt'] = {'status': 'PASSED'}
        self.assertEqual(report(item)['counts']['FAILED'], 1)
    def test_duplicate_slot_rejected(self):
        item = evidence()
        item['receipts'].append(copy.deepcopy(item['receipts'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            report(item)
    def test_unrelated_receipt_rejected(self):
        item = evidence()
        item['receipts'][0]['case_id'] = 'not-requested'
        with self.assertRaises(ValueError):
            report(item)
    def test_changed_output_fails_even_with_matching_status(self):
        item = evidence()
        item['receipts'][0]['receipt']['output'] = {'y': 'forged'}
        self.assertEqual(report(item)['counts']['FAILED'], 1)
    def test_bounded_file_read_and_duplicate_json_key(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'evidence.json'
            p.write_text(json.dumps(evidence()))
            self.assertEqual(load(p)['counts']['VERIFIED'], 1)
            p.write_text('{"cases": [], "cases": [], "receipts": []}')
            with self.assertRaisesRegex(ValueError, 'duplicate JSON'):
                load(p)
    def test_empty_matrix_rejected(self):
        with self.assertRaises(ValueError):
            report({'cases': [], 'receipts': []})
    def test_unpinned_matrix_is_not_run(self):
        item = evidence()
        item['cases'][0]['new_ref'] = 'main'
        self.assertEqual(report(item)['counts']['NOT_RUN'], 1)
