"""An always-on-top banner at the top of the screen: one window per process.

It lives here in the core, not in a module, because modules are re-imported when they
reload, and the window must outlive that (see ``Banner``).
"""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from typing import Any


class Banner:
    """One banner window per process, owned by one thread that lives as long as the process.

    Tcl aborts the whole process when an interpreter is freed by a thread other than the
    one that created it ("Tcl_AsyncDelete: async handler deleted by the wrong thread"),
    and starting a new interpreter in a new thread after an earlier one ended can fail
    too. So the window is created once, by this thread, and never freed: closing only
    hides it, and no Tk object is ever referenced outside ``_run``.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.available = True  # False: no tkinter or no display

    def show(self, text: str) -> None:
        self._send("show", text, start=True)

    def hide(self, after: float = 0.0) -> None:
        """Hide the window ``after`` seconds from now, unless it is shown again first."""
        self._send("hide", after)

    def _send(self, command: str, value: Any, start: bool = False) -> None:
        with self._lock:
            if not self.available:
                return
            if self._thread is None:
                if not start:  # nothing on screen yet: nothing to hide
                    return
                self._thread = threading.Thread(target=self._run, name="banner", daemon=True)
                self._thread.start()
            self._queue.put((command, value))

    def _run(self) -> None:
        try:
            import tkinter as tk

            root = tk.Tk()
        except Exception:  # no tkinter or no display
            self.available = False
            return
        try:
            root.overrideredirect(True)
            root.protocol("WM_DELETE_WINDOW", root.withdraw)  # Alt+F4 hides, not ends
            root.attributes("-topmost", True)
            with contextlib.suppress(Exception):
                root.attributes("-alpha", 0.9)
            label = tk.Label(
                root, bg="#b00020", fg="white", font=("Segoe UI", 11, "bold"), padx=14, pady=6
            )
            label.pack()
            root.withdraw()
            # The hide deadline travels from one poll to the next in a one-item list (a
            # mutable box for the after() callbacks); 0.0 means no hide is pending.
            root.after(100, self._poll, root, label, [0.0])
            root.mainloop()
        finally:
            self.available = False  # never started again from another thread

    def _poll(self, root: Any, label: Any, hide_at: list[float]) -> None:
        try:
            while True:
                command, value = self._queue.get_nowait()
                if command == "show":
                    if label.cget("text") != value:
                        label.configure(text=value)
                        root.update_idletasks()
                        x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
                        root.geometry(f"+{x}+8")
                    root.deiconify()
                    root.lift()
                    hide_at[0] = 0.0
                elif command == "hide":
                    hide_at[0] = time.monotonic() + value
        except queue.Empty:
            pass
        if hide_at[0] and time.monotonic() >= hide_at[0]:
            root.withdraw()
            hide_at[0] = 0.0
        root.after(100, self._poll, root, label, hide_at)


_BANNER = Banner()


def banner() -> Banner:
    """The process's banner."""
    return _BANNER
