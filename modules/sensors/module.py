"""Sensors: what happens on this PC, as events automations (and other modules) react to - a
new file in a folder you watch, a USB stick or drive plugged in or taken out, the battery
running low, a program starting, and you leaving the PC and coming back.

Coming back after a while away brings one message with what came in meanwhile (reminders,
alerts, mail notes), so nothing gets lost under a pile of notifications. It only looks at
this PC; nothing is sent anywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import string
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Event, Module, ModuleContext, ToolError, tool
from starlight.core.jsonfile import load_json, save_json
from starlight.core.safepaths import expand

# The events, with their fields (for automations: on "sensors.drive_added", {event.label}).
EVENTS: dict[str, tuple[str, list[str]]] = {
    "sensors.file_appeared": ("a new file in a folder you watch", ["path", "name", "folder"]),
    "sensors.drive_added": ("a USB stick or drive was plugged in", ["drive", "label"]),
    "sensors.drive_removed": ("a USB stick or drive was taken out", ["drive", "label"]),
    "sensors.battery_low": ("the battery is low, on battery", ["percent"]),
    "sensors.power_plugged": ("the charger was plugged in", ["percent"]),
    "sensors.power_unplugged": ("the charger was unplugged", ["percent"]),
    "sensors.app_started": ("a program was started", ["name"]),
    "sensors.away": ("no mouse or keyboard for a while", ["idle_minutes"]),
    "sensors.back": ("you are back at the PC", ["away_minutes"]),
}
UNFINISHED = (".tmp", ".crdownload", ".part", ".partial", ".download", "~")
RESTARTED_AFTER = 60  # seconds: a program closed this long and running again counts as started
MAX_AWAY_ITEMS = 15


class Config(BaseModel):
    check_every: float = Field(default=10, ge=2)
    folders: list[str] = Field(default_factory=list)
    battery_low: int = Field(default=20, ge=1, le=99)
    away_after_minutes: float = Field(default=10, ge=1)
    away_summary: bool = True
    watch_programs: bool = True


# -- what the PC says (each returns None where it cannot tell) ------------------------------


def idle_seconds() -> float | None:
    """Seconds since the last mouse or keyboard input."""
    if sys.platform == "win32":
        import ctypes

        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LastInput()
        info.cbSize = ctypes.sizeof(info)
        windll: Any = ctypes.windll  # type: ignore[attr-defined,unused-ignore]
        if not windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        ticks: int = windll.kernel32.GetTickCount()
        return ((ticks - int(info.dwTime)) & 0xFFFFFFFF) / 1000
    tool_path = shutil.which("xprintidle")  # Linux with X11
    if tool_path is None:
        return None
    try:
        out = subprocess.run([tool_path], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip()) / 1000
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def drives() -> dict[str, str]:
    """Plugged-in drives: {"E:": "USB STICK"} on Windows, {mount point: name} on Linux."""
    found: dict[str, str] = {}
    if sys.platform == "win32":
        import ctypes

        kernel: Any = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        mask = kernel.GetLogicalDrives()
        for n, letter in enumerate(string.ascii_uppercase):
            root = f"{letter}:\\"
            # 2 removable (a stick), 3 fixed (an external disk shows as fixed too)
            if not mask >> n & 1 or kernel.GetDriveTypeW(root) not in (2, 3):
                continue
            label = ctypes.create_unicode_buffer(261)
            kernel.GetVolumeInformationW(root, label, 261, None, None, None, None, 0)
            found[f"{letter}:"] = label.value
        return found
    user = os.environ.get("USER", "")
    for base in (Path("/media") / user, Path("/run/media") / user):
        with contextlib.suppress(OSError):
            for mount in base.iterdir():
                if mount.is_dir():
                    found[str(mount)] = mount.name
    return found


def battery() -> tuple[int, bool] | None:
    """(percent, charger plugged in), or None without a battery."""
    with contextlib.suppress(Exception):
        import psutil

        state = psutil.sensors_battery()
        if state is None:
            return None
        return round(state.percent), bool(state.power_plugged)
    if sys.platform == "win32":
        import ctypes

        class PowerStatus(ctypes.Structure):
            _fields_ = [("ac", ctypes.c_ubyte), ("flag", ctypes.c_ubyte),
                        ("percent", ctypes.c_ubyte), ("saver", ctypes.c_ubyte),
                        ("left", ctypes.c_ulong), ("full", ctypes.c_ulong)]  # fmt: skip

        status = PowerStatus()
        windll: Any = ctypes.windll  # type: ignore[attr-defined,unused-ignore]
        if not windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
        if status.flag & 128 or status.percent == 255:  # no battery, or it cannot tell
            return None
        return int(status.percent), status.ac == 1
    for supply in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        with contextlib.suppress(OSError, ValueError):
            percent = int((supply / "capacity").read_text().strip())
            charging = (supply / "status").read_text().strip() != "Discharging"
            return percent, charging
    return None


def programs() -> set[str]:
    """The names of the running programs, lowercase and without ".exe"."""
    names: list[str] = []
    try:
        import psutil

        names = [p.info["name"] or "" for p in psutil.process_iter(["name"])]
    except ImportError:
        if sys.platform == "win32":
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                out = subprocess.run(
                    ["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True,
                    timeout=10, creationflags=0x08000000,
                )  # fmt: skip
                names = [line.split('","')[0].strip('"') for line in out.stdout.splitlines()]
        else:
            for comm in Path("/proc").glob("[0-9]*/comm"):
                with contextlib.suppress(OSError):
                    names.append(comm.read_text().strip())
    return {n.lower().removesuffix(".exe") for n in names if n}


class SensorsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.folders_file = ctx.data_dir / "folders.json"
        self._files: dict[str, set[str]] = {}  # folder -> names seen
        self._drives: dict[str, str] | None = None
        self._power: tuple[int, bool] | None = None
        self._low_told = False
        self._running: set[str] | None = None  # the programs at the last look
        self._closed: dict[str, float] = {}  # program -> when it was last seen closing
        self.away_since: float | None = None
        self.missed: list[dict[str, str]] = []
        ctx.bus.subscribe("deliver", self._on_deliver, owner=ctx.name)
        ctx.every(self.config.check_every, self.check, name="sensors", first_delay=5)

    # -- the probes (the tests swap these) -----------------------------------------------

    def _idle(self) -> float | None:
        return idle_seconds()

    def _drives_now(self) -> dict[str, str]:
        return drives()

    def _battery(self) -> tuple[int, bool] | None:
        return battery()

    def _programs(self) -> set[str]:
        return programs()

    def _clock(self) -> float:
        return time.time()

    # -- one look ------------------------------------------------------------------------

    def _watched(self) -> dict[str, dict[str, Any]]:
        """Folder -> {"tell": bool}: from the config and from sensors.watch_folder."""
        saved: dict[str, dict[str, Any]] = load_json(self.folders_file, {})
        for folder in self.config.folders:
            saved.setdefault(str(expand(folder)), {"tell": False})
        return saved

    async def check(self) -> list[str]:
        """Look once at everything; publish what changed. Returns the event names."""
        watched = self._watched()
        # The probes wait on the system (a slow drive, the process list): not on the loop
        # that answers the chat.
        found, idle = await asyncio.to_thread(self._look, watched)
        notes = [
            f"New in {Path(data['folder']).name}: {data['name']}"
            for name, data in found
            if name == "sensors.file_appeared" and watched.get(data["folder"], {}).get("tell")
        ]
        if notes:
            await self.ctx.deliver("\n".join(notes[:10]), title="New files")
        fired = [*found, *await self._check_away(idle)]
        for name, data in fired:
            await self.ctx.publish(name, data)
        return [name for name, _ in fired]

    def _look(
        self, watched: dict[str, dict[str, Any]]
    ) -> tuple[list[tuple[str, dict[str, Any]]], float | None]:
        fired = self._check_folders(watched)
        fired += self._check_drives()
        fired += self._check_battery()
        if self.config.watch_programs:
            fired += self._check_programs()
        return fired, self._idle()

    def _check_folders(
        self, watched: dict[str, dict[str, Any]]
    ) -> list[tuple[str, dict[str, Any]]]:
        fired = []
        for folder in list(self._files):
            if folder not in watched:
                del self._files[folder]
        for folder in watched:
            try:
                with os.scandir(folder) as entries:
                    names = {e.name for e in entries if e.is_file()}
            except OSError:  # gone, or a drive not plugged in: look again next time
                continue
            before = self._files.get(folder)
            self._files[folder] = names
            if before is None:  # the first look only learns what is there
                continue
            for name in sorted(names - before):
                if name.lower().endswith(UNFINISHED) or name.startswith(("~$", ".")):
                    continue
                path = str(Path(folder) / name)
                fired.append(("sensors.file_appeared",
                              {"path": path, "name": name, "folder": folder}))  # fmt: skip
        return fired

    def _check_drives(self) -> list[tuple[str, dict[str, Any]]]:
        now = self._drives_now()
        before, self._drives = self._drives, now
        if before is None:
            return []
        fired: list[tuple[str, dict[str, Any]]] = [
            ("sensors.drive_added", {"drive": d, "label": now[d]})
            for d in sorted(set(now) - set(before))
        ]
        fired += [("sensors.drive_removed", {"drive": d, "label": before[d]})
                  for d in sorted(set(before) - set(now))]  # fmt: skip
        return fired

    def _check_battery(self) -> list[tuple[str, dict[str, Any]]]:
        now = self._battery()
        before, self._power = self._power, now
        if now is None:
            return []
        percent, plugged = now
        fired: list[tuple[str, dict[str, Any]]] = []
        if before is not None and before[1] != plugged:
            name = "sensors.power_plugged" if plugged else "sensors.power_unplugged"
            fired.append((name, {"percent": percent}))
        if plugged or percent > self.config.battery_low:
            self._low_told = False
        elif not self._low_told:
            self._low_told = True
            fired.append(("sensors.battery_low", {"percent": percent}))
        return fired

    def _check_programs(self) -> list[tuple[str, dict[str, Any]]]:
        """Programs running now that were not at the last look - unless they were closed
        only a moment ago (a restart, a helper that comes and goes). Compared look to
        look, so waking the PC from sleep starts nothing."""
        now, running = self._clock(), self._programs()
        before, self._running = self._running, running
        if before is None:
            return []
        for name in before - running:
            self._closed[name] = now
        started = [
            name for name in sorted(running - before)
            if now - self._closed.pop(name, float("-inf")) > RESTARTED_AFTER
        ]  # fmt: skip
        for name in [n for n, when in self._closed.items() if now - when > 3600]:
            del self._closed[name]  # (long gone: forget it)
        return [("sensors.app_started", {"name": name}) for name in started]

    async def _check_away(self, idle: float | None) -> list[tuple[str, dict[str, Any]]]:
        if idle is None:
            return []
        limit = self.config.away_after_minutes * 60
        if self.away_since is None:
            if idle < limit:
                return []
            self.away_since = self._clock() - idle
            self.missed = []
            return [("sensors.away", {"idle_minutes": round(idle / 60)})]
        if idle >= limit:
            return []
        minutes = max(1, round((self._clock() - idle - self.away_since) / 60))
        self.away_since = None
        if self.config.away_summary and self.missed:
            await self.ctx.deliver(self._summary(minutes), title="While you were away")
        self.missed = []
        return [("sensors.back", {"away_minutes": minutes})]

    def _summary(self, minutes: int) -> str:
        lines = [f"- {m['time']} {m['text']}" for m in self.missed[:MAX_AWAY_ITEMS]]
        if len(self.missed) > MAX_AWAY_ITEMS:
            lines.append(f"- and {len(self.missed) - MAX_AWAY_ITEMS} more")
        away = f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"
        return f"You were away for {away}. Meanwhile:\n" + "\n".join(lines)

    def _on_deliver(self, event: Event) -> None:
        if self.away_since is None or event.source == self.ctx.name:
            return
        text = " ".join(str(event.data.get("text", "")).split())
        if text:
            self.missed.append({"time": datetime.now().strftime("%H:%M"), "text": text[:300]})
            del self.missed[:-200]  # (a very long absence keeps the newest)

    # -- tools ---------------------------------------------------------------------------

    @tool
    def watch_folder(self, path: str, tell_me: bool = True) -> dict[str, Any]:
        """Notice new files in a folder (e.g. Downloads): automations can react to
        sensors.file_appeared, and with tell_me (the default) you get a short note too."""
        folder = expand(path)
        if not folder.is_dir():
            raise ToolError(f"{folder} is not a folder")
        saved = load_json(self.folders_file, {})
        saved[str(folder)] = {"tell": tell_me}
        save_json(self.folders_file, saved)
        return {"watching": str(folder), "tell_me": tell_me}

    @tool
    def unwatch_folder(self, path: str) -> dict[str, Any]:
        """Stop noticing new files in a folder."""
        saved = load_json(self.folders_file, {})
        key = str(expand(path))
        if key not in saved:
            raise ToolError(f"{key} is not watched (folders in config/modules/sensors.yaml "
                            "are changed there)")  # fmt: skip
        del saved[key]
        save_json(self.folders_file, saved)
        return {"stopped": key}

    @tool
    def status(self) -> dict[str, Any]:
        """What the sensors see now: watched folders, drives, the battery, how long since
        the last input, and whether you count as away."""
        idle = self._idle()
        power = self._battery()
        return {
            "folders": sorted(self._watched()),
            "drives": self._drives_now(),
            "battery": {"percent": power[0], "charging": power[1]} if power else None,
            "idle_minutes": None if idle is None else round(idle / 60, 1),
            "away": self.away_since is not None,
            "messages_while_away": len(self.missed),
        }

    @tool
    def events(self) -> dict[str, Any]:
        """The events the sensors send, with their fields, for automations ("on":
        "sensors.drive_added", condition "label ~ backup")."""
        return {name: {"when": when, "fields": fields} for name, (when, fields) in EVENTS.items()}
