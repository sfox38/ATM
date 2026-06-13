"""SQLite storage backend using the standard library.

Suitable for large deployments. Async access goes through the ProfileStore's
``a``-prefixed methods, which offload to a thread.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from custom_components.atm.mesa_core import backends


class SqliteBackend(backends.StorageBackend):
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS profiles (key TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def read(self, key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM profiles WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        data: dict[str, Any] = json.loads(row[0])
        return data

    def write(self, key: str, data: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO profiles (key, data) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET data = excluded.data",
                (key, json.dumps(data)),
            )

    def delete(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM profiles WHERE key = ?", (key,))

    def list_keys(self, prefix: str | None = None) -> list[str]:
        query = "SELECT key FROM profiles"
        params: tuple[Any, ...] = ()
        if prefix is not None:
            query += " WHERE key LIKE ?"
            params = (prefix.replace("%", r"\%").replace("_", r"\_") + "%",)
            query += r" ESCAPE '\'"
        query += " ORDER BY key"
        with self._connect() as conn:
            return [row[0] for row in conn.execute(query, params)]
