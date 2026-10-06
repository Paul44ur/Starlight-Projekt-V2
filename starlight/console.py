"""Reading from the terminal.

* a block of pasted lines is one request, not one request per line
* a question (allow this action? a 2FA code?) is answered only by what is typed after it
  appears: text typed or pasted earlier is dropped, so it can never say "yes" by accident

Both only apply in an interactive terminal; piped input is read line by line as before.
"""

from __future__ import annotations

import sys
import time

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
