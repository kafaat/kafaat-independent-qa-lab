"""SQLite connection lifecycle utilities (explicit close)."""
import sqlite3
from contextlib import contextmanager

@contextmanager
def sqlite_transaction(path):
    connection = sqlite3.connect(path)
    try:
        with connection:
            yield connection
    finally:
        connection.close()
