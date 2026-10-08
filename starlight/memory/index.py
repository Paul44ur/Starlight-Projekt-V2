"""Vector index over the Memory folder.

Embeddings are cached in ``data/memory_index.sqlite`` keyed by file and content hash, and
rebuilt from the folder on every sync, so the Markdown files stay the source of truth:
edit or delete a file by hand and the index follows. Search is exact cosine similarity
with numpy, which is fast for tens of thousands of memories without an extra database.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from starlight.memory.embed import Embedder, Vector
from starlight.memory.store import MemoryEntry, MemoryStore


@dataclass
class Hit:
    entry: MemoryEntry
    score: float


def content_hash(entry: MemoryEntry) -> str:
    return hashlib.sha256(entry.text.encode()).hexdigest()


class MemoryIndex:
    def __init__(self, store: MemoryStore, embedder: Embedder, db_path: Path | None) -> None:
        self.store = store
        self.embedder = embedder
        if db_path is not None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(db_path) if db_path else ":memory:", check_same_thread=False, isolation_level=None
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS vectors (id TEXT PRIMARY KEY, hash TEXT, "
            "embedder TEXT, vector BLOB)"
        )
        self._lock = threading.Lock()
        self._entries: list[MemoryEntry] = []
        self._matrix = np.zeros((0, 1), dtype=np.float32)

    async def sync(self) -> int:
        """Bring the index in line with the folder. Returns how many files were embedded."""
        entries = self.store.all()
        with self._lock:
            rows = {
                row[0]: (row[1], row[2], row[3])
                for row in self._db.execute("SELECT id, hash, embedder, vector FROM vectors")
            }
        vectors: dict[str, Vector] = {}
        stale: list[MemoryEntry] = []
        for entry in entries:
            row = rows.get(entry.id)
            if row and row[0] == content_hash(entry) and row[1] == self.embedder.id:
                vectors[entry.id] = np.frombuffer(row[2], dtype=np.float32)
            else:
                stale.append(entry)
        for start in range(0, len(stale), 64):
            batch = stale[start : start + 64]
            embedded = await self.embedder.embed([e.text for e in batch])
            with self._lock:
                for entry, vector in zip(batch, embedded, strict=True):
                    vectors[entry.id] = vector
                    self._db.execute(
                        "INSERT OR REPLACE INTO vectors VALUES (?, ?, ?, ?)",
                        (entry.id, content_hash(entry), self.embedder.id, vector.tobytes()),
                    )
        current = {e.id for e in entries}
        with self._lock:
            for gone in set(rows) - current:
                self._db.execute("DELETE FROM vectors WHERE id = ?", (gone,))
        self._entries = entries
        self._matrix = (
            np.vstack([vectors[e.id] for e in entries])
            if entries
            else np.zeros((0, 1), dtype=np.float32)
        )
        return len(stale)

    async def search(self, query: str, top_k: int = 5, min_score: float = -1.0) -> list[Hit]:
        if not self._entries or top_k <= 0:
            return []
        [q] = await self.embedder.embed([query])
        scores = self._matrix @ q
        order = np.argsort(-scores)[:top_k]
        return [Hit(self._entries[i], float(scores[i])) for i in order if scores[i] >= min_score]

    def similar_pairs(self, threshold: float) -> list[tuple[MemoryEntry, MemoryEntry, float]]:
        """Pairs of entries at least ``threshold`` alike, most alike first."""
        if len(self._entries) < 2:
            return []
        scores = self._matrix @ self._matrix.T
        first, second = np.triu_indices(len(self._entries), k=1)
        found = [
            (self._entries[i], self._entries[j], float(scores[i, j]))
            for i, j in zip(first, second, strict=True)
            if scores[i, j] >= threshold
        ]
        return sorted(found, key=lambda pair: -pair[2])

    def __len__(self) -> int:
        return len(self._entries)

    def close(self) -> None:
        with self._lock:
            self._db.close()
