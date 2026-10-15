"""Play, pause and skip, and change the volume - like pressing the media keys.

Windows: the media and volume keys are pressed through the Win32 API (no extra packages);
Spotify, browsers, VLC and most players listen to them. Linux: playerctl for the player and
pactl (PulseAudio/PipeWire) or amixer for the volume.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool

KEYS = {  # Windows virtual-key codes
    "play_pause": 0xB3,
    "next": 0xB0,
    "previous": 0xB1,
    "stop": 0xB2,
    "mute": 0xAD,
    "down": 0xAE,
    "up": 0xAF,
}
WINDOWS_STEP = 2  # one volume key press moves the Windows volume by 2%
PLAYERCTL = {"play_pause": "play-pause", "next": "next", "previous": "previous", "stop": "stop"}


class Config(BaseModel):
    volume_step: int = Field(default=10, ge=2, le=50)


class MediaModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    # -- system access (replaced in tests) ------------------------------------------------

    @staticmethod
    def _on_windows() -> bool:
        return sys.platform == "win32"

    def _press(self, key: str, times: int = 1) -> None:
        self.ctx.kill_switch.check()
        if not self._on_windows():
            raise ToolError("media keys are pressed this way only on Windows")
        import ctypes

        user32: Any = ctypes.windll.user32  # type: ignore[attr-defined,unused-ignore]
        for _ in range(times):
            user32.keybd_event(KEYS[key], 0, 0, 0)
            user32.keybd_event(KEYS[key], 0, 2, 0)  # key up
            if times > 1:
                time.sleep(0.005)

    def _run(self, *command: str) -> str:
        self.ctx.kill_switch.check()
        tool_path = shutil.which(command[0])
        if tool_path is None:
            raise ToolError(f"'{command[0]}' is not installed")
        done = subprocess.run(
            [tool_path, *command[1:]], capture_output=True, text=True, timeout=10, check=False
        )
        if done.returncode != 0:
            raise ToolError((done.stderr or done.stdout or "it did not work").strip()[:300])
        return done.stdout.strip()

    def _player(self, action: str) -> dict[str, Any]:
        if self._on_windows():
            self._press(action)
        else:
            self._run("playerctl", PLAYERCTL[action])
        return {"done": action.replace("_", "/")}

    # -- tools ---------------------------------------------------------------------------

    @tool
    def play_pause(self) -> dict[str, Any]:
        """Play or pause what is playing (music, a video)."""
        return self._player("play_pause")

    @tool(name="next")
    def next_track(self) -> dict[str, Any]:
        """Skip to the next track."""
        return self._player("next")

    @tool
    def previous(self) -> dict[str, Any]:
        """Go back to the previous track."""
        return self._player("previous")

    @tool
    def stop(self) -> dict[str, Any]:
        """Stop playback."""
        return self._player("stop")

    @tool
    def volume(
        self, change: Literal["up", "down", "set", "mute"] = "up", percent: int = 0
    ) -> dict[str, Any]:
        """Change the volume: "up"/"down" by a step (or by `percent`), "set" to `percent`
        (0-100), or "mute" to toggle mute."""
        if change == "set" and not 0 <= percent <= 100:
            raise ToolError("the volume is 0 to 100 percent")
        amount = percent or self.config.volume_step
        if self._on_windows():
            if change == "mute":
                self._press("mute")
            elif change == "set":
                self._press("down", 100 // WINDOWS_STEP)  # to zero, then up to the level
                self._press("up", round(percent / WINDOWS_STEP))
            else:
                self._press(change, max(1, round(amount / WINDOWS_STEP)))
            return {"volume": change, "percent": percent if change == "set" else None}
        if shutil.which("pactl"):
            sink = "@DEFAULT_SINK@"
            if change == "mute":
                self._run("pactl", "set-sink-mute", sink, "toggle")
            else:
                level = (
                    f"{percent}%"
                    if change == "set"
                    else f"{'+' if change == 'up' else '-'}{amount}%"
                )
                self._run("pactl", "set-sink-volume", sink, level)
            return {"volume": change, "now": self._level()}
        if change == "mute":
            self._run("amixer", "-q", "set", "Master", "toggle")
        else:
            level = (
                f"{percent}%" if change == "set" else f"{amount}%{'+' if change == 'up' else '-'}"
            )
            self._run("amixer", "-q", "set", "Master", level)
        return {"volume": change}

    def _level(self) -> str | None:
        try:
            out = self._run("pactl", "get-sink-volume", "@DEFAULT_SINK@")
        except ToolError:
            return None
        match = re.search(r"(\d+)%", out)
        return f"{match.group(1)}%" if match else None

    @tool
    def now_playing(self) -> dict[str, Any]:
        """What is playing right now (title and artist), where the system can tell."""
        if self._on_windows():
            raise ToolError(
                "Windows does not tell other programs what is playing without extra "
                "packages; ask me to look at the player's window instead"
            )
        # Tabs between the parts: unlike "|", they do not turn up in artists and titles.
        out = self._run("playerctl", "metadata", "--format", "{{status}}\t{{artist}}\t{{title}}")
        status, artist, title = [*out.split("\t", 2), "", ""][:3]
        return {"status": status.lower(), "artist": artist, "title": title}
