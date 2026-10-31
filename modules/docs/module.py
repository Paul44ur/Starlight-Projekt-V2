"""A private knowledge base over folders you choose: "what does my rental contract say about
pets?", "find the invoice from the plumber". Everything stays on this PC.

Files are split into overlapping parts, each part is turned into a vector with memory's
embedding model, and the vectors are kept in data/modules/docs/index.sqlite. Only new or
changed files are read again. `ask` finds the best parts and has the model answer from
them, citing the file and page; `search` just lists the matching parts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, Field

from starlight.core import KillSwitchEngaged, Message, Module, ModuleContext, ToolError, tool
from starlight.core.config import ConfigError, write_yaml_config
from starlight.core.safepaths import expand, inside
from starlight.text import decode_text

TEXT = {".txt", ".md", ".markdown", ".csv", ".log", ".json", ".yaml", ".yml", ".ini", ".rst"}
HTML = {".html", ".htm"}
# (folders starting with a dot, like .git and .venv, are skipped as well)
SKIP_DIRS = {"node_modules", "__pycache__", "venv", "$RECYCLE.BIN", "AppData"}
type Matrix = npt.NDArray[np.float32]
ANSWER = (
    "Answer the question using ONLY the numbered excerpts from the user's own files below. "
    "Be short and direct, in the language of the question. After each fact put its excerpt "
    "number like [2]. If the excerpts do not contain the answer, say so plainly."
)


class Config(BaseModel):
    folders: list[str] = Field(default_factory=list)
    include_notes: bool = True
    reindex_minutes: int = Field(default=60, ge=0)
    max_file_mb: int = Field(default=30, ge=1)
    max_files: int = Field(default=20000, ge=10)
    chunk_chars: int = Field(default=1200, ge=200, le=8000)
    top_k: int = Field(default=6, ge=1, le=30)


@dataclass
class Part:
    path: str
    page: int  # 1-based page (PDF) or sheet number (Excel); 0 when the file has no pages
    text: str


# -- reading files -------------------------------------------------------------------------


def read_pages(path: Path) -> list[tuple[int, str]]:
    """(page, text) for a file; [] when it has no readable text or its reader is missing."""
    suffix = path.suffix.lower()
    if suffix in TEXT:
        return [(0, decode_text(path.read_bytes()))]
    if suffix in HTML:
        from starlight.text import html_to_text

        return [(0, html_to_text(decode_text(path.read_bytes())).text)]
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return []
        reader = PdfReader(str(path))
        return [(i, page.extract_text() or "") for i, page in enumerate(reader.pages, 1)]
    if suffix == ".docx":
        try:
            import docx
        except ImportError:
            return []
        document = docx.Document(str(path))
        lines = [p.text for p in document.paragraphs]
        for table in document.tables:
            lines += [" | ".join(c.text for c in row.cells) for row in table.rows]
        return [(0, "\n".join(lines))]
    if suffix == ".xlsx":
        try:
            import openpyxl
        except ImportError:
            return []
        book = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
        try:
            pages = []
            for number, sheet in enumerate(book.worksheets, 1):
                rows = [
                    " | ".join("" if v is None else str(v) for v in row)
                    for row in sheet.iter_rows(values_only=True, max_row=5000)
                ]
                pages.append((number, f"Sheet {sheet.title}\n" + "\n".join(rows)))
            return pages
        finally:
            book.close()
    return []


def readable(path: Path) -> bool:
    return path.suffix.lower() in TEXT | HTML | {".pdf", ".docx", ".xlsx"}


def chunks(text: str, size: int) -> Iterator[str]:
    """Parts of about ``size`` characters, cut at paragraph or sentence ends, overlapping a
    little so a fact on a boundary is in both."""
    clean = "\n".join(line.rstrip() for line in text.replace("\r", "").splitlines())
    clean = clean.strip()
    start = 0
    while start < len(clean):
        end = min(start + size, len(clean))
        if end < len(clean):
            for mark in ("\n\n", "\n", ". ", " "):
                cut = clean.rfind(mark, start + size // 2, end)
                if cut != -1:
                    end = cut + len(mark)
                    break
        piece = clean[start:end].strip()
        if piece:
            yield piece
        if end >= len(clean):
            break
        start = max(end - size // 8, start + 1)


# -- the module ----------------------------------------------------------------------------


class DocsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self._db = sqlite3.connect(
            str(ctx.data_dir / "index.sqlite"), check_same_thread=False, isolation_level=None
        )
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, size INTEGER,"
            " embedder TEXT);"
            "CREATE TABLE IF NOT EXISTS parts (id INTEGER PRIMARY KEY, path TEXT, page INTEGER,"
            " text TEXT, vector BLOB);"
            "CREATE INDEX IF NOT EXISTS parts_path ON parts(path);"
        )
        self._lock = threading.Lock()
        self._busy = asyncio.Lock()
        self._index: tuple[list[Part], Matrix] | None = None  # (parts, their vectors as a matrix)
        self._generation = 0  # bumped whenever the index is dropped
        if self.config.reindex_minutes:
            ctx.every(self.config.reindex_minutes * 60, self.reindex, "reindex", first_delay=30)

    async def shutdown(self) -> None:
        with self._lock:
            self._db.close()

    def _sql(self, query: str, args: tuple[Any, ...] = ()) -> list[Any]:
        with self._lock:
            return self._db.execute(query, args).fetchall()

    def _roots(self) -> list[Path]:
        roots = [expand(f) for f in self.config.folders]
        if self.config.include_notes:
            # "Notes" must match the notes module's folder (`folder` in
            # config/modules/notes.yaml, "Notes" by default): modules cannot import each
            # other's settings, so a changed notes folder is not followed here.
            # output.folder is invalid or cannot be resolved: index the other folders
            with contextlib.suppress(ConfigError, OSError):
                roots.append(self.ctx.output_dir / "Notes")
        own = self.ctx.paths.root.resolve()
        return [r for r in dict.fromkeys(roots) if r.is_dir() and not inside(r, own)]

    def _files(self) -> Iterator[Path]:
        own = self.ctx.paths.root.resolve()
        limit = self.config.max_file_mb * 1024**2
        count = 0
        for root in self._roots():
            for folder, dirs, names in os.walk(root):
                here = Path(folder)
                if inside(here, own):
                    dirs[:] = []
                    continue
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
                for name in names:
                    path = here / name
                    if not readable(path) or name.startswith("~$"):  # Office lock files
                        continue
                    with contextlib.suppress(OSError):
                        if path.stat().st_size <= limit:
                            count += 1
                            if count > self.config.max_files:
                                return
                            yield path

    @property
    def _embedder_id(self) -> str:
        return str(getattr(self.ctx.embedder, "id", "embedder"))

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """One commit for a group of writes (the connection is in autocommit mode, where
        every row would be its own fsync, and a crash could leave a file without parts)."""
        with self._lock:
            self._db.execute("BEGIN")
            try:
                yield self._db
            except BaseException:
                with contextlib.suppress(sqlite3.Error):
                    self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _store(
        self,
        key: str,
        stat: os.stat_result,
        parts: list[tuple[int, str]],
        vectors: list[Any],
    ) -> None:
        rows = [
            (key, page, text, np.asarray(v, dtype=np.float32).tobytes())
            for (page, text), v in zip(parts, vectors, strict=True)
        ]
        with self._transaction() as db:
            db.execute("DELETE FROM parts WHERE path = ?", (key,))
            db.executemany("INSERT INTO parts (path, page, text, vector) VALUES (?, ?, ?, ?)", rows)
            db.execute(
                "INSERT OR REPLACE INTO files (path, mtime, size, embedder) VALUES (?, ?, ?, ?)",
                (key, stat.st_mtime, stat.st_size, self._embedder_id),
            )

    def _invalidate(self) -> None:
        self._index = None  # reload on the next search
        self._generation += 1

    async def _embedder_down(self) -> bool:
        try:
            await self.ctx.embedder.embed(["ok"])
        except Exception:
            return True
        return False

    async def reindex(self) -> dict[str, Any]:
        """Read new and changed files, forget deleted ones."""
        async with self._busy:
            self.ctx.kill_switch.check()
            # path -> (mtime, size, embedder): a file whose stat and embedder match is unchanged
            known = {
                path: (mtime, size, embedder)
                for path, mtime, size, embedder in self._sql(
                    "SELECT path, mtime, size, embedder FROM files"
                )
            }
            seen: set[str] = set()
            changed = failed = 0
            touched = False
            try:
                for path in await asyncio.to_thread(lambda: list(self._files())):
                    key = str(path)
                    seen.add(key)
                    try:
                        stat = path.stat()
                        if known.get(key) == (stat.st_mtime, stat.st_size, self._embedder_id):
                            continue
                        self.ctx.kill_switch.check()
                        pages = await asyncio.to_thread(read_pages, path)
                    except KillSwitchEngaged:
                        raise
                    except Exception as exc:  # a damaged or locked file must not stop the rest
                        self.ctx.logger.warning("could not read", file=key, error=str(exc))
                        failed += 1
                        continue
                    parts = [
                        (page, piece)
                        for page, text in pages
                        for piece in chunks(text, self.config.chunk_chars)
                    ]
                    try:
                        vectors: list[Any] = []
                        for start in range(0, len(parts), 32):
                            batch = [p[1] for p in parts[start : start + 32]]
                            vectors.extend(await self.ctx.embedder.embed(batch))
                        touched = True
                        await asyncio.to_thread(self._store, key, stat, parts, vectors)
                    except KillSwitchEngaged:
                        raise
                    except Exception as exc:  # e.g. a part too long for the embedding model
                        self.ctx.logger.warning("could not index", file=key, error=str(exc))
                        failed += 1
                        if await self._embedder_down():  # it is the embedder, not this file
                            raise
                        continue
                    changed += 1
            finally:
                if touched:
                    self._invalidate()  # also after an aborted run: what it stored is searchable
            gone = set(known) - seen
            if gone:
                with self._transaction() as db:
                    for key in gone:
                        db.execute("DELETE FROM parts WHERE path = ?", (key,))
                        db.execute("DELETE FROM files WHERE path = ?", (key,))
                self._invalidate()
            total = self._sql("SELECT COUNT(*) FROM files")[0][0]
            return {"files": total, "read": changed, "removed": len(gone), "unreadable": failed}

    def _load(self) -> tuple[list[Part], Matrix]:
        rows = self._sql("SELECT path, page, text, vector FROM parts")
        parts = [Part(r[0], r[1], r[2]) for r in rows]
        matrix = (
            np.vstack([np.frombuffer(r[3], dtype=np.float32) for r in rows])
            if rows
            else np.zeros((0, 1), dtype=np.float32)
        )
        return parts, matrix

    async def _search(self, query: str, top_k: int) -> list[tuple[Part, float]]:
        if not query.strip():
            raise ToolError("what should I look for?")
        index = self._index  # one snapshot: a reindex may reset self._index while we wait
        if index is None:
            generation = self._generation
            index = await asyncio.to_thread(self._load)
            if generation == self._generation:  # not invalidated while it was loading
                self._index = index
        parts, matrix = index
        if not parts:
            raise ToolError(
                "the knowledge base is empty: add a folder with docs.add_folder, then wait "
                "for it to be read (or run docs.reindex)"
            )
        [vector] = await self.ctx.embedder.embed([query])
        scores = matrix @ np.asarray(vector, dtype=np.float32)
        order = np.argsort(-scores)[:top_k]
        return [(parts[i], float(scores[i])) for i in order]

    @staticmethod
    def _where(part: Part) -> str:
        return f"{part.path} (page {part.page})" if part.page else part.path

    def indexed(self, path: str) -> bool:
        """Whether this file is in the knowledge base (the web chat opens only those)."""
        return bool(self._sql("SELECT 1 FROM parts WHERE path = ? LIMIT 1", (path,)))

    @staticmethod
    def link(path: str, page: int) -> str:
        """Where the web chat opens the file, at the page for a PDF."""
        at = f"#page={page}" if page and path.lower().endswith(".pdf") else ""
        return f"/docs/file?path={quote(path)}{at}"

    # -- tools ---------------------------------------------------------------------------

    @tool
    async def ask(self, question: str) -> dict[str, Any]:
        """Answer a question from your own documents, with the files (and pages) it came
        from. Show the answer and the sources to the user."""
        hits = await self._search(question, self.config.top_k)
        excerpts = "\n\n".join(
            f"[{i}] {self._where(p)}\n{p.text}" for i, (p, _) in enumerate(hits, 1)
        )
        model = self.ctx.model_for("docs")
        if model is None:
            raise ToolError("no model is configured")
        reply = await model.chat(
            [Message("system", ANSWER), Message("user", f"Question: {question}\n\n{excerpts}")],
            [],
        )
        sources = [
            {"n": i, "file": p.path, "page": p.page or None, "link": self.link(p.path, p.page)}
            for i, (p, _) in enumerate(hits, 1)
        ]
        return {
            "answer": reply.content.strip(),
            "sources": sources,
            "note": "In the web chat, show each source as a Markdown link to its link.",
        }

    @tool
    async def search(self, query: str, limit: int = 0) -> dict[str, Any]:
        """Find the passages in your documents that best match `query` (file, page and a
        snippet each) - without an answer."""
        hits = await self._search(query, max(1, min(limit or self.config.top_k, 30)))
        return {
            "results": [
                {
                    "file": p.path,
                    "page": p.page or None,
                    "link": self.link(p.path, p.page),
                    "score": round(s, 3),
                    "snippet": p.text[:400],
                }
                for p, s in hits
            ]
        }

    @tool
    async def add_folder(self, path: str) -> dict[str, Any]:
        """Add a folder to the knowledge base (its files are read and indexed on this PC).
        Asks first."""
        folder = expand(path)
        if not folder.is_dir():
            raise ToolError(f"{folder} is not a folder")
        if inside(folder, self.ctx.paths.root.resolve()):
            raise ToolError("Starlight's own folder is off-limits")
        if str(folder) in [str(expand(f)) for f in self.config.folders]:
            raise ToolError(f"{folder} is already in the knowledge base")
        if not await self.ctx.ui.confirm(
            f"Read and index the files in {folder} (they stay on this PC)?"
        ):
            raise ToolError("not confirmed, nothing changed")
        self.config.folders.append(str(folder))
        self._write_config()
        summary = await self.reindex()
        return {"added": str(folder), **summary}

    @tool
    def remove_folder(self, path: str) -> dict[str, Any]:
        """Take a folder out of the knowledge base (its files are forgotten at the next
        reindex)."""
        folder = str(expand(path))
        kept = [f for f in self.config.folders if str(expand(f)) != folder]
        if len(kept) == len(self.config.folders):
            raise ToolError(f"{folder} is not in the knowledge base")
        self.config.folders = kept
        self._write_config()
        return {
            "removed": folder,
            "note": "its files are dropped from the index at the next reindex",
        }

    @tool
    def folders(self) -> dict[str, Any]:
        """The folders in the knowledge base and how many files are indexed."""
        files = self._sql("SELECT COUNT(*) FROM files")[0][0]
        parts = self._sql("SELECT COUNT(*) FROM parts")[0][0]
        return {"folders": [str(r) for r in self._roots()], "files": files, "parts": parts}

    @tool(name="reindex")
    async def reindex_now(self) -> dict[str, Any]:
        """Read new and changed files now (normally this happens by itself every hour)."""
        return await self.reindex()

    def _write_config(self) -> None:
        write_yaml_config(
            self.ctx.paths.module_configs / "docs.yaml",
            json.loads(self.config.model_dump_json()),
            "# The docs module (modules/docs): your private knowledge base.",
        )
