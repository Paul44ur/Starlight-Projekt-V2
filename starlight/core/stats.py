"""Metrics in data/stats.db (SQLite). The core and modules write; the statistics viewer
(Step 10) only reads, so it keeps working when the AI is stopped."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    ts REAL NOT NULL,
    name TEXT NOT NULL,
    value REAL NOT NULL,
    labels TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS metrics_name_ts ON metrics (name, ts);
"""


class Stats:
    def __init__(self, path: Path | None) -> None:
        """``path=None`` keeps metrics in memory (tests)."""
        self.path = path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(path) if path else ":memory:", check_same_thread=False, isolation_level=None
        )
        if path is not None:
            self._db.execute("PRAGMA journal_mode=WAL")  # the viewer reads while we write
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def record(self, name: str, value: float = 1.0, **labels: Any) -> None:
        """Write one data point, e.g. ``record("tool.calls", module="web", ok=True)``."""
        row = (time.time(), name, float(value), json.dumps(labels, sort_keys=True, default=str))
        with self._lock:
            self._db.execute("INSERT INTO metrics VALUES (?, ?, ?, ?)", row)

    def prune(self, retention_days: int) -> int:
        """Delete data points older than ``retention_days``."""
        cutoff = time.time() - retention_days * 86400
        with self._lock:
            cursor = self._db.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        return cursor.rowcount

    def query(
        self, name: str, since: float = 0.0, limit: int = 1000
    ) -> list[tuple[float, float, dict[str, Any]]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, value, labels FROM metrics WHERE name = ? AND ts >= ? "
                "ORDER BY ts DESC LIMIT ?",
                (name, since, limit),
            ).fetchall()
        return [(ts, value, json.loads(labels)) for ts, value, labels in rows]

    def close(self) -> None:
        with self._lock:
            self._db.close()
