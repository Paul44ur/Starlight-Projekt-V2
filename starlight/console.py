"""Reading from the terminal.

* a block of pasted lines is one request, not one request per line
* a question (allow this action? a 2FA code?) is answered only by what is typed after it
  appears: text typed or pasted earlier is dropped, so it can never say "yes" by accident

Both only apply in an interactive terminal; piped input is read line by line as before.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future

PASTE_GAP = 0.05  # seconds: lines that arrive closer together than this were pasted


def interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


if sys.platform == "win32":
    import msvcrt

    def _pending() -> bool:
        return msvcrt.kbhit()

    def _drop() -> None:
        for _ in range(100_000):  # a huge paste is still dropped in bounded time
            if not msvcrt.kbhit():
                return
            msvcrt.getwch()

    _ERRORS: tuple[type[Exception], ...] = (OSError, ValueError)

else:
    import select
    import termios

    def _pending() -> bool:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
        return bool(ready)

    def _drop() -> None:
        termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)

    _ERRORS = (OSError, ValueError, termios.error)


def read_request(prompt: str) -> str:
    """One line, plus the lines pasted together with it."""
    lines = [input(prompt)]
    if not interactive():
        return lines[0]
    deadline = time.monotonic() + PASTE_GAP
    while time.monotonic() < deadline:
        if _pending():
            lines.append(input())
            deadline = time.monotonic() + PASTE_GAP
        else:
            time.sleep(0.005)
    return "\n".join(lines)


def drop_typeahead() -> bool:
    """Throw away anything typed or pasted that was not read yet. True if there was some."""
    if not interactive():
        return False
    try:
        pending = _pending()
        _drop()
    except _ERRORS:
        return False
    return pending


def answer(prompt: str) -> str:
    """Read the answer to a question that was just asked."""
    if drop_typeahead():
        print("\n(Text typed before this question was ignored. Please answer it now.)")
    return input(prompt)


class LineReader:
    """Reads input in a background thread so the event loop keeps running while a prompt
    waits (the kill-switch watcher, reminders and the WhatsApp bridge carry on).

    At most one read is in flight. If a question is abandoned (Ctrl+C at a confirmation),
    its read is still waiting for a line; the next prompt takes that read over instead of
    starting a second one, which would otherwise swallow the user's next line. The thread
    is a daemon, so leaving Starlight never waits for Enter (see leave_if_reading)."""

    def __init__(self) -> None:
        self._pending: Future[str] | None = None

    def waiting(self) -> bool:
        """True while a read is still waiting for a line."""
        return self._pending is not None and not self._pending.done()

    async def read(self, reader: Callable[[str], str], prompt: str) -> str:
        if self._pending is not None and not self._pending.done():
            print(prompt, end="", flush=True)  # the old read is waiting: show the new prompt
            future = self._pending
        else:
            future = Future()
            future.set_running_or_notify_cancel()  # running: Ctrl+C cannot cancel it
            self._pending = future
            threading.Thread(target=_run, args=(future, reader, prompt), daemon=True).start()
        try:
            return await asyncio.wrap_future(future)
        finally:
            if future.done():
                self._pending = None


def _run(future: Future[str], reader: Callable[[str], str], prompt: str) -> None:
    try:
        future.set_result(reader(prompt))
    except BaseException as exc:  # EOFError, KeyboardInterrupt: hand them to the caller
        future.set_exception(exc)


lines = LineReader()


def leave_if_reading(code: int) -> None:
    """Exit now when a prompt that Ctrl+C abandoned is still reading in its thread. The C
    library holds the lock of stdin for that read, so a normal exit would hang until Enter."""
    if not (lines.waiting() and interactive()):
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (OSError, ValueError):
            pass
    logging.shutdown()
    os._exit(code)
