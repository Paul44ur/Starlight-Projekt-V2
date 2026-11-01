"""Quick notes as plain Markdown files you can open in any editor: "note: the WiFi password
for guests is on the router", "add to my shopping note: eggs", "what did I note about the
car?". Notes live in the Notes folder of your Starlight folder.

Every change keeps the note as it was (starlight.notebook: History, write_note), so it can be
undone ("undo the last change to my shopping note", files.restore), and a deleted note goes
to the trash (notes.trash, notes.restore_deleted).

Notes link to each other with [[Note]]. notes.links and notes.backlinks follow them, and
notes.rename updates the links in other notes too (asking first when there are any).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.notebook import append_note, write_note
from starlight.notebook.rename import plan as plan_rename
from starlight.notebook.rename import rename as carry_out_rename

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
        if not matches:  # another name of a note (its aliases)
            matches = [p for p in self._notes() if wanted in self._aliases().get(p.name, [])]
        if len(matches) == 1:
            return matches[0]
        if matches:
            names = ", ".join(sorted(p.stem for p in matches)[:10])
            raise ToolError(f"'{title}' matches several notes: {names}")
        raise NoteNotFound(f"no note called '{title}'; see notes.list")

    def _relative(self, path: Path) -> str:
        """The note's path in the notebook index ("Notes/Shopping.md")."""
        return path.resolve().relative_to(Path(self.ctx.notebook.root).resolve()).as_posix()

    def _aliases(self) -> dict[str, list[str]]:
        """Each note's other names (file name -> aliases, without case)."""
        index = self.ctx.notebook
        index.refresh()
        folder = self._relative(self.folder / "x.md").rpartition("/")[0]
        return {
            Path(n.path).name: [a.casefold() for a in n.aliases]
            for n in index.notes(folder)
            if n.folder == folder and n.aliases
        }

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
        content = f"# {title.strip()}\n\n{text.strip()}\n"
        written = write_note(path, content, "notes.note", self.ctx.history)
        return {"saved": path.stem, "file": str(path), "changed": written.changed(path)}

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
        addition = f"\n_{self._stamp()}_\n{text.strip()}\n"
        written = append_note(path, addition, "notes.append", self.ctx.history)
        return {"added_to": path.stem, "file": str(path), "changed": written.changed(path)}

    @tool
    def read(self, title: str) -> dict[str, Any]:
        """Read a note (by its title or part of it), with the notes it links to and the
        notes that link to it."""
        path = self._find(title)
        index = self.ctx.notebook
        index.refresh()
        index.touch(path)
        relative = self._relative(path)
        return {
            "title": path.stem,
            "text": path.read_text("utf-8", errors="replace")[:20000],
            "links": sorted({link["target"] for link in index.links(relative) if link["target"]}),
            "linked_from": sorted({Path(b["from"]).stem for b in index.backlinks(relative)}),
        }

    @tool
    def links(self, title: str) -> dict[str, Any]:
        """The notes a note links to with [[...]], and which of them are not written yet."""
        path = self._find(title)
        index = self.ctx.notebook
        index.refresh()
        index.touch(path)
        found: dict[str, dict[str, Any]] = {}
        for link in index.links(self._relative(path)):
            if link["target"] and link["target"] not in found:
                found[link["target"]] = {"note": link["target"], "exists": link["to"] is not None}
        return {"title": path.stem, "count": len(found), "links": list(found.values())}

    @tool
    def backlinks(self, title: str) -> dict[str, Any]:
        """The notes that link to a note, with the line that links."""
        path = self._find(title)
        index = self.ctx.notebook
        index.refresh()
        found = [
            {"note": Path(b["from"]).stem, "folder": Path(b["from"]).parent.as_posix(),
             "line": b["text"]}
            for b in index.backlinks(self._relative(path))
        ]  # fmt: skip
        return {"title": path.stem, "count": len(found), "linked_from": found[:100]}

    @tool
    async def rename(self, title: str, new_title: str) -> dict[str, Any]:
        """Rename a note. Links to it in other notes are changed to the new name too (each
        keeps its earlier version); asks first when other notes change."""
        path = self._find(title)
        new = self._file(new_title)
        if new.name == path.name:
            raise ToolError(f"the note is already called '{path.stem}'")
        planned = plan_rename(self.ctx.notebook, path, new)
        others = planned.others()
        if others:
            names = ", ".join(sorted(p.stem for p in others)[:10])
            question = (
                f"Rename the note '{path.stem}' to '{new.stem}' and change the links to it in "
                f"{len(others)} other notes ({names})? Each keeps its earlier version."
            )
            if not await self.ctx.ui.confirm(question):
                raise ToolError("not confirmed, nothing was renamed")
        changed = carry_out_rename(self.ctx.notebook, self.ctx.history, planned)
        return {"renamed": path.stem, "to": new.stem, "links_changed_in": [p.stem for p in changed]}

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
        """Delete a note: it goes to the trash (notes.restore_deleted brings it back).
        Asks first."""
        path = self._find(title)
        if not await self.ctx.ui.confirm(f"Delete the note '{path.stem}'?"):
            raise ToolError("not confirmed, the note is kept")
        self.ctx.history.trash(path, "notes.delete")
        return {"deleted": path.stem, "note": "in the trash: notes.restore_deleted brings it back"}

    @tool
    def trash(self) -> dict[str, Any]:
        """Deleted notes that can still come back (newest first)."""
        found = self.ctx.history.deleted(self.folder)
        return {
            "count": len(found),
            "notes": [{"title": Path(v.path).stem, "deleted": v.when} for v in found[:100]],
        }

    @tool
    def restore_deleted(self, title: str) -> dict[str, Any]:
        """Bring a deleted note back from the trash, as it was."""
        wanted = title.strip().casefold()
        found = self.ctx.history.deleted(self.folder)
        exact = [v for v in found if Path(v.path).stem.casefold() == wanted]
        matches = exact or [v for v in found if wanted in Path(v.path).stem.casefold()]
        if not matches:
            names = ", ".join(sorted({Path(v.path).stem for v in found})[:10]) or "it is empty"
            raise ToolError(f"no deleted note called '{title}' in the trash ({names})")
        if len({v.path for v in matches}) > 1:
            names = ", ".join(sorted({Path(v.path).stem for v in matches})[:10])
            raise ToolError(f"'{title}' matches several deleted notes: {names}")
        path = Path(matches[0].path)
        self.ctx.history.restore_deleted(path)
        return {"restored": path.stem, "file": str(path)}
