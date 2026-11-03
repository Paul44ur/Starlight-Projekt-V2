"""The earlier versions of files Starlight changes: notes, the journal, recipes, memory.

Before a change, the file as it was is kept (``save``): gzip-compressed, in
``data/history/history.sqlite`` together with when, why and how big. A version can be read,
compared with another or with the file as it is now, and put back (``restore``, which keeps
the current file as a version first, so a restore can be undone too). Deleting a file
through ``trash`` keeps its last version marked as deleted, so it can come back.

Kept: at most ``versions`` per file, none older than ``days``, and all of them together at
most ``max_mb`` (compressed; the oldest go first). The settings are read at every call
(NotebookConfig, ``notebook:`` in starlight.yaml), so a change applies at once.

This database is not a cache: it is the only copy of the earlier versions. The notebook's
index (index.py) lives elsewhere and can be thrown away; this one must not be.

Every writer gets this one History, so it also passes the news on: after a change, a
restore or a deletion, ``changed(path)`` tells its ``listeners`` (the index, which then
reads that note again at once).
"""

from __future__ import annotations

import difflib
import gzip
import hashlib
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlight.core.fsutil import retry_on_lock, write_atomic_bytes
from starlight.core.tools import ToolError
from starlight.notebook.db import opened

MIGRATIONS = (
    # 1: the versions. key: the path as compared (case-insensitive on Windows); path: as
    # given, for showing it; sha256 of the content, to skip keeping the same text twice.
    """
    CREATE TABLE versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT NOT NULL,
        path TEXT NOT NULL,
        saved REAL NOT NULL,
        size INTEGER NOT NULL,
        stored INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        reason TEXT NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0,
        data BLOB NOT NULL
    );
    CREATE INDEX versions_key ON versions (key, id);
    CREATE INDEX versions_saved ON versions (saved)
    """,
)
MB = 1024**2
log = logging.getLogger(__name__)


class HistoryError(ToolError):
    pass


@dataclass(frozen=True)
class Version:
    id: int
    path: str
    saved: float  # time.time() when it was kept
    size: int  # bytes of the file then
    reason: str  # what was about to change it ("notes.append", "before restore" ...)
    deleted: bool  # the file was deleted after this version (it is in the trash)

    @property
    def when(self) -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.saved))

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "when": self.when,
            "size": self.size,
            "reason": self.reason,
            "deleted": self.deleted,
        }


def key_of(path: Path) -> str:
    """The same file, however its path was written (relative, "..", case on Windows)."""
    return os.path.normcase(str(Path(path).resolve()))


