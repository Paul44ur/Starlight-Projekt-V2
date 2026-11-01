"""The one safe way to change a note, a journal day, a recipe or a memory file.

``write_note`` and ``append_note``:
1. refuse if the file changed since it was read (``expected``: the ``stamp`` taken then),
   so another program or another browser tab is never silently overwritten;
2. keep the file as it was (History.save), so the change can be undone;
3. write the new content as a whole (a temporary file, then replaced in one step), so a
   crash or a power cut leaves the old file or the new one, never half of it;
4. tell the history's listeners (the notebook index updates that note at once).

Line endings follow the platform for new text, as the modules' ``write_text`` did before;
appending keeps the file's own (CRLF stays CRLF) and its bytes exactly as they were.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from starlight.core.fsutil import write_atomic_bytes
from starlight.core.tools import ToolError
from starlight.notebook.history import History, HistoryError

MISSING = "missing"  # the stamp of a file that does not exist (yet)


class NoteConflict(ToolError):
    """The file changed since it was read: it was not overwritten."""


@dataclass(frozen=True)
class Written:
    stamp: str  # the file's stamp now: what the next change should expect
    version: int | None  # the earlier version kept (None: the file was new, or versions off)
    new: bool = False  # the file did not exist before

    def changed(self, path: Path) -> dict[str, object]:
        """What tools return about a change, so the chat can offer to undo it: the
        version to put back, or (for a new file) that undoing means deleting it."""
        return {"path": str(path), "title": path.stem, "version": self.version, "new": self.new}


def stamp(path: Path) -> str:
    """What a file looks like now (modification time and size), to notice a change."""
    try:
        info = path.stat()
    except FileNotFoundError:
        return MISSING
    return f"{info.st_mtime_ns}-{info.st_size}"


def _check(path: Path, expected: str | None) -> None:
    if expected is not None and stamp(path) != expected:
        raise NoteConflict(
            f"{path.name} was changed meanwhile (in another program or tab); read it again "
            "before changing it, so that change is not lost"
        )


def _encode(text: str, newline: bytes) -> bytes:
    return text.replace("\r\n", "\n").encode("utf-8").replace(b"\n", newline)


def write_note(
    path: Path,
    text: str,
    reason: str,
    history: History | None,
    *,
    expected: str | None = None,
    min_gap: float = 0.0,
) -> Written:
    """Replace the file's content with ``text`` (UTF-8), safely (see the module's notes)."""
    _check(path, expected)
    new = not path.exists()
    version = history.save(path, reason, min_gap=min_gap) if history is not None else None
    write_atomic_bytes(path, _encode(text, os.linesep.encode()))
    if history is not None:
        history.changed(path)  # (the index reads it again)
    return Written(stamp(path), version, new)


def append_note(
    path: Path,
    text: str,
    reason: str,
    history: History | None,
    *,
    expected: str | None = None,
) -> Written:
    """Add ``text`` to the end of the file (made if missing), safely. The file's earlier
    bytes stay exactly as they were, and the new text uses its kind of line endings."""
    _check(path, expected)
    try:
        existing = path.read_bytes()
    except FileNotFoundError:
        existing = b""
    newline = b"\r\n" if b"\r\n" in existing else b"\n" if existing else os.linesep.encode()
    version = history.save(path, reason) if history is not None and existing else None
    write_atomic_bytes(path, existing + _encode(text, newline))
    if history is not None:
        history.changed(path)
    return Written(stamp(path), version, new=not existing)


def undo_change(history: History, change: dict[str, object]) -> str:
    """Undo a change a tool reported (``Written.changed``): a new file goes to the trash, a
    changed one gets its earlier version back. The file as it is now is kept first, so the
    undo can be undone too. What it says it did, in plain words."""
    path = Path(str(change.get("path") or ""))
    if change.get("new"):
        if path.is_file():
            history.trash(path, "undo")
        return f"{path.stem} removed (it is in the trash)"
    version = change.get("version")
    if not isinstance(version, int):
        raise HistoryError("no earlier version was kept (keeping versions is switched off)")
    history.restore(path, version)
    return f"{path.stem} is back as it was"
