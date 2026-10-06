"""Reading and writing the system clipboard, using whatever the OS provides.

Windows uses PowerShell, macOS uses pbcopy/pbpaste, Linux uses wl-clipboard, xclip or
xsel. If none is available (a server with no desktop), the clipboard is reported as
unavailable rather than failing oddly.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass


class ClipboardUnavailable(Exception):
    pass


@dataclass
class Backend:
    name: str
    get_cmd: list[str]
    set_cmd: list[str]

    def get(self) -> str:
        result = subprocess.run(self.get_cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            raise ClipboardUnavailable(result.stderr.strip() or "could not read the clipboard")
        return result.stdout

    def set(self, text: str) -> None:
        result = subprocess.run(
            self.set_cmd, input=text, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            raise ClipboardUnavailable(result.stderr.strip() or "could not set the clipboard")


def detect() -> Backend | None:
    """A clipboard backend for this computer, or None if there is no usable one."""
    if sys.platform == "win32":
        return Backend(
            "powershell",
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
            ["powershell", "-NoProfile", "-Command", "$input | Set-Clipboard"],
        )
    if sys.platform == "darwin":
        return Backend("pbcopy", ["pbpaste"], ["pbcopy"])
    if shutil.which("wl-paste") and shutil.which("wl-copy"):
        return Backend("wl-clipboard", ["wl-paste", "--no-newline"], ["wl-copy"])
    if shutil.which("xclip"):
        sel = ["-selection", "clipboard"]
        return Backend("xclip", ["xclip", *sel, "-o"], ["xclip", *sel])
    if shutil.which("xsel"):
        return Backend("xsel", ["xsel", "-b", "-o"], ["xsel", "-b", "-i"])
    return None
