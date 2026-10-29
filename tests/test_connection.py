import sqlite3
import tempfile
import unittest
from pathlib import Path
from qa_lab.connection import sqlite_transaction

class ConnectionTests(unittest.TestCase):
    def test_connection_closed_after_commit(self):
        with tempfile.TemporaryDirectory() as d:
            with sqlite_transaction(Path(d)/'test.db') as conn:
                conn.execute('CREATE TABLE t (id INTEGER)')
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute('SELECT 1')
    def test_connection_closed_after_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                with sqlite_transaction(Path(d)/'test.db') as conn:
                    conn.execute('CREATE TABLE t (id INTEGER)')
                    raise ValueError('rollback')
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute('SELECT 1')
