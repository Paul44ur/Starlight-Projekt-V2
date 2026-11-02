"""Notes, the journal, recipes and memory as a notebook (docs/plan-notes-app.md).

This package grows block by block. So far:

* ``db``: opening a SQLite file and bringing its tables up to date.
* ``history``: the earlier versions of files Starlight changes, to compare and restore
  them, and the trash for deleted ones.
* ``write``: the one safe way to change such a file: refuse if it changed since it was
  read, keep a version, write it as a whole; and undoing such a change (``undo_change``,
  the chat's Undo chip and the terminal's /undo).
* ``markdown``, ``links``, ``index``, ``rename``, ``templates``: reading notes, their links,
  the index, renaming with links that follow, templates.
* ``tasks``: tasks in notes as to-dos (their ids, ticking them in the note).
"""

from __future__ import annotations

from starlight.notebook.history import History, Version
from starlight.notebook.write import (
    NoteConflict,
    Written,
    append_note,
    stamp,
    undo_change,
    write_note,
)

__all__ = [
    "History",
    "NoteConflict",
    "Version",
    "Written",
    "append_note",
    "stamp",
    "undo_change",
    "write_note",
]
