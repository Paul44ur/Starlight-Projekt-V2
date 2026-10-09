"""Kill switch. Checked by the dispatcher before every tool call.

Layers:
  * a global hotkey (default Ctrl+Alt+J, needs the optional ``pynput`` extra)
  * a STOP file, created by ``starlight kill`` and polled while the core runs
  * a WhatsApp ``/kill`` command from your own number (Step 10), via ``trigger()``

On trigger, running tasks are cancelled, cleanup callbacks registered by modules run
(release keyboard and mouse, close browser sessions), and the reason goes to the audit log.
The STOP file is the source of truth across processes: the switch stays engaged until the
file is removed (``starlight resume`` from any terminal, ``/resume``, or ``reset()``), and
every running process then releases on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

from starlight.core.logging import AuditLog, get_logger

type Cleanup = Callable[[], Awaitable[None] | None]


class KillSwitchEngaged(Exception):
    """Raised when an action is attempted while the kill switch is engaged."""


class KillSwitch:
    def __init__(self, stop_file: Path, audit: AuditLog | None = None) -> None:
        self.stop_file = stop_file
        self._audit = audit or AuditLog()
        self._log = get_logger("killswitch")
        self._fired = False
        self._file_backed = False  # the STOP file was written/seen when the switch fired
        self._reason: str | None = None
        self._cleanups: dict[str, list[Cleanup]] = {}
        # Tracked tasks, with how many guards each is inside: an agent run started by a tool
        # (automations.run_now) guards the same task again, and leaving it must not untrack
        # the outer run.
        self._tasks: dict[asyncio.Task[Any], int] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._hotkey_listener: Any = None
        self._pending: set[asyncio.Future[Any]] = set()

    # -- state -------------------------------------------------------------------------

    @property
    def engaged(self) -> bool:
        self._sync()
        return self._fired or self.stop_file.exists()

    def _sync(self) -> None:
        """Another process removed the STOP file (`starlight resume`): release here too."""
        if self._fired and self._file_backed and not self.stop_file.exists():
            with self._lock:
                if not (self._fired and self._file_backed):
                    return
                self._fired = False
                self._file_backed = False
                self._reason = None
            self._audit.record("killswitch.reset", source="stop_file_removed")
            self._log.info("kill switch released (STOP file removed)")

    @property
    def reason(self) -> str | None:
        if self._reason:
            return self._reason
        if self.stop_file.exists():
            return self.stop_file.read_text(encoding="utf-8").strip() or "STOP file present"
        return None

    def check(self) -> None:
        """Raise KillSwitchEngaged if the switch is engaged. Call before every action."""
        self._sync()
        if self._fired:
            raise KillSwitchEngaged(self._reason or "kill switch engaged")
        if self.stop_file.exists():
            self._fire(self.reason or "STOP file present", source="stop_file")
            raise KillSwitchEngaged(self._reason or "kill switch engaged")

    def trigger(self, reason: str = "manual", source: str = "api") -> None:
        """Engage the switch from any thread: write the STOP file, cancel, clean up."""
        # Claim first, then write: the watcher must not see the new file and fire a second
        # time with a generic reason before this one is recorded.
        with self._lock:
            if self._fired:
                return
            self._fired = True
            self._reason = reason
        if self._write_stop_file(reason):
            with self._lock:
                self._file_backed = self._fired
        # else: still stop this process; only reset() releases it then
        self._announce(reason, source)

    def _write_stop_file(self, reason: str) -> bool:
        """Atomically, so other processes never read a half-written file. On Windows a
        reader holding the file open can briefly block the replace: retry a few times."""
        temp = self.stop_file.with_name(f"{self.stop_file.name}.{os.getpid()}.tmp")
        for attempt in range(5):
            try:
                self.stop_file.parent.mkdir(parents=True, exist_ok=True)
                temp.write_text(f"{reason}\n", encoding="utf-8")
                os.replace(temp, self.stop_file)
                return True
            except OSError as exc:
                if attempt == 4:
                    self._log.error("could not write the STOP file", error=str(exc))
                time.sleep(0.02)
        with contextlib.suppress(OSError):
            temp.unlink()
        return False

    def reset(self) -> None:
        """Disengage. Removes the STOP file."""
        with contextlib.suppress(FileNotFoundError):
            self.stop_file.unlink()
        with self._lock:
            self._fired = False
            self._file_backed = False
            self._reason = None
        self._audit.record("killswitch.reset")
        self._log.info("kill switch reset")

    # -- what a trigger stops ----------------------------------------------------------

    def add_cleanup(self, callback: Cleanup, owner: str = "core") -> None:
        self._cleanups.setdefault(owner, []).append(callback)

    def remove_cleanups(self, owner: str) -> None:
        self._cleanups.pop(owner, None)

    @contextlib.contextmanager
    def guard(self, task: asyncio.Task[Any] | None = None) -> Iterator[None]:
        """Track a task (default: the current one) so a trigger cancels it."""
        task = task or asyncio.current_task()
        if task is None:
            yield
            return
        self._loop = task.get_loop()
        self._tasks[task] = self._tasks.get(task, 0) + 1
        try:
            yield
        finally:
            left = self._tasks.get(task, 1) - 1
            if left > 0:
                self._tasks[task] = left
            else:
                self._tasks.pop(task, None)

    def _fire(self, reason: str, source: str) -> None:
        """Engaged by a STOP file that appeared (another process, or `starlight kill`)."""
        with self._lock:
            if self._fired:
                return
            self._fired = True
            self._file_backed = self.stop_file.exists()
            self._reason = reason
        self._announce(reason, source)

    def _announce(self, reason: str, source: str) -> None:
        """Audit, then cancel running work and run cleanups (on the event loop's thread)."""
        self._audit.record("killswitch.triggered", reason=reason, source=source)
        self._log.warning("kill switch triggered", reason=reason, source=source)

        loop = self._loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if loop is not None and running is not loop and not loop.is_closed():
            loop.call_soon_threadsafe(self._stop_everything)
        else:
            self._stop_everything()

    def _stop_everything(self) -> None:
        try:
            current = asyncio.current_task()
        except RuntimeError:  # no running loop (a thread)
            current = None
        for task in list(self._tasks):
            # The task running now found the switch itself (check() raises in it): a
            # cancel on top would stay pending and hit its next await after the kill was
            # already handled.
            if not task.done() and task is not current:
                task.cancel(msg="kill switch")
        for owner, callbacks in list(self._cleanups.items()):
            for callback in callbacks:
                try:
                    result = callback()
                    if inspect.isawaitable(result):
                        future = asyncio.ensure_future(result)
                        self._pending.add(future)
                        future.add_done_callback(self._pending.discard)
                except Exception:
                    self._log.exception("kill-switch cleanup failed", owner=owner)

    # -- watchers ----------------------------------------------------------------------

    async def watch(self, interval: float = 0.2) -> None:
        """Poll for the STOP file so `starlight kill` from another terminal stops a run."""
        self._loop = asyncio.get_running_loop()
        while True:
            self._sync()
            if not self._fired and self.stop_file.exists():
                self._fire(self.reason or "STOP file present", source="stop_file")
            await asyncio.sleep(interval)

    def start_hotkey(self, combo: str) -> bool:
        """Listen for a global hotkey. Returns False if pynput is not installed or no
        desktop session is available."""
        try:
            from pynput import keyboard
        except Exception:
            self._log.warning("hotkey unavailable: install the 'hotkey' extra (pynput)")
            return False
        try:
            listener = keyboard.GlobalHotKeys(
                {combo: lambda: self.trigger("hotkey " + combo, source="hotkey")}
            )
            listener.start()
        except Exception:
            self._log.exception("hotkey listener failed to start", combo=combo)
            return False
        self._hotkey_listener = listener
        self._log.info("hotkey listening", combo=combo)
        return True

    def stop_hotkey(self) -> None:
        if self._hotkey_listener is not None:
            self._hotkey_listener.stop()
            self._hotkey_listener = None
