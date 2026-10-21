"""Only one Starlight process runs the background parts: the WhatsApp webhook, the server
watcher, the heartbeat the viewer reads. Others (``starlight run`` while ``starlight serve``
runs) answer requests but leave those alone.

The lock is an OS file lock held for the life of the process, so it is released even if
the process crashes. A process that did not get it keeps trying and takes over when the
holder exits.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import IO


class CoreLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: IO[str] | None = None

    @property
    def held(self) -> bool:
        return self._file is not None

    def acquire(self) -> bool:
        """Try once, without waiting. True if this process now holds the lock."""
        if self._file is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115 - held for life
        try:
            if sys.platform == "win32":
                import msvcrt

                # msvcrt.locking locks bytes from the current position, and "a+" opens at
                # the end: every process must lock (and unlock) the same byte, byte 0.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        if self._file is None:
            return
        with contextlib.suppress(OSError):
            if sys.platform == "win32":
                import msvcrt

                self._file.seek(0)  # the byte acquire() locked
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None


def core_running(path: Path) -> bool:
    """Is another process holding the lock (i.e. a core is running)?"""
    probe = CoreLock(path)
    if probe.acquire():
        probe.release()
        return False
    return True
