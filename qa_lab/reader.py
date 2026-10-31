"""Read an initialized SQLite lab without mkdir, chmod, DDL or write authority."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from .core import digest


class ReadOnlyLab:
    """A live read-only view, not an immutable snapshot or a database initializer."""

    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        self.db = self.root / "lab.sqlite3"
        if not self.root.is_dir() or self.db.is_symlink() or not self.db.is_file():
            raise FileNotFoundError("initialized lab database required")
        self.ready()

    def connect(self):
        # as_uri escapes '?', '#' and Unicode in filenames. Do not use immutable:
        # the API/worker may commit new evidence while this reader is running.
        db = sqlite3.connect(self.db.as_uri() + "?mode=ro", uri=True, timeout=10)
        try:
            db.execute("PRAGMA query_only=ON")
            db.execute("PRAGMA busy_timeout=10000")
            return db
        except BaseException:
            db.close()
            raise

    def ready(self):
        with closing(self.connect()) as db:
            db.execute("SELECT id,project,source_sha,recipe,status,receipt FROM runs LIMIT 0")

    def get(self, rid):
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT id,project,source_sha,recipe,status,receipt FROM runs WHERE id=?", (rid,)
            ).fetchone()
        if row is None:
            raise KeyError(rid)
        return {"run_id": row[0], "project": row[1], "source_sha": row[2],
                "recipe": row[3], "status": row[4],
                "receipt": json.loads(row[5]) if row[5] else None}

    def verify(self, rid):
        receipt = self.get(rid)["receipt"]
        return (isinstance(receipt, dict) and isinstance(receipt.get("sha256"), str)
                and digest({k: v for k, v in receipt.items() if k != "sha256"}) == receipt["sha256"])
