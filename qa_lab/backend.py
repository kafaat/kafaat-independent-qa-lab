"""Explicit queue backend selection.

SQLite remains the local default. PostgreSQL is opt-in; no implicit
fallback to SQLite if the requested PostgreSQL connection is unavailable.
"""
import os
from .core import Lab

def select_queue(*, data_dir=None, backend=None, dsn=None):
    name=(backend or os.environ.get('QA_QUEUE_BACKEND','sqlite')).lower()
    if name=='sqlite':
        return Lab(data_dir or os.environ.get('QA_DATA_DIR','/data'))
    if name=='postgres':
        from .pg_queue import PostgresQueue
        url=dsn or os.environ.get('QA_POSTGRES_DSN')
        if not url:raise ValueError('QA_POSTGRES_DSN required for postgres backend')
        return PostgresQueue(url)
    raise ValueError('unsupported queue backend')
