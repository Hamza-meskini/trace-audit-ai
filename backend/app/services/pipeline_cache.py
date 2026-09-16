"""Small versioned cache for deterministic, expensive pipeline operations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import settings


def stable_key(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    root = Path(settings.UPLOAD_DIR)
    root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(root / ".pipeline-cache.sqlite3", timeout=10)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS entries ("
            "namespace TEXT NOT NULL, cache_key TEXT NOT NULL, payload TEXT NOT NULL, "
            "created_at TEXT NOT NULL, PRIMARY KEY(namespace, cache_key))"
        )
        yield connection
    finally:
        connection.close()


def get_json(namespace: str, cache_key: str) -> Any | None:
    with _connect() as connection:
        row = connection.execute(
            "SELECT payload FROM entries WHERE namespace=? AND cache_key=?",
            (namespace, cache_key),
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None


def set_json(namespace: str, cache_key: str, payload: Any) -> None:
    created_at = datetime.now(timezone.utc).isoformat()
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str)
    with _connect() as connection, connection:
        connection.execute(
            "INSERT OR REPLACE INTO entries(namespace, cache_key, payload, created_at) VALUES (?, ?, ?, ?)",
            (namespace, cache_key, encoded, created_at),
        )
        max_entries = max(100, int(settings.AUDIT_CACHE_MAX_ENTRIES))
        connection.execute(
            "DELETE FROM entries WHERE rowid IN ("
            "SELECT rowid FROM entries ORDER BY created_at DESC LIMIT -1 OFFSET ?)",
            (max_entries,),
        )
