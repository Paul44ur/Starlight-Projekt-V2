"""Crashes: when Starlight ends because of an error nobody caught - or Python itself fails -
what happened is written to logs/crash.log (and logs/fault.log), on this PC only.

With ``diagnostics: crash_reports: true`` Starlight says so the next time it starts, and
offers a support bundle (with passwords and keys taken out) to send for help. Nothing is
ever sent anywhere by itself. Being stopped (closing the window, the kill switch) is not
a crash and is not reported.
"""

from __future__ import annotations

import contextlib
import faulthandler
import json
import sys
import threading
import time
import traceback
from pathlib import Path
from types import TracebackType
from typing import Any, TextIO

CRASH_LOG = "crash.log"
FAULT_LOG = "fault.log"
SEEN = "crash.seen.json"
_fault_file: TextIO | None = None  # kept open: Python writes to it as it fails


def record(logs: Path, text: str) -> None:
    """Add one crash (a traceback) to logs/crash.log."""
    with contextlib.suppress(OSError):
        logs.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with (logs / CRASH_LOG).open("a", encoding="utf-8") as out:
            out.write(f"==== {stamp} (process {_pid()})\n{text.rstrip()}\n")


def _pid() -> int:
    import os

    return os.getpid()


def watch(logs: Path) -> None:
    """Write uncaught errors (in any thread) and Python's own failures to the logs."""
    global _fault_file
    before = sys.excepthook

    def on_error(kind: type[BaseException], value: BaseException, tb: TracebackType | None) -> None:
        if not issubclass(kind, KeyboardInterrupt | SystemExit):
            record(logs, "".join(traceback.format_exception(kind, value, tb)))
        before(kind, value, tb)

    thread_before = threading.excepthook

    def on_thread_error(args: Any) -> None:
        if args.exc_type is not SystemExit:
            name = args.thread.name if args.thread is not None else "?"
            text = "".join(
                traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
            )
            record(logs, f"in the thread {name}:\n{text}")
        thread_before(args)

    sys.excepthook = on_error
    threading.excepthook = on_thread_error
    with contextlib.suppress(OSError, RuntimeError, ValueError):
        logs.mkdir(parents=True, exist_ok=True)
        if _fault_file is None:
            _fault_file = (logs / FAULT_LOG).open("a", encoding="utf-8")
        faulthandler.enable(file=_fault_file)


def _sizes(logs: Path) -> dict[str, int]:
    sizes = {}
    for name in (CRASH_LOG, FAULT_LOG):
        with contextlib.suppress(OSError):
            sizes[name] = (logs / name).stat().st_size
    return sizes


def unseen(logs: Path) -> str | None:
    """What crashed since it was last told (the last line of the newest crash), or None."""
    try:
        seen = json.loads((logs / SEEN).read_text("utf-8"))
    except (OSError, ValueError):
        seen = {}
    grown = [name for name, size in _sizes(logs).items() if size > int(seen.get(name, 0))]
    if not grown:
        return None
    if CRASH_LOG in grown:
        text = (logs / CRASH_LOG).read_text("utf-8", errors="replace")
        newest = text.rsplit("==== ", 1)[-1].strip().splitlines()
        when = newest[0].split(" (")[0] if newest else ""
        what = newest[-1].strip() if len(newest) > 1 else "an error"
        return f"{what} ({when})" if when else what
    return "Python itself failed (see logs/fault.log)"


def mark_seen(logs: Path) -> None:
    with contextlib.suppress(OSError):
        (logs / SEEN).write_text(json.dumps(_sizes(logs)), encoding="utf-8")


def message(what: str) -> str:
    return (
        f"Starlight crashed the last time it ran: {what}. A support bundle helps find out "
        "why - logs and settings with passwords and keys taken out, kept on this PC until "
        "you send it to someone: in the Starlight folder, run `tools\\uv\\uv.exe run "
        "starlight support-bundle`. logs/crash.log has the details."
    )