class History:
    def __init__(self, db_path: Path, settings: Any = None) -> None:
        self.db_path = db_path
        # NotebookConfig or anything with keep_versions, versions, days and max_mb
        self.settings = settings
        self.listeners: list[Callable[[Path], None]] = []  # told about each changed file

    def changed(self, path: Path) -> None:
        """``path`` was written, put back or deleted: tell the listeners. One that fails is
        logged, never the writer's problem (the file is written either way)."""
        for listener in list(self.listeners):
            try:
                listener(path)
            except Exception:
                log.exception("telling %r that %s changed failed", listener, path)

    def _setting(self, name: str, default: Any) -> Any:
        return getattr(self.settings, name, default) if self.settings is not None else default

    @property
    def enabled(self) -> bool:
        return bool(self._setting("keep_versions", True))

    def _db(self) -> Any:
        return opened(self.db_path, MIGRATIONS)

    # -- keeping ---------------------------------------------------------------------------

    def save(
        self, path: Path, reason: str, *, min_gap: float = 0.0, deleted: bool = False
    ) -> int | None:
        """Keep ``path`` as it is now, before it changes. Returns the version's id; None
        when there is nothing to keep (no such file yet, or versions are switched off).

        The same content as the newest version is not kept twice. ``min_gap`` (seconds):
        no new version if the newest is younger (typing on the Notes page: one version per
        few minutes, not one per autosave)."""
        if not self.enabled:
            return None
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            return None
        key, now = key_of(path), time.time()
        digest = hashlib.sha256(data).hexdigest()
        with self._db() as db:
            last = db.execute(
                "SELECT id, saved, sha256 FROM versions WHERE key = ? ORDER BY id DESC LIMIT 1",
                (key,),
            ).fetchone()
            if last is not None and last[2] == digest:
                if deleted:
                    db.execute("UPDATE versions SET deleted = 1 WHERE id = ?", (last[0],))
                return int(last[0])
            if last is not None and not deleted and min_gap and now - last[1] < min_gap:
                return int(last[0])
            packed = gzip.compress(data, mtime=0)
            cursor = db.execute(
                "INSERT INTO versions (key, path, saved, size, stored, sha256, reason, deleted,"
                " data) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    str(path),
                    now,
                    len(data),
                    len(packed),
                    digest,
                    reason[:200],
                    int(deleted),
                    packed,
                ),
            )
            version = int(cursor.lastrowid or 0)
            self._trim(db, key, now, version)
        return version

    def _trim(self, db: Any, key: str, now: float, newest: int) -> None:
        """Keep at most `versions` of this file, none older than `days`, and all together
        under `max_mb`: the oldest go first. The version just kept (``newest``) always stays,
        even if it alone is bigger than the limit: it is the one an undo needs."""
        keep = max(1, int(self._setting("versions", 20)))
        days = float(self._setting("days", 90))
        limit = float(self._setting("max_mb", 200)) * MB
        removed = db.execute(
            "DELETE FROM versions WHERE key = ? AND id NOT IN"
            " (SELECT id FROM versions WHERE key = ? ORDER BY id DESC LIMIT ?)",
            (key, key, keep),
        ).rowcount
        removed += db.execute(
            "DELETE FROM versions WHERE saved < ?", (now - days * 86400,)
        ).rowcount
        total = int(db.execute("SELECT COALESCE(SUM(stored), 0) FROM versions").fetchone()[0])
        if total > limit:
            over, doomed = total - limit, []
            rows = db.execute(
                "SELECT id, stored FROM versions WHERE id != ? ORDER BY id", (newest,)
            )
            for version, stored in rows:
                if over <= 0:
                    break
                doomed.append((version,))
                over -= stored
            db.executemany("DELETE FROM versions WHERE id = ?", doomed)
            removed += len(doomed)
        if removed:
            db.execute("PRAGMA incremental_vacuum")  # give the space back to the disk

    # -- looking -----------------------------------------------------------------------------

    def versions(self, path: Path) -> list[Version]:
        """The kept versions of a file, newest first."""
        with self._db() as db:
            rows = db.execute(
                "SELECT id, path, saved, size, reason, deleted FROM versions WHERE key = ?"
                " ORDER BY id DESC",
                (key_of(path),),
            ).fetchall()
        return [Version(r[0], r[1], r[2], r[3], r[4], bool(r[5])) for r in rows]

    def _row(self, version: int) -> tuple[str, bytes]:
        with self._db() as db:
            row = db.execute("SELECT key, data FROM versions WHERE id = ?", (version,)).fetchone()
        if row is None:
            raise HistoryError(f"there is no version {version} (it may have been cleaned up)")
        return str(row[0]), gzip.decompress(row[1])

    def read(self, version: int) -> bytes:
        """A version's content, exactly as the file was."""
        return self._row(version)[1]

    def text(self, version: int) -> str:
        return self.read(version).decode("utf-8", errors="replace")

    def diff(self, path: Path, old: int, new: int | None = None) -> str:
        """What changed from version ``old`` to version ``new`` (None: to the file as it is
        now), as a unified diff."""
        before = self.text(old).splitlines(keepends=True)
        if new is None:
            try:
                after = path.read_bytes().decode("utf-8", errors="replace")
            except FileNotFoundError:
                after = ""
            label = "now"
        else:
            after, label = self.text(new), f"version {new}"
        return "".join(
            difflib.unified_diff(before, after.splitlines(keepends=True), f"version {old}", label)
        )

    def deleted(self, under: Path | None = None) -> list[Version]:
        """Files in the trash: deleted through ``trash`` and not back since (newest first),
        optionally only those inside the folder ``under``."""
        with self._db() as db:
            rows = db.execute(
                "SELECT id, path, saved, size, reason, deleted FROM versions AS v"
                " WHERE deleted = 1 AND id = (SELECT MAX(id) FROM versions WHERE key = v.key)"
                " ORDER BY id DESC"
            ).fetchall()
        folder = Path(key_of(under)) if under is not None else None
        found = []
        for r in rows:
            version = Version(r[0], r[1], r[2], r[3], r[4], True)
            if Path(version.path).exists():
                continue  # (made again since)
            if folder is not None and not Path(key_of(Path(version.path))).is_relative_to(folder):
                continue
            found.append(version)
        return found

    # -- changing ----------------------------------------------------------------------------

    def restore(self, path: Path, version: int) -> int | None:
        """Put version ``version`` of ``path`` back. The file as it is now is kept as a
        version first (returned), so this can be undone in turn."""
        key, data = self._row(version)
        if key != key_of(path):
            raise HistoryError(f"version {version} belongs to another file, not {path}")
        before = self.save(path, "before restore")
        write_atomic_bytes(path, data)
        self.changed(path)
        return before

    def trash(self, path: Path, reason: str = "deleted") -> int | None:
        """Delete ``path``, keeping it as a version marked deleted (see ``deleted``)."""
        if not path.is_file():
            raise HistoryError(f"{path} does not exist")
        version = self.save(path, reason, deleted=True)
        if version is None and self.enabled:
            raise HistoryError(f"{path} could not be kept, so it was not deleted")
        retry_on_lock(path.unlink)
        self.changed(path)
        return version

    def moved(self, old: Path, new: Path) -> None:
        """A file was renamed (or moved): its kept versions belong to its new name now, so
        they can still be listed and put back there."""
        with self._db() as db:
            db.execute(
                "UPDATE versions SET key = ?, path = ? WHERE key = ?",
                (key_of(new), str(new), key_of(old)),
            )

    def restore_deleted(self, path: Path) -> None:
        """Bring a deleted file back from the trash, as it was."""
        if path.exists():
            raise HistoryError(f"{path} exists again; nothing to bring back")
        kept = self.versions(path)
        if not kept:
            raise HistoryError(f"{path} is not in the trash")
        write_atomic_bytes(path, self.read(kept[0].id))
        self.changed(path)
