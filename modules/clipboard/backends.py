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

    @staticmethod
    def _run(command: list[str], text: str | None = None) -> subprocess.CompletedProcess[str]:
        # UTF-8 on both sides, whatever the code page of this PC is (umlauts stay umlauts)
        try:
            return subprocess.run(
                command,
                input=text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise ClipboardUnavailable(f"the clipboard program did not answer: {exc}") from exc

    def get(self) -> str:
        result = self._run(self.get_cmd)
        if result.returncode != 0:
            raise ClipboardUnavailable(result.stderr.strip() or "could not read the clipboard")
        return result.stdout.removeprefix("\ufeff")  # (in case the program starts with a BOM)

    def set(self, text: str) -> None:
        result = self._run(self.set_cmd, text)
        if result.returncode != 0:
            raise ClipboardUnavailable(result.stderr.strip() or "could not set the clipboard")


# PowerShell reads and writes the PC's old console code page (cp850 on a German Windows)
# unless told otherwise.
_UTF8 = "[Console]::InputEncoding=[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "


def detect() -> Backend | None:
    """A clipboard backend for this computer, or None if there is no usable one."""
    if sys.platform == "win32":
        return Backend(
            "powershell",
            ["powershell", "-NoProfile", "-Command", _UTF8 + "Get-Clipboard -Raw"],
            # $input: the text Python writes to stdin, line by line; Set-Clipboard joins them
            ["powershell", "-NoProfile", "-Command", _UTF8 + "$input | Set-Clipboard"],
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
