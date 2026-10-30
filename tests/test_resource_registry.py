import unittest
from qa_lab.resource_registry import evaluate

SHA = 'a' * 40


def resource(**changes):
    value = {'id': 'tool', 'url': 'https://example.org/repo', 'license': 'MIT', 'revision': SHA,
             'license_reviewed': True, 'maintenance_reviewed': True}
    value.update(changes)
    return value


class RegistryTests(unittest.TestCase):
    def test_complete_declaration_does_not_approve_execution(self):
        result = evaluate([resource()])
        self.assertEqual(result['status'], 'DECLARATIONS_COMPLETE')
        self.assertFalse(result['execution_approved'])
        self.assertFalse(result['network_checked'])

    def test_duplicates_are_case_insensitive_and_canonical(self):
        result = evaluate([resource(), resource(id='TOOL', url='https://EXAMPLE.org:443/repo')])
        self.assertIn('DUPLICATE_ID', result['resources'][1]['reasons'])
        self.assertIn('DUPLICATE_URL', result['resources'][1]['reasons'])

    def test_reject_unpinned_unreviewed_source(self):
        result = evaluate([resource(revision='main', license='UNKNOWN', license_reviewed=False, maintenance_reviewed=False)])
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertEqual(len(result['resources'][0]['reasons']), 4)

    def test_reject_credentials_http_fragment_and_control_characters(self):
        for url in ('http://example.org', 'https://user:pass@example.org', 'https://example.org/#frag', 'https://example.org/\nother'):
            with self.subTest(url=url):
                self.assertIn('INVALID_URL', evaluate([resource(url=url)])['resources'][0]['reasons'])

    def test_empty_catalog_is_not_qualified(self):
        result = evaluate([])
        self.assertEqual(result['status'], 'REVIEW_REQUIRED')
        self.assertFalse(result['execution_approved'])

    def test_unknown_fields_are_rejected(self):
        result = evaluate([resource(unexpected='ignore-me')])
        self.assertIn('UNKNOWN_FIELDS', result['resources'][0]['reasons'])

    def test_invalid_entries_fail_closed(self):
        self.assertEqual(evaluate([None])['status'], 'REVIEW_REQUIRED')
        with self.assertRaises(ValueError):
            evaluate({})


if __name__ == '__main__':
    unittest.main()
