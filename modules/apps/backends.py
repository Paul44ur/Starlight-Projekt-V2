"""How programs are found, started and their windows handled, per system.

Windows: Start-menu shortcuts (.lnk) are the program list, ``os.startfile`` opens them (and
documents and links) like a double-click, and windows are handled with the Win32 API through
ctypes - no extra packages. Linux: .desktop entries, xdg-open, and wmctrl for windows.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class AppsUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Program:
    name: str
    target: str  # the shortcut / .desktop file


@dataclass(frozen=True)
class Window:
    id: int
    title: str
    program: str = ""


class Backend(Protocol):
    def programs(self, extra: list[Path]) -> list[Program]: ...
    def start(self, program: Program) -> None: ...
    def open(self, target: str) -> None: ...
    def windows(self) -> list[Window]: ...
    def focus(self, window: Window) -> None: ...
    def show(self, window: Window, state: str) -> None: ...  # minimize | maximize | restore
    def close(self, window: Window) -> None: ...


def _scan(folders: list[Path], suffix: str) -> list[Path]:
    found: list[Path] = []
    for folder in folders:
        if folder.is_dir():
            found.extend(p for p in folder.rglob(f"*{suffix}") if p.is_file())
    return found


_SKIP = re.compile(r"uninstall|deinstall|readme|help|website|release notes", re.IGNORECASE)


# -- Windows -------------------------------------------------------------------------------


class WindowsBackend:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        # typed loosely: these names only exist on Windows
        self.ctypes: Any = ctypes
        self.wintypes: Any = wintypes
        self.user32: Any = self.ctypes.windll.user32

    def programs(self, extra: list[Path]) -> list[Program]:
        folders = [
            Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
            Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
            / "Microsoft/Windows/Start Menu/Programs",
            *extra,
        ]
        return [Program(p.stem, str(p)) for p in _scan(folders, ".lnk") if not _SKIP.search(p.stem)]

    def start(self, program: Program) -> None:
        _startfile(program.target)  # like a double-click

    def open(self, target: str) -> None:
        _startfile(target)

    def windows(self) -> list[Window]:
        found: list[Window] = []
        proto = self.ctypes.WINFUNCTYPE(
            self.wintypes.BOOL, self.wintypes.HWND, self.wintypes.LPARAM
        )

        def visit(hwnd: int, _: int) -> bool:
            if not self.user32.IsWindowVisible(hwnd) or self.user32.GetWindow(hwnd, 4):
                return True  # hidden, or owned by another window (dialogs, tool windows)
            length = self.user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = self.ctypes.create_unicode_buffer(length + 1)
                self.user32.GetWindowTextW(hwnd, buffer, length + 1)
                found.append(Window(int(hwnd), buffer.value, self._program(hwnd)))
            return True

        self.user32.EnumWindows(proto(visit), 0)
        return [w for w in found if w.title not in ("Program Manager", "Settings")]

    def _program(self, hwnd: int) -> str:
        pid = self.wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, self.ctypes.byref(pid))
        try:
            import psutil

            return str(psutil.Process(pid.value).name())
        except Exception:
            return ""

    def focus(self, window: Window) -> None:
        if self.user32.IsIconic(window.id):
            self.user32.ShowWindow(window.id, 9)  # SW_RESTORE
        # Windows only lets the foreground app hand over focus; a no-op key press makes
        # this process count as the one the user last used.
        self.user32.keybd_event(0x12, 0, 0, 0)  # Alt down
        self.user32.keybd_event(0x12, 0, 2, 0)  # Alt up
        self.user32.SetForegroundWindow(window.id)

    def show(self, window: Window, state: str) -> None:
        self.user32.ShowWindow(window.id, {"minimize": 6, "maximize": 3, "restore": 9}[state])

    def close(self, window: Window) -> None:
        self.user32.PostMessageW(window.id, 0x0010, 0, 0)  # WM_CLOSE: the app may ask to save


# -- Linux ---------------------------------------------------------------------------------


class LinuxBackend:
    def programs(self, extra: list[Path]) -> list[Program]:
        data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
        folders = [Path.home() / ".local/share/applications"]
        folders += [Path(d) / "applications" for d in data_dirs] + extra
        found = []
        for path in _scan(folders, ".desktop"):
            entry = _desktop_entry(path)
            if entry.get("Name") and entry.get("Exec") and entry.get("NoDisplay") != "true":
                found.append(Program(entry["Name"], str(path)))
        return found

    def start(self, program: Program) -> None:
        entry = _desktop_entry(Path(program.target))
        command = [p for p in shlex.split(entry.get("Exec", "")) if not re.fullmatch(r"%\w", p)]
        if not command:
            raise AppsUnavailable(f"{program.name} has no command to run")
        subprocess.Popen(command, start_new_session=True)

    def open(self, target: str) -> None:
        opener = shutil.which("xdg-open")
        if opener is None:
            raise AppsUnavailable("xdg-open is not installed")
        subprocess.Popen([opener, target], start_new_session=True)

    def _wmctrl(self, *args: str) -> str:
        tool = shutil.which("wmctrl")
        if tool is None:
            raise AppsUnavailable("window control needs 'wmctrl' (sudo apt install wmctrl)")
        return _run([tool, *args])

    def windows(self) -> list[Window]:
        found = []
        for line in self._wmctrl("-l").splitlines():
            parts = line.split(None, 3)
            if len(parts) == 4:
                found.append(Window(int(parts[0], 16), parts[3]))
        return found

    def focus(self, window: Window) -> None:
        self._wmctrl("-i", "-a", hex(window.id))

    def show(self, window: Window, state: str) -> None:
        if state == "minimize":
            tool = shutil.which("xdotool")
            if tool is None:
                raise AppsUnavailable("minimizing needs 'xdotool'")
            _run([tool, "windowminimize", str(window.id)])
            return
        action = "add" if state == "maximize" else "remove"
        self._wmctrl("-i", "-r", hex(window.id), "-b", f"{action},maximized_vert,maximized_horz")

    def close(self, window: Window) -> None:
        self._wmctrl("-i", "-c", hex(window.id))


def _run(command: list[str]) -> str:
    """Run a window-control tool and return its output. A failure (no display, a window that
    is gone) or a hang is an error, not a silent success."""
    program = Path(command[0]).name
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AppsUnavailable(f"{program} did not answer") from exc
    if done.returncode != 0:
        raise AppsUnavailable(f"{program} failed: {done.stderr.strip() or done.returncode}")
    return done.stdout


def _desktop_entry(path: Path) -> dict[str, str]:
    entry: dict[str, str] = {}
    in_main = False
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines()
    except OSError:
        return entry
    for line in lines:
        if line.startswith("["):
            in_main = line.strip() == "[Desktop Entry]"
        elif in_main and "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            entry.setdefault(key.strip(), value.strip())
    return entry


def _startfile(target: str) -> None:
    """Open like a double-click in Explorer (Windows only)."""
    system: Any = os
    system.startfile(target)


def detect() -> Backend:
    if sys.platform == "win32":
        return WindowsBackend()
    return LinuxBackend()
