"""Showing a desktop notification, using whatever the OS provides.

Windows uses a PowerShell toast, macOS uses osascript, Linux uses notify-send. If none is
available (a server with no desktop), notifications are reported as unavailable rather than
failing oddly.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass

# A toast on Windows 10/11 through the built-in notification API (no extra PowerShell module).
# Built from short fragments so no source line is too long; adjacent strings with no "\n"
# between them join into one PowerShell line.
_MGR = "[Windows.UI.Notifications.ToastNotificationManager]"
_WINDOWS_TOAST = (
    "$ErrorActionPreference = 'Stop'\n"
    "[void][Windows.UI.Notifications.ToastNotificationManager, "
    "Windows.UI.Notifications, ContentType=WindowsRuntime]\n"
    f"$tpl = {_MGR}::GetTemplateContent(\n"
    "[Windows.UI.Notifications.ToastTemplateType]::ToastText02)\n"
    "$texts = $tpl.GetElementsByTagName('text')\n"
    "[void]$texts.Item(0).AppendChild("
    "$tpl.CreateTextNode($env:STARLIGHT_NOTIFY_TITLE))\n"
    "[void]$texts.Item(1).AppendChild("
    "$tpl.CreateTextNode($env:STARLIGHT_NOTIFY_MESSAGE))\n"
    "$toast = [Windows.UI.Notifications.ToastNotification]::new($tpl)\n"
    f"{_MGR}::CreateToastNotifier('Starlight').Show($toast)\n"
)


class NotifyUnavailable(Exception):
    pass


@dataclass
class Backend:
    name: str
    kind: str  # "windows", "mac" or "linux"

    def send(self, title: str, message: str, urgent: bool = False) -> None:
        if self.kind == "windows":
            self._run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_TOAST],
                env={"STARLIGHT_NOTIFY_TITLE": title, "STARLIGHT_NOTIFY_MESSAGE": message},
            )
        elif self.kind == "mac":
            script = f"display notification {_osa(message)} with title {_osa(title)}"
            self._run(["osascript", "-e", script])
        else:
            args = ["notify-send"]
            if urgent:
                args += ["-u", "critical"]
            args += [title, message]
            self._run(args)

    @staticmethod
    def _run(args: list[str], env: dict[str, str] | None = None) -> None:
        import os

        full_env = {**os.environ, **env} if env else None
        result = subprocess.run(args, capture_output=True, text=True, timeout=15, env=full_env)
        if result.returncode != 0:
            raise NotifyUnavailable(result.stderr.strip() or "the notification was not shown")


def _osa(text: str) -> str:
    """Quote a string for AppleScript."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def detect() -> Backend | None:
    """A notification backend for this computer, or None if there is no usable one."""
    if sys.platform == "win32":
        return Backend("powershell-toast", "windows")
    if sys.platform == "darwin":
        return Backend("osascript", "mac")
    if shutil.which("notify-send"):
        return Backend("notify-send", "linux")
    return None
