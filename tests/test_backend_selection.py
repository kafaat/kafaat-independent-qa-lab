import unittest
from unittest.mock import patch
from qa_lab.backend import select_queue
from qa_lab.pg_queue import PostgresQueue

class BackendSelectionTests(unittest.TestCase):
    def test_postgres_selected_explicitly(self):
        q=select_queue(backend='postgres',dsn='postgresql://qa:example@localhost/qa')
        self.assertIsInstance(q,PostgresQueue)
    def test_postgres_missing_dsn_fails_closed(self):
        with patch.dict('os.environ',{'QA_POSTGRES_DSN':''}):
            with self.assertRaisesRegex(ValueError,'QA_POSTGRES_DSN required'):
                select_queue(backend='postgres')
    def test_unknown_backend_rejected(self):
        with self.assertRaisesRegex(ValueError,'unsupported queue backend'):
            select_queue(backend='unknown')
