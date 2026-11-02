"""Boards: cards and arrows on an endless surface, kept as .canvas files (JSON Canvas, the
format Obsidian uses) in your Starlight folder. "What's on my project board?", "add the
article https://... to my reading board", "put 'call the bank' under the card about money".

A card is a text, a note (shown on the board), a web link or a group around other cards.
Every change keeps the board as it was (starlight.notebook: History, write_note), so it can
be undone like a note. The Notes page shows and edits boards (block F4).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.notebook import boards, write_note
from starlight.text import decode_text

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class Config(BaseModel):
    folder: str = "Boards"


class BoardsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    @property
    def root(self) -> Path:
        return self.ctx.output_dir

    @property
    def folder(self) -> Path:
        return self.root / (_BAD.sub("", self.config.folder).strip() or "Boards")

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def _new_file(self, name: str) -> Path:
        clean = _BAD.sub("", name.strip().removesuffix(boards.SUFFIX)).strip(" .")
        if not clean:
            raise ToolError("what should the board be called?")
        return self.folder / f"{clean[:80]}{boards.SUFFIX}"

    def _find(self, name: str) -> Path | None:
        """A board by its name ("Project"), a part of it, or its path in the Starlight
        folder ("Boards/Project.canvas"). None when there is none; an error when several
        boards match."""
        wanted = name.strip().replace("\\", "/").removesuffix(boards.SUFFIX).casefold()
        if not wanted:
            raise ToolError("which board?")
        found = boards.find(self.root)
        exact = [p for p in found if self._relative(p).casefold().removesuffix(".canvas") == wanted]
        exact = exact or [p for p in found if p.stem.casefold() == wanted]
        if len(exact) == 1:
            return exact[0]
        partly = exact or [p for p in found if wanted in p.stem.casefold()]
        if len(partly) == 1:
            return partly[0]
        if partly:
            names = ", ".join(self._relative(p) for p in partly[:10])
            raise ToolError(f"'{name}' matches several boards: {names}")
        return None

    def _read(self, path: Path) -> boards.Board:
        if path.stat().st_size > boards.MAX_BYTES:
            raise ToolError(f"{path.name} is over 5 MB; it is not read")
        return boards.load(decode_text(path.read_bytes()))

    @tool(name="list")
    def list_boards(self) -> dict[str, Any]:
        """Your boards: their names, where they are, and how many cards each has."""
        listed = []
        for path in boards.find(self.root):
            try:
                cards: Any = len(self._read(path).nodes)
            except (ToolError, OSError):
                cards = "unreadable"
            changed = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            listed.append(
                {"name": path.stem, "path": self._relative(path), "cards": cards,
                 "changed": changed}
            )  # fmt: skip
        return {"count": len(listed), "boards": listed}

    @tool
    def read(self, board: str) -> dict[str, Any]:
        """What is on a board: its cards (text, notes, links, groups; with the group each
        card is in) and the arrows between them."""
        path = self._find(board)
        if path is None:
            raise ToolError(f"there is no board called '{board}'; see boards.list")
        return {
            "board": path.stem,
            "path": self._relative(path),
            **boards.summary(self._read(path)),
        }

    @tool
    def add_card(
        self,
        board: str,
        text: str = "",
        note: str = "",
        url: str = "",
        group: str = "",
        under: str = "",
        label: str = "",
    ) -> dict[str, Any]:
        """Add a card to a board (a new board when there is none of that name). Give one of:
        `text` (words, Markdown), `note` (a note's name: the card shows it), `url` (a web
        link), `group` (a frame's label). `under`: a card's id or words from it, to put the
        new card below it with an arrow (named `label`)."""
        given = [(k, v) for k, v in (("text", text), ("file", note), ("link", url),
                                     ("group", group)) if v.strip()]  # fmt: skip
        if len(given) != 1:
            raise ToolError("give one of text, note, url or group for the card")
        kind, value = given[0]
        if kind == "file":
            index = self.ctx.notebook
            index.refresh()
            target = index.resolve(value)
            if target is None:
                raise ToolError(f"there is no note called '{value}'")
            value = target
        path = self._find(board) or self._new_file(board)
        board_now = self._read(path) if path.exists() else boards.Board()
        anchor = ""
        if under.strip():
            anchor = self._card(board_now, under)
        card = boards.add_card(board_now, kind, value, label=label, near=anchor)
        path.parent.mkdir(parents=True, exist_ok=True)
        written = write_note(path, boards.dump(board_now), "boards.add_card", self.ctx.history)
        return {
            "board": path.stem,
            "path": self._relative(path),
            "card": card["id"],
            "changed": written.changed(path),
        }

    @staticmethod
    def _card(board: boards.Board, words: str) -> str:
        """A card's id, by its id or by words from what it says (only one may match)."""
        if board.node(words.strip()):
            return words.strip()
        wanted = words.strip().casefold()
        matches = [n for n in board.nodes if wanted in boards.describe(n).casefold()]
        if len(matches) == 1:
            return str(matches[0]["id"])
        if matches:
            raise ToolError(f"'{words}' is on {len(matches)} cards; use a card's id (boards.read)")
        raise ToolError(f"no card on the board says '{words}'")
