"""Saving files on Windows without losing data to a passing lock.

Antivirus, OneDrive, the search indexer or a backup program open a file for a moment
after it changes, and during that moment Windows refuses to replace or delete it
(``PermissionError``, "being used by another process"). A save that gives up at once
loses a conversation turn or a list change. These helpers try again for about two
seconds - long enough for the other program to finish, short enough not to hang a chat.

Everything here writes a temporary file first and then replaces the real one, so a crash
(or a power cut) never leaves half a file behind.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

# Seconds to wait before each new attempt (so 7 attempts, about 1.8 s in all).
DELAYS = (0.05, 0.1, 0.2, 0.4, 0.5, 0.5)


def is_transient(error: OSError) -> bool:
    """Whether trying again may help. Only on Windows is a PermissionError usually a
    file that is busy for a moment; elsewhere it means "not allowed" and stays so."""
    return sys.platform == "win32" and isinstance(error, PermissionError)


def retry_on_lock[T](action: Callable[[], T], delays: tuple[float, ...] | None = None) -> T:
    """Run ``action``; if it fails because a file is busy, wait and try again."""
    waits = DELAYS if delays is None else delays
    for wait in waits:
        try:
            return action()
        except OSError as error:
            if not is_transient(error):
                raise
        time.sleep(wait)
    return action()  # the last attempt raises its error as it is


def replace_file(source: Path | str, target: Path | str) -> None:
    """``os.replace`` that waits out a lock on the target."""
    retry_on_lock(lambda: os.replace(source, target))


def write_atomic(
    path: Path,
    text: str,
    encoding: str = "utf-8",
    newline: str | None = None,
) -> None:
    """Write ``text`` to ``path`` as a whole: to a temporary file next to it first, then
    replaced in one step. Other programs reading the file see the old or the new text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        retry_on_lock(lambda: temp.write_text(text, encoding=encoding, newline=newline))
        replace_file(temp, path)
    finally:
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
