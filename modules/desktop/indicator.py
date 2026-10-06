"""A small always-on-top banner shown while Starlight controls mouse and keyboard."""

from __future__ import annotations

import contextlib
import queue
import threading
import time
from typing import Any

TEXT = "Starlight is controlling mouse and keyboard  ·  {hotkey} or a screen corner stops it"


class Indicator:
    def __init__(self, enabled: bool = True, hotkey: str = "Ctrl+Alt+K", linger: float = 2.0):
        self.enabled = enabled
        self.text = TEXT.format(hotkey=hotkey)
        self.linger = linger
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self.visible = False

    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception:
            self.enabled = False
            return
        try:
            root = tk.Tk()
        except Exception:  # no display
            self.enabled = False
            return
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        with contextlib.suppress(Exception):
            root.attributes("-alpha", 0.9)
        label = tk.Label(
            root,
            text=self.text,
            bg="#b00020",
            fg="white",
            font=("Segoe UI", 11, "bold"),
            padx=14,
            pady=6,
        )
        label.pack()
        root.update_idletasks()
        x = (root.winfo_screenwidth() - root.winfo_width()) // 2
        root.geometry(f"+{x}+8")
        root.withdraw()
        hide_at = [0.0]

        def poll() -> None:
            try:
                while True:
                    command = self._queue.get_nowait()
                    if command == "show":
                        root.deiconify()
                        root.lift()
                        hide_at[0] = 0.0
                    elif command == "hide":
                        hide_at[0] = time.monotonic() + self.linger
                    elif command == "now":
                        hide_at[0] = time.monotonic()
                    elif command == "quit":
                        root.destroy()
                        return
            except queue.Empty:
                pass
            if hide_at[0] and time.monotonic() >= hide_at[0]:
                root.withdraw()
                hide_at[0] = 0.0
            root.after(100, poll)

        root.after(100, poll)
        root.mainloop()

    def _send(self, command: str) -> None:
        if not self.enabled:
            return
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="indicator", daemon=True)
            self._thread.start()
        self._queue.put(command)

    def show(self) -> None:
        self.visible = True
        self._send("show")

    def hide(self, immediately: bool = False) -> None:
        self.visible = False
        self._send("now" if immediately else "hide")

    def close(self, *_: Any) -> None:
        self.visible = False
        if self._thread is not None and self._thread.is_alive():
            self._queue.put("quit")
