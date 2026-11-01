"""What is in every note, kept in ``data/notebook/index.sqlite`` so finding is fast.

For each Markdown file in the notebook folder (the Starlight folder in Documents, without
its ``code`` folder and hidden folders): its title, aliases, properties, links, tags and
tasks, and its words for the full-text search. The notes stay the truth: this file is a
cache, and deleting it only means it is built again.

``refresh`` looks at the files' modification times and sizes, at most every few seconds,
and reads again only the notes that changed (no file watcher needed). Starlight's own
writes update the index at once (``touch``, which History calls after every change).

Search uses SQLite's FTS5 when this Python has it (it ranks hits and finds word starts).
Without it, a plain scan finds the same words, more slowly. Which one is chosen when the
index opens; if that changes, the index is built again.

Caps: 20,000 files (the rest is left out and ``capped`` says so) and 1 MB per note (a
bigger one is listed, but its text is not read).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlight.notebook.db import connect
from starlight.notebook.links import Known, Resolver, clean, key
from starlight.notebook.markdown import parse

log = logging.getLogger(__name__)

MAX_FILES = 20_000
MAX_NOTE_BYTES = 1024**2
REFRESH_EVERY = 5.0  # seconds: refresh() looks at the files at most this often
SKIP_TOP = {"code"}  # Starlight's code folder holds programs, not notes

MIGRATIONS = (
    """
    CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE files (
        id INTEGER PRIMARY KEY,
        path TEXT NOT NULL UNIQUE,
        folder TEXT NOT NULL,
        name TEXT NOT NULL,
        title TEXT NOT NULL,
        mtime_ns INTEGER NOT NULL,
        size INTEGER NOT NULL,
        words INTEGER NOT NULL DEFAULT 0,
        aliases TEXT NOT NULL DEFAULT '[]',
        error TEXT NOT NULL DEFAULT '',
        too_big INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE links (
        file_id INTEGER NOT NULL, line INTEGER NOT NULL, target TEXT NOT NULL,
        name_key TEXT NOT NULL, heading TEXT NOT NULL, block TEXT NOT NULL,
        text TEXT NOT NULL, embed INTEGER NOT NULL
    );
    CREATE INDEX links_file ON links (file_id);
    CREATE INDEX links_name ON links (name_key);
    CREATE TABLE tags (file_id INTEGER NOT NULL, line INTEGER NOT NULL, tag TEXT NOT NULL,
                       shown TEXT NOT NULL);
    CREATE INDEX tags_file ON tags (file_id);
    CREATE INDEX tags_tag ON tags (tag);
    CREATE TABLE properties (file_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL);
    CREATE INDEX properties_file ON properties (file_id);
    CREATE TABLE tasks (
        file_id INTEGER NOT NULL, line INTEGER NOT NULL, indent INTEGER NOT NULL,
        status TEXT NOT NULL, text TEXT NOT NULL, due TEXT NOT NULL, start TEXT NOT NULL,
        done TEXT NOT NULL, priority TEXT NOT NULL, repeat TEXT NOT NULL
    );
    CREATE INDEX tasks_file ON tasks (file_id);
    CREATE TABLE texts (file_id INTEGER PRIMARY KEY, folded TEXT NOT NULL, body TEXT NOT NULL)
    """,
)
FTS = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, body,"
    " tokenize = 'unicode61 remove_diacritics 2')"
)
WORD = re.compile(r"\w+")


def has_fts5() -> bool:
    """This Python's SQLite can do full-text search (FTS5)."""
    try:
        with closing(sqlite3.connect(":memory:")) as db:
            db.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
        return True
    except sqlite3.OperationalError:
        return False


@dataclass(frozen=True)
class NoteInfo:
    path: str  # relative to the notebook folder, with "/"
    folder: str
    name: str  # the file name without .md
    title: str  # the "title" property, else the name
    aliases: list[str]
    modified: float  # seconds since 1970
    size: int
    words: int
    error: str  # why its properties could not be read, or ""
    too_big: bool  # over 1 MB: listed, but its text was not read

    def public(self) -> dict[str, Any]:
        return {**self.__dict__}


@dataclass(frozen=True)
class Hit:
    path: str
    title: str
    snippet: str  # a few words around the first hit


@dataclass(frozen=True)
class Refreshed:
    added: int = 0
    changed: int = 0
    removed: int = 0
    total: int = 0
    capped: bool = False  # more than MAX_FILES files: some were left out
    skipped: bool = False  # looked too recently: nothing was done


def _info(row: Any) -> NoteInfo:
    return NoteInfo(
        row[0], row[1], row[2], row[3], json.loads(row[4]), row[5] / 1e9, row[6], row[7],
        row[8], bool(row[9]),
    )  # fmt: skip


INFO = "path, folder, name, title, aliases, mtime_ns, size, words, error, too_big"


class Index:
    def __init__(
        self,
        db_path: Path,
        root: Path,
        *,
        fts: bool | None = None,
        max_files: int = MAX_FILES,
        max_note_bytes: int = MAX_NOTE_BYTES,
        refresh_every: float = REFRESH_EVERY,
    ) -> None:
        self.db_path = db_path
        self.root = root
        self.fts = has_fts5() if fts is None else fts
        self.max_files = max_files
        self.max_note_bytes = max_note_bytes
        self.refresh_every = refresh_every
        self.capped = False
        self._lock = threading.RLock()  # (the web app calls from several threads)
        self._last = float("-inf")
        self._ready = False

    # -- the database --------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return "fts5" if self.fts else "scan"

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if not self._ready:
                self._prepare()
            with closing(connect(self.db_path, MIGRATIONS)) as db, db:
                yield db

    def _prepare(self) -> None:
        """Open (or build) the index for this search mode; a cache made for the other
        mode, or one that cannot be read, is thrown away and built again."""
        try:
            with closing(connect(self.db_path, MIGRATIONS)) as db:
                row = db.execute("SELECT value FROM meta WHERE key = 'mode'").fetchone()
                usable = row is None or row[0] == self.mode
        except sqlite3.DatabaseError as exc:
            log.warning("notebook index %s unreadable, building it again: %s", self.db_path, exc)
            usable = False
        if not usable:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(f"{self.db_path}{suffix}").unlink(missing_ok=True)
        with closing(connect(self.db_path, MIGRATIONS)) as db, db:
            db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('mode', ?)", (self.mode,))
            if self.fts:
                db.execute(FTS)
        self._ready = True

    # -- keeping it up to date -----------------------------------------------------------

    def _walk(self) -> dict[str, tuple[Path, int, int]]:
        """The notes now: relative path -> (path, mtime_ns, size)."""
        found: dict[str, tuple[Path, int, int]] = {}
        self.capped = False
        if not self.root.is_dir():
            return found
        for folder, dirs, files in os.walk(self.root):
            here = Path(folder)
            top = here == self.root
            dirs[:] = sorted(
                d for d in dirs if not d.startswith(".") and not (top and d.casefold() in SKIP_TOP)
            )
            for name in sorted(files):
                if not name.casefold().endswith(".md") or name.startswith("."):
                    continue
                if len(found) >= self.max_files:
                    self.capped = True
                    return found
                path = here / name
                try:
                    info = path.stat()
                except OSError:
                    continue
                relative = path.relative_to(self.root).as_posix()
                found[relative] = (path, info.st_mtime_ns, info.st_size)
        return found

    def refresh(self, force: bool = False) -> Refreshed:
        """Bring the index up to date with the files (at most every ``refresh_every``
        seconds unless ``force``)."""
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last < self.refresh_every:
                return Refreshed(skipped=True, capped=self.capped)
            self._last = now
            files = self._walk()
            added = changed = removed = 0
            with self._db() as db:
                known = {
                    row[0]: (row[1], row[2], row[3])
                    for row in db.execute("SELECT path, id, mtime_ns, size FROM files")
                }
                for path, (file_id, _, _) in known.items():
                    if path not in files:
                        self._forget(db, file_id)
                        removed += 1
                for path, (full, mtime, size) in files.items():
                    old = known.get(path)
                    if old is not None and old[1:] == (mtime, size):
                        continue
                    if self._read(db, path, full, mtime, size, old[0] if old else None):
                        changed += old is not None
                        added += old is None
            return Refreshed(added, changed, removed, len(files), self.capped)

    def touch(self, path: Path) -> None:
        """``path`` changed or went (Starlight wrote it): update just that note now."""
        try:
            relative = path.resolve().relative_to(self.root.resolve()).as_posix()
        except (OSError, ValueError):
            return  # not in the notebook
        if not relative.casefold().endswith(".md") or relative.split("/")[0].casefold() in (
            SKIP_TOP
        ):
            return
        with self._db() as db:
            row = db.execute("SELECT id FROM files WHERE path = ?", (relative,)).fetchone()
            try:
                info = path.stat()
            except OSError:
                if row is not None:
                    self._forget(db, row[0])
                return
            self._read(db, relative, path, info.st_mtime_ns, info.st_size, row[0] if row else None)

    def _forget(self, db: sqlite3.Connection, file_id: int) -> None:
        for table in ("links", "tags", "properties", "tasks"):
            db.execute(f"DELETE FROM {table} WHERE file_id = ?", (file_id,))
        db.execute("DELETE FROM texts WHERE file_id = ?", (file_id,))
        if self.fts:
            db.execute("DELETE FROM fts WHERE rowid = ?", (file_id,))
        db.execute("DELETE FROM files WHERE id = ?", (file_id,))

    def _read(
        self,
        db: sqlite3.Connection,
        relative: str,
        path: Path,
        mtime: int,
        size: int,
        file_id: int | None,
    ) -> bool:
        """(Re-)index one note. False when it could not be read (it stays as it was)."""
        from starlight.text import decode_text

        too_big = size > self.max_note_bytes
        try:
            text = "" if too_big else decode_text(path.read_bytes())
        except OSError as exc:
            log.info("notebook index: cannot read %s: %s", path, exc)
            return False
        note = parse(text)
        folder, _, filename = relative.rpartition("/")
        name = filename[:-3]
        title = note.properties.get("title")
        title = str(title).strip() if isinstance(title, (str, int, float)) and str(title) else name
        if file_id is not None:
            self._forget(db, file_id)
        cursor = db.execute(
            "INSERT INTO files (path, folder, name, title, mtime_ns, size, words, aliases, error,"
            " too_big) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                relative, folder, name, title, mtime, size, note.words,
                json.dumps(note.aliases, ensure_ascii=False), note.error, int(too_big),
            ),
        )  # fmt: skip
        new_id = int(cursor.lastrowid or 0)
        db.executemany(
            "INSERT INTO links VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (new_id, link.line, link.target, key(clean(link.target).rpartition("/")[2]),
                 link.heading, link.block, link.text, int(link.embed))
                for link in note.links
            ],
        )  # fmt: skip
        db.executemany(
            "INSERT INTO tags VALUES (?, ?, ?, ?)",
            [(new_id, tag.line, key(tag.name), tag.name) for tag in note.tags],
        )
        db.executemany(
            "INSERT INTO properties VALUES (?, ?, ?)",
            [
                (new_id, name_, json.dumps(value, ensure_ascii=False))
                for name_, value in note.properties.items()
            ],
        )
        db.executemany(
            "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (new_id, t.line, t.indent, t.status, t.text, t.due, t.start, t.done,
                 t.priority, t.repeat)
                for t in note.tasks
            ],
        )  # fmt: skip
        if self.fts:
            db.execute(
                "INSERT INTO fts (rowid, title, body) VALUES (?, ?, ?)", (new_id, title, note.body)
            )
        else:
            folded = f"{title}\n{note.body}".casefold()  # (the plain scan compares these)
            db.execute("INSERT INTO texts VALUES (?, ?, ?)", (new_id, folded, note.body))
        return True

    # -- looking things up ---------------------------------------------------------------

    def notes(self, folder: str | None = None) -> list[NoteInfo]:
        """Every note (in ``folder`` and below, when given), by path."""
        with self._db() as db:
            if folder:
                rows = db.execute(
                    f"SELECT {INFO} FROM files WHERE folder = ? OR folder LIKE ? ESCAPE '\\'"
                    " ORDER BY path",
                    (folder, _like_prefix(folder) + "/%"),
                ).fetchall()
            else:
                rows = db.execute(f"SELECT {INFO} FROM files ORDER BY path").fetchall()
        return [_info(r) for r in rows]

    def note(self, path: str) -> NoteInfo | None:
        with self._db() as db:
            row = db.execute(f"SELECT {INFO} FROM files WHERE path = ?", (path,)).fetchone()
        return _info(row) if row else None

    def properties(self, path: str) -> dict[str, Any]:
        with self._db() as db:
            rows = db.execute(
                "SELECT p.key, p.value FROM properties p JOIN files f ON f.id = p.file_id"
                " WHERE f.path = ?",
                (path,),
            ).fetchall()
        return {name: json.loads(value) for name, value in rows}

    def search(self, text: str, limit: int = 20) -> list[Hit]:
        """Notes with all these words (the last one may be the start of a word), best
        first. Without FTS5: the same words, found by a plain scan."""
        words = WORD.findall(text)
        if not words:
            return []
        with self._db() as db:
            if self.fts:
                query = " ".join(f'"{w}"' for w in words[:-1]) + f' "{words[-1]}"*'
                rows = db.execute(
                    "SELECT f.path, f.title, snippet(fts, 1, '', '', '…', 12) FROM fts"
                    " JOIN files f ON f.id = fts.rowid WHERE fts MATCH ?"
                    " ORDER BY bm25(fts, 5.0, 1.0) LIMIT ?",
                    (query.strip(), limit),
                ).fetchall()
                return [Hit(r[0], r[1], r[2]) for r in rows]
            folded = [w.casefold() for w in words]
            where = " AND ".join("t.folded LIKE ? ESCAPE '\\'" for _ in folded)
            rows = db.execute(
                f"SELECT f.path, f.title, t.folded, t.body FROM texts t"
                f" JOIN files f ON f.id = t.file_id WHERE {where}",
                [f"%{_like_prefix(w)}%" for w in folded],
            ).fetchall()

        def rank(row: Any) -> tuple[int, int]:  # words in the title first, then how often
            title = row[1].casefold()
            return (-sum(w in title for w in folded), -sum(row[2].count(w) for w in folded))

        ranked = sorted(rows, key=rank)
        return [Hit(r[0], r[1], _snippet(r[3], folded)) for r in ranked[:limit]]

    def tags(self) -> list[tuple[str, int]]:
        """Each tag (as first written) and in how many notes, most used first."""
        with self._db() as db:
            rows = db.execute(
                "SELECT MIN(shown), COUNT(DISTINCT file_id) AS n FROM tags GROUP BY tag"
                " ORDER BY n DESC, tag"
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def with_tag(self, tag: str) -> list[str]:
        """Notes with ``tag`` or a tag under it (``work`` finds ``#work/projects`` too)."""
        wanted = key(tag.lstrip("#"))
        with self._db() as db:
            rows = db.execute(
                "SELECT DISTINCT f.path FROM tags t JOIN files f ON f.id = t.file_id"
                " WHERE t.tag = ? OR t.tag LIKE ? ESCAPE '\\' ORDER BY f.path",
                (wanted, _like_prefix(wanted) + "/%"),
            ).fetchall()
        return [r[0] for r in rows]

    def tasks(self, open_only: bool = False) -> list[dict[str, Any]]:
        """The tasks in all notes (open ones only, if asked), by note and line."""
        with self._db() as db:
            rows = db.execute(
                "SELECT f.path, t.line, t.indent, t.status, t.text, t.due, t.start, t.done,"
                " t.priority, t.repeat FROM tasks t JOIN files f ON f.id = t.file_id"
                + (" WHERE t.status = ' '" if open_only else "")
                + " ORDER BY f.path, t.line"
            ).fetchall()
        names = ("path", "line", "indent", "status", "text", "due", "start", "done", "priority")
        return [dict(zip((*names, "repeat"), r, strict=True)) for r in rows]

    # -- links ---------------------------------------------------------------------------

    def resolver(self) -> Resolver:
        with self._db() as db:
            rows = db.execute("SELECT path, aliases FROM files").fetchall()
        return Resolver(Known(path, tuple(json.loads(aliases))) for path, aliases in rows)

    def resolve(self, target: str, source: str | None = None) -> str | None:
        """The note a link to ``target`` from ``source`` means (links.Resolver)."""
        return self.resolver().resolve(target, source)

    def links(self, path: str) -> list[dict[str, Any]]:
        """The links in a note, each with the note it leads to (None: no such note yet)."""
        resolver = self.resolver()
        with self._db() as db:
            rows = db.execute(
                "SELECT l.line, l.target, l.heading, l.block, l.text, l.embed FROM links l"
                " JOIN files f ON f.id = l.file_id WHERE f.path = ? ORDER BY l.line, l.rowid",
                (path,),
            ).fetchall()
        return [
            {
                "line": r[0], "target": r[1], "heading": r[2], "block": r[3], "text": r[4],
                "embed": bool(r[5]), "to": resolver.resolve(r[1], path),
            }
            for r in rows
        ]  # fmt: skip

    def backlinks(self, path: str) -> list[dict[str, Any]]:
        """The links from other notes to ``path``: which note, which line."""
        resolver = self.resolver()
        names = sorted(resolver.names_of(path))
        marks = ", ".join("?" for _ in names)
        with self._db() as db:
            rows = db.execute(
                f"SELECT f.path, l.line, l.target, l.heading, l.embed FROM links l"
                f" JOIN files f ON f.id = l.file_id WHERE l.name_key IN ({marks})"
                " ORDER BY f.path, l.line, l.rowid",
                names,
            ).fetchall()
        found = [
            {"from": r[0], "line": r[1], "target": r[2], "heading": r[3], "embed": bool(r[4])}
            for r in rows
            if r[0] != path and resolver.resolve(r[2], r[0]) == path
        ]
        texts: dict[str, list[str]] = {}
        for link in found:  # the line that links, to show it with the link
            if link["from"] not in texts:
                try:
                    data = (self.root / link["from"]).read_bytes()[: self.max_note_bytes]
                    texts[link["from"]] = data.decode("utf-8", errors="replace").splitlines()
                except OSError:
                    texts[link["from"]] = []
            lines = texts[link["from"]]
            link["text"] = lines[link["line"]].strip()[:300] if link["line"] < len(lines) else ""
        return found


def _like_prefix(text: str) -> str:
    """``text`` for a LIKE pattern: its % and _ taken literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _snippet(body: str, words: list[str], around: int = 60) -> str:
    """A few words of ``body`` around the first of ``words`` (casefolded)."""
    lower = body.lower()  # (about the same length as the body, unlike casefold: ß -> ss)
    at = min((i for i in (lower.find(w) for w in words) if i >= 0), default=0)
    start, end = max(0, at - around), min(len(body), at + around)
    text = " ".join(body[start:end].split())
    return ("…" if start else "") + text + ("…" if end < len(body) else "")
