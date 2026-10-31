"""Quick notes as plain Markdown files you can open in any editor: "note: the WiFi password
for guests is on the router", "add to my shopping note: eggs", "what did I note about the
car?". Notes live in the Notes folder of your Starlight folder.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from starlight.core import Module, ModuleContext, ToolError, tool

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)


MAX_NOTE_BYTES = 1_000_000  # a note this big is a document: start another one
MAX_HITS = 100  # matching lines notes.find returns


class Config(BaseModel):
    folder: str = "Notes"


class NoteNotFound(ToolError):
    pass


class NotesModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    @property
    def folder(self) -> Path:
        folder = self.ctx.output_dir / (_BAD.sub("", self.config.folder).strip() or "Notes")
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _file(self, title: str) -> Path:
        name = _BAD.sub("", title).strip().strip(".")[:80]
        if _RESERVED.match(name):  # "Con", "Nul": names Windows keeps for devices
            name = f"_{name}"
        if not name:
            raise ToolError("give the note a title")
        return self.folder / f"{name}.md"

    def _notes(self) -> list[Path]:
        """The note files (a folder called "Ideas.md" is not one)."""
        return [p for p in self.folder.glob("*.md") if p.is_file()]

    def _find(self, title: str) -> Path:
        exact = self._file(title)
        if exact.is_file():
            return exact
        wanted = title.strip().casefold()
        matches = [p for p in self._notes() if wanted in p.stem.casefold()]
        if len(matches) == 1:
            return matches[0]
        if matches:
            names = ", ".join(sorted(p.stem for p in matches)[:10])
            raise ToolError(f"'{title}' matches several notes: {names}")
        raise NoteNotFound(f"no note called '{title}'; see notes.list")

    @staticmethod
    def _stamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M")

    @tool
    def note(self, title: str, text: str) -> dict[str, Any]:
        """Write a new note. If a note with this title exists, the text is added to it."""
        if not text.strip():
            raise ToolError("what should the note say?")
        path = self._file(title)
        if path.is_file():
            return self.append(title, text)
        if path.exists():  # (append would come back here: it finds notes as files only)
            raise ToolError(f"{path} is a folder, not a note; give the note another title")
        if len(text.encode("utf-8")) > MAX_NOTE_BYTES:
            raise ToolError("that is more than 1 MB of text: save it as a file instead of a note")
        path.write_text(f"# {title.strip()}\n\n{text.strip()}\n", encoding="utf-8")
        return {"saved": path.stem, "file": str(path)}

    @tool
    def append(self, title: str, text: str) -> dict[str, Any]:
        """Add text to the end of a note (with the date), e.g. to a shopping or ideas list."""
        if not text.strip():
            raise ToolError("what should be added?")
        try:
            path = self._find(title)
        except NoteNotFound:  # not "matches several notes": that one is for the user to settle
            return self.note(title, text)
        if path.stat().st_size + len(text.encode("utf-8")) > MAX_NOTE_BYTES:
            raise ToolError(
                f"the note '{path.stem}' would be over 1 MB: start a new one "
                f"(for example '{path.stem} 2')"
            )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n_{self._stamp()}_\n{text.strip()}\n")
        return {"added_to": path.stem, "file": str(path)}

    @tool
    def read(self, title: str) -> dict[str, Any]:
        """Read a note (by its title or part of it)."""
        path = self._find(title)
        return {"title": path.stem, "text": path.read_text("utf-8", errors="replace")[:20000]}

    @tool(name="list")
    def list_notes(self) -> dict[str, Any]:
        """Your notes, most recently changed first."""
        files = sorted(self._notes(), key=lambda p: p.stat().st_mtime, reverse=True)
        return {
            "count": len(files),
            "notes": [
                {
                    "title": p.stem,
                    "changed": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                }
                for p in files[:200]
            ],
        }

    @tool
    def find(self, text: str) -> dict[str, Any]:
        """Search all notes for a word or phrase; returns the matching lines."""
        wanted = text.strip().casefold()
        if not wanted:
            raise ToolError("what should I look for?")
        hits: list[dict[str, Any]] = []
        for path in sorted(self._notes()):
            for number, line in enumerate(
                path.read_text("utf-8", errors="replace").splitlines(), 1
            ):
                if wanted in line.casefold():
                    hits.append({"note": path.stem, "line": number, "text": line.strip()[:300]})
                    if len(hits) >= MAX_HITS:  # enough: stop even in the middle of a note
                        return {"count": len(hits), "matches": hits}
        return {"count": len(hits), "matches": hits}

    @tool
    async def delete(self, title: str) -> dict[str, Any]:
        """Delete a note. Asks first."""
        path = self._find(title)
        if not await self.ctx.ui.confirm(f"Delete the note '{path.stem}'?"):
            raise ToolError("not confirmed, the note is kept")
        path.unlink()
        return {"deleted": path.stem}
