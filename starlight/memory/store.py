"""The Memory folder. Each memory is a Markdown file you can open, fix or delete:

    ---
    topic: Favourite colour
    source: conversation 7afeafcc6f1c
    date: 2026-10-06
    confidence: 0.9
    tags: [preference]
    ---
    Paul's favourite colour is blue.

The id is the path relative to ``memory/`` without ``.md`` (e.g. ``favourite-colour`` or
``conversations/2026-10-06-7afe``).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from starlight.core.fsutil import write_atomic

FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def slugify(text: str, max_len: int = 60) -> str:
    text = text.replace("ß", "ss").replace("ẞ", "SS")  # "Straße" -> "strasse", not "strae"
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "note"


def read_text(path: Path) -> str:
    """A memory file as text: UTF-8 (with or without the BOM Notepad may add), else - a
    file saved by an older editor - Windows-1252, so one odd file never breaks memory."""
    from starlight.text import decode_text

    return decode_text(path.read_bytes())


@dataclass
class MemoryEntry:
    id: str
    topic: str
    content: str
    source: str = "user"
    date: str = field(default_factory=lambda: dt.date.today().isoformat())
    confidence: float = 1.0
    tags: list[str] = field(default_factory=list)
    expires: str = ""  # a date (YYYY-MM-DD) after which it no longer holds; "" never

    def to_markdown(self) -> str:
        header: dict[str, Any] = {
            "topic": self.topic,
            "source": self.source,
            "date": self.date,
            "confidence": round(self.confidence, 2),
        }
        if self.tags:
            header["tags"] = self.tags
        if self.expires:
            header["expires"] = self.expires
        front = yaml.safe_dump(header, sort_keys=False, allow_unicode=True).strip()
        return f"---\n{front}\n---\n{self.content.strip()}\n"

    @property
    def text(self) -> str:
        """What gets embedded and shown to the model."""
        return f"{self.topic}: {self.content}".strip()

    def expired(self, today: dt.date | None = None) -> bool:
        """Its "expires" date has passed (a fact that only held until then)."""
        if not self.expires:
            return False
        try:
            return dt.date.fromisoformat(self.expires[:10]) < (today or dt.date.today())
        except ValueError:
            return False


class MemoryStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, entry_id: str) -> Path:
        path = (self.root / f"{entry_id}.md").resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError(f"invalid memory id: {entry_id}")
        return path

    def parse(self, path: Path) -> MemoryEntry:
        raw = read_text(path)
        entry_id = path.relative_to(self.root).with_suffix("").as_posix()
        match = FRONT.match(raw)
        header: dict[str, Any] = {}
        body = raw
        if match:
            try:
                loaded = yaml.safe_load(match.group(1))
                header = loaded if isinstance(loaded, dict) else {}
                body = match.group(2)
            except yaml.YAMLError:
                header = {}
        try:
            confidence = float(header.get("confidence", 1.0))
        except (TypeError, ValueError):
            confidence = 1.0
        tags = header.get("tags") or []
        return MemoryEntry(
            id=entry_id,
            topic=str(header.get("topic") or path.stem.replace("-", " ")),
            content=body.strip(),
            source=str(header.get("source") or "user"),
            date=str(header.get("date") or dt.date.today().isoformat()),
            confidence=confidence,
            tags=[str(t) for t in tags] if isinstance(tags, list) else [str(tags)],
            expires=str(header.get("expires") or ""),
        )

    def files(self) -> list[Path]:
        if not self.root.is_dir():
            return []
        return sorted(p for p in self.root.rglob("*.md") if not p.name.startswith("."))

    def all(self) -> list[MemoryEntry]:
        return [self.parse(p) for p in self.files()]

    def get(self, entry_id: str) -> MemoryEntry | None:
        path = self.path(entry_id)
        return self.parse(path) if path.is_file() else None

    def new_id(self, topic: str, folder: str = "") -> str:
        base = f"{folder}/{slugify(topic)}" if folder else slugify(topic)
        candidate, n = base, 2
        while self.path(candidate).exists():
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    def write(self, entry: MemoryEntry) -> Path:
        path = self.path(entry.id)
        write_atomic(path, entry.to_markdown())
        return path

    def delete(self, entry_id: str) -> bool:
        path = self.path(entry_id)
        if not path.is_file():
            return False
        path.unlink()
        return True
