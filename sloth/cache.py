"""SQLite cache keyed by file content (sha256), so re-runs are instant
and renaming or moving a file doesn't invalidate it."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path


def sha256(path: Path) -> str:
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


class Cache:
    """Two kinds of entries: "extract" (text pulled from a file) and
    "classify" (the model's raw answer for that text)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            " kind TEXT NOT NULL, key TEXT NOT NULL, sha256 TEXT NOT NULL,"
            " result TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY (kind, key))"
        )
        self.db.commit()

    def get(self, kind: str, key: str) -> dict | None:
        row = self.db.execute("SELECT result FROM entries WHERE kind = ? AND key = ?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind: str, key: str, sha: str, result: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO entries (kind, key, sha256, result, created) VALUES (?, ?, ?, ?, ?)",
            (kind, key, sha, json.dumps(result), time.time()),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
