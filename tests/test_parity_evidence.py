"""Executable regressions for missing, forged, stale, and cross-case evidence."""
import copy
import unittest
import uuid
from qa_lab.parity import content_digest, evaluate, receipt_digest, summary


def fixture():
    case = {'id': 'pcm-integrity', 'legacy_ref': 'a'*40, 'new_ref': 'b'*40,
            'legacy_tree_sha256': 'c'*64, 'new_tree_sha256': 'd'*64,
            'legacy_recipe': 'pcm-v1', 'new_recipe': 'pcm-v1',
            'input_digest': content_digest({'input': 'same'}),
            'comparator': 'exact-output-sha256-v1'}
    receipts = {}
    for role in ('legacy', 'new'):
        r = {'schema_version': '2.0', 'case_id': case['id'], 'role': role,
             'run_id': str(uuid.uuid4()), 'source_ref': case[role+'_ref'],
             'source_tree_sha256': case[role+'_tree_sha256'],
             'input': {'input': 'same'}, 'output': {'valid': True},
             'input_digest': case['input_digest'], 'output_digest': content_digest({'valid': True}),
             'recipe_id': case[role+'_recipe'], 'status': 'PASSED', 'exit_code': 0,
             'executed': True, 'source_unchanged': True,
             'started_at': '2026-10-29T10:00:00+00:00', 'finished_at': '2026-10-29T10:00:01+00:00'}
        r['receipt_sha256'] = receipt_digest(r)
        receipts[(case['id'], role)] = r
    return case, receipts


class ParityEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.case, self.receipts = fixture()

    def state(self):
        return evaluate([self.case], self.receipts)[0]

    def corrupt(self, field, value, resign=True):
        r = self.receipts[(self.case['id'], 'new')]
        r[field] = value
        if resign:
            r['receipt_sha256'] = receipt_digest(r)
        self.assertEqual(self.state()['status'], 'FAILED')

    def test_valid_pair_is_verified_with_limited_authority(self):
        row = self.state()
        self.assertEqual(row['status'], 'VERIFIED')
        self.assertFalse(row['host_attested'])
        self.assertFalse(row['production_accepted'])

    def test_original_missing_digests_bug_rejected(self):
        self.receipts = {(self.case['id'], s): {'status': 'PASSED'} for s in ('legacy', 'new')}
        self.assertEqual(self.state()['status'], 'FAILED')

    def test_every_required_receipt_field_is_enforced(self):
        original = copy.deepcopy(self.receipts)
        for field in original[(self.case['id'], 'new')]:
            with self.subTest(field=field):
                self.receipts = copy.deepcopy(original)
                del self.receipts[(self.case['id'], 'new')][field]
                self.assertEqual(self.state()['status'], 'FAILED')

    def test_empty_receipt_is_failed_not_passed(self):
        self.receipts[(self.case['id'], 'new')] = {}
        self.assertEqual(self.state()['status'], 'FAILED')

    def test_missing_execution_is_not_run(self):
        del self.receipts[(self.case['id'], 'legacy')]
        self.assertEqual(self.state()['status'], 'NOT_RUN')

    def test_unpinned_case_is_not_run(self):
        self.case['new_ref'] = 'main'
        self.assertEqual(self.state()['status'], 'NOT_RUN')

    def test_old_schema_case_is_not_run(self):
        row = evaluate([{'id': 'old', 'legacy_ref': 'old', 'new_ref': 'new'}], {})[0]
        self.assertEqual(row['status'], 'NOT_RUN')

    def test_missing_case_input_is_not_run(self):
        del self.case['input_digest']
        self.assertEqual(self.state()['status'], 'NOT_RUN')

    def test_missing_tree_is_not_run(self):
        del self.case['new_tree_sha256']
        self.assertEqual(self.state()['status'], 'NOT_RUN')

    def test_missing_comparator_is_not_run(self):
        del self.case['comparator']
        self.assertEqual(self.state()['status'], 'NOT_RUN')

    def test_wrong_source_commit_rejected(self):
        self.corrupt('source_ref', 'e'*40)

    def test_wrong_tree_rejected(self):
        self.corrupt('source_tree_sha256', 'e'*64)

    def test_wrong_input_rejected(self):
        self.corrupt('input_digest', 'e'*64)

    def test_wrong_recipe_rejected(self):
        self.corrupt('recipe_id', 'other')

    def test_cross_case_replay_rejected(self):
        self.corrupt('case_id', 'different-case')

    def test_wrong_role_rejected(self):
        self.corrupt('role', 'legacy')

    def test_same_run_id_cannot_prove_two_executions(self):
        r = self.receipts[(self.case['id'], 'legacy')]
        self.corrupt('run_id', r['run_id'])

    def test_source_changed_rejected(self):
        self.corrupt('source_unchanged', False)

    def test_missing_execution_flag_rejected(self):
        self.corrupt('executed', False)

    def test_nonzero_exit_rejected(self):
        self.corrupt('exit_code', 1)

    def test_bool_exit_code_rejected(self):
        self.corrupt('exit_code', False)

    def test_failed_status_rejected(self):
        for status in ('FAILED', 'SKIPPED', 'BLOCKED', 'TIMED_OUT', 'PENDING'):
            with self.subTest(status=status):
                self.corrupt('status', status)

    def test_nonhex_and_zero_digests_rejected(self):
        for bad in ('x', 'g'*64, '0'*64, None, 3):
            with self.subTest(bad=bad):
                self.corrupt('output_digest', bad)

    def test_tampering_not_resigned_rejected(self):
        self.corrupt('output_digest', 'e'*64, resign=False)

    def test_signed_but_different_output_is_partial(self):
        r = self.receipts[(self.case['id'], 'new')]
        r['output'] = {'valid': False}
        r['output_digest'] = content_digest(r['output'])
        r['receipt_sha256'] = receipt_digest(r)
        self.assertEqual(self.state()['status'], 'PARTIAL')

    def test_invalid_run_identity_rejected(self):
        self.corrupt('run_id', 'not-a-uuid')

    def test_invalid_timestamps_rejected(self):
        self.corrupt('finished_at', '2026-10-29T09:00:00+00:00')

    def test_naive_timestamps_rejected(self):
        self.corrupt('finished_at', '2026-10-29T11:00:00')

    def test_nonfinite_envelope_is_rejected(self):
        r = self.receipts[(self.case['id'], 'new')]
        r['unsafe'] = float('nan')
        self.assertEqual(self.state()['status'], 'FAILED')

    def test_unknown_schema_is_rejected(self):
        self.corrupt('schema_version', '1.0')

    def test_archive_refs_supported(self):
        for role in ('legacy', 'new'):
            self.case[role+'_ref'] = 'sha256:'+self.case[role+'_tree_sha256']
            r = self.receipts[(self.case['id'], role)]
            r['source_ref'] = self.case[role+'_ref']
            r['receipt_sha256'] = receipt_digest(r)
        self.assertEqual(self.state()['status'], 'VERIFIED')

    def test_duplicate_cases_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            evaluate([self.case, self.case], self.receipts)

    def test_bad_case_and_receipt_collection_types(self):
        for cases, receipts in ((None, {}), ([{}], {}), ([self.case], [])):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                evaluate(cases, receipts)

    def test_output_artifact_digest_is_verified(self):
        self.corrupt('output', {'forged': True})

    def test_input_artifact_digest_is_verified(self):
        self.corrupt('input', {'different': True})

    def test_cross_case_execution_reuse_rejected(self):
        other = copy.deepcopy(self.case)
        other['id'] = 'another-case'
        receipts = copy.deepcopy(self.receipts)
        for role in ('legacy', 'new'):
            r = copy.deepcopy(self.receipts[(self.case['id'], role)])
            r['case_id'] = other['id']
            r['receipt_sha256'] = receipt_digest(r)
            receipts[(other['id'], role)] = r
        rows = evaluate([self.case, other], receipts)
        self.assertTrue(all(r['status'] == 'FAILED' for r in rows))

    def test_summary_rejects_unknown_status(self):
        with self.assertRaises(ValueError):
            summary([{'status': 'SUCCESS'}])
        self.assertEqual(summary([self.state()])['VERIFIED'], 1)
