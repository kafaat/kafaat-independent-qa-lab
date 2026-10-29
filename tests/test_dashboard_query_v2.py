import contextlib
import unittest
import uuid
from qa_lab.pg_dashboard import ReadOnlyRuns, filters, render, valid_token


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        return self
    def fetchall(self):
        return self.rows
    def fetchone(self):
        return self.rows[0] if self.rows else None


class QueryTests(unittest.TestCase):
    def test_valid_filters(self):
        self.assertEqual(filters('status=FAILED&project=lab&limit=2')['limit'], 2)
    def test_invalid_filters(self):
        for query in ('limit=0', 'limit=101', 'limit=true', 'project=a&project=b', 'status=BAD', 'shell=x', 'after=bad'):
            with self.subTest(query=query), self.assertRaises(ValueError):
                filters(query)
    def test_sql_values_are_bound(self):
        attack = "x' OR 1=1 --"
        db = FakeDB([])
        queue = type('Q', (), {'_connect': lambda _: contextlib.nullcontext(db)})()
        ReadOnlyRuns(queue).list(filters('project=x%27%20OR%201%3D1%20--&limit=2'))
        sql, args = db.calls[-1]
        self.assertNotIn(attack, sql)
        self.assertIn(attack, args)
        self.assertEqual(db.calls[0][0], 'SET TRANSACTION READ ONLY')
    def test_keyset_pagination(self):
        ids = sorted((str(uuid.uuid4()) for _ in range(3)), reverse=True)
        db = FakeDB([(rid, 'PASSED', 'p', 'python-smoke') for rid in ids])
        queue = type('Q', (), {'_connect': lambda _: contextlib.nullcontext(db)})()
        result = ReadOnlyRuns(queue).list({'limit': 2})
        self.assertEqual(result['next_cursor'], ids[1])
        self.assertEqual(len(result['runs']), 2)
        self.assertEqual(result['ordering'], 'run-id-desc-not-chronological')
    def test_xss_escaped_in_project_and_form(self):
        row = {'run_id': str(uuid.uuid4()), 'status': 'PASSED', 'project': '<script>alert(1)</script>', 'recipe': 'x'}
        page = render({'runs': [row], 'next_cursor': None}, {'project': '\"><script>x</script>'})
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
    def test_placeholder_token_rejected(self):
        self.assertFalse(valid_token('${{secret(48, "abc")}}' * 2))
        self.assertFalse(valid_token('x' * 23))
        self.assertTrue(valid_token('x' * 32))
    def test_invalid_detail_id_is_rejected_before_db(self):
        with self.assertRaises(ValueError):
            ReadOnlyRuns(None).detail('not-a-run')
