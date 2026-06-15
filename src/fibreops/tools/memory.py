"""Procedural memory tool.

Backed by SQLite under ./state/memory.db so memories persist across runs.
In production this maps onto Foundry Agent Service's hosted memory store via
the AIProjectClient agents memory APIs — the public surface (remember/recall)
stays identical.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ..observability import get_logger, tool_span

logger = get_logger(__name__)

_DB_PATH = Path("state") / "memory.db"
_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.execute(
        """CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    return con


def remember(scope: str, key: str, value: Any) -> dict[str, Any]:
    """Persist a procedural memory entry (scope='global' or an incident id)."""
    with tool_span("memory.remember", scope=scope, key=key), _LOCK, _conn() as con:
        con.execute(
            "INSERT INTO memories (scope, key, value) VALUES (?, ?, ?)",
            (scope, key, json.dumps(value, default=str)),
        )
        return {"stored": True, "scope": scope, "key": key}


def recall(scope: str, key: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
    """Retrieve recent memories for the given scope (optionally filtered by key)."""
    with tool_span("memory.recall", scope=scope, key=key or "*"), _LOCK, _conn() as con:
        if key:
            rows = con.execute(
                "SELECT key, value, created_at FROM memories WHERE scope=? AND key=? ORDER BY id DESC LIMIT ?",
                (scope, key, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT key, value, created_at FROM memories WHERE scope=? ORDER BY id DESC LIMIT ?",
                (scope, limit),
            ).fetchall()
        return [{"key": k, "value": json.loads(v), "created_at": c} for (k, v, c) in rows]
