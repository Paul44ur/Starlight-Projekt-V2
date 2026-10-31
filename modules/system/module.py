"""How your PC is doing: CPU, memory, disk space, battery, uptime and running programs,
and power: lock the screen, sleep, shut down or restart (now or in a few minutes).

Reading and locking are safe. Closing a program, sleep, shutdown and restart ask first
(unless you listed one under ``permissions: auto_approve:`` in starlight.yaml).
A shutdown or restart Starlight scheduled is cancelled by cancel_shutdown - and by the
kill switch.

Uses ``psutil`` when it is installed (the ``system`` extra, part of ``all``) for the full
picture; without it, it still reports what the standard library can.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.jsonfile import load_json, save_json


def _psutil() -> Any | None:
    try:
        import psutil
    except ImportError:
        return None
    return psutil


def human_bytes(n: float | None) -> str | None:
    if n is None:
        return None
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < step:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= step
    return f"{n:.1f} EB"


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes = seconds // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def folder_sizes(
    root: Path, budget: float = 20.0
) -> tuple[dict[str, int], list[tuple[str, int]], bool]:
    """Sizes of the folders right inside ``root`` (each with all it holds), the biggest
    files anywhere below, and whether it was all counted within ``budget`` seconds."""
    deadline = time.monotonic() + budget
    folders: dict[str, int] = {}
    files: list[tuple[str, int]] = []
    complete = True
    for top in sorted(root.iterdir(), key=lambda p: p.name.lower()) if root.is_dir() else []:
        total = 0
        stack = [top]
        while stack:
            if time.monotonic() > deadline:
                complete = False
                break
            current = stack.pop()
            try:
                if current.is_symlink():
                    continue
                if current.is_file():
                    size = current.stat().st_size
                    total += size
                    files.append((str(current), size))
                    continue
                with os.scandir(current) as entries:
                    stack.extend(Path(entry.path) for entry in entries)
            except OSError:  # no access, or gone meanwhile
                continue
        if top.is_dir():
            folders[top.name] = total
        if not complete:
            break
    files.sort(key=lambda f: -f[1])
    return folders, files[:15], complete


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def run_entries() -> list[dict[str, str]]:
    """Windows' Run lists: yours and the one for all users."""
    if sys.platform != "win32":
        return []
    import winreg

    found = []
    for hive, where in (
        (winreg.HKEY_CURRENT_USER, "you"),
        (winreg.HKEY_LOCAL_MACHINE, "all users"),
    ):
        with contextlib.suppress(OSError), winreg.OpenKey(hive, RUN_KEY) as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                found.append({"name": name, "command": str(value), "where": f"Run ({where})"})
                index += 1
    return found


def set_run_entry(name: str, command: str | None) -> None:
    """Add (or with None remove) a program in your own Run list."""
    if sys.platform != "win32":
        raise ToolError("the Run list is a Windows thing")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if command is None:
            winreg.DeleteValue(key, name)
        else:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)


def startup_entries(platform_name: str) -> list[dict[str, str]]:
    """The programs that start when you log in: Windows' Run lists and Startup folder, or
    Linux's autostart folder. Each with where it is set, so it can be turned off."""
    found: list[dict[str, str]] = []
    if platform_name == "win32":
        found += run_entries()
        appdata = os.environ.get("APPDATA", "")
        folders = (
            [Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup"] if appdata else []
        )
    else:
        folders = [Path.home() / ".config/autostart"]
    for folder in folders:
        for item in sorted(folder.glob("*")) if folder.is_dir() else []:
            if item.is_file() and item.suffix.lower() in (
                ".lnk",
                ".desktop",
                ".url",
                ".bat",
                ".cmd",
            ):
                found.append({"name": item.stem, "command": str(item), "where": "Startup folder"})
    return found


class Config(BaseModel):
    top_processes: int = Field(default=10, ge=1, le=50)


def power_command(action: str, minutes: int, platform_name: str) -> list[str]:
    """The command for a power action on this kind of system."""
    seconds = max(0, minutes) * 60
    if platform_name == "win32":
        return {
            "lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
            # Not rundll32 powrprof.dll,SetSuspendState: that hibernates when hibernation
            # is on. This is real sleep (suspend), wake events allowed.
            "sleep": [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Add-Type -AssemblyName System.Windows.Forms; "
                "[System.Windows.Forms.Application]::SetSuspendState('Suspend', $false, $false)",
            ],
            "shutdown": ["shutdown", "/s", "/t", str(seconds)],
            "restart": ["shutdown", "/r", "/t", str(seconds)],
            "cancel": ["shutdown", "/a"],
        }[action]
    if platform_name == "darwin":
        delayed = action in ("shutdown", "restart") and minutes != 0
        if action == "cancel" or delayed:  # no delayed shutdown on a Mac, so nothing to cancel
            raise ToolError("on a Mac, Starlight can only shut down or restart right away")
        return {
            # Turns the display off: that locks the Mac only when it asks for the password
            # after the display sleeps (System Settings > Lock Screen).
            "lock": ["pmset", "displaysleepnow"],
            "sleep": ["pmset", "sleepnow"],
            "shutdown": ["osascript", "-e", 'tell app "System Events" to shut down'],
            "restart": ["osascript", "-e", 'tell app "System Events" to restart'],
        }[action]
    when = f"+{minutes}" if minutes > 0 else "now"
    return {
        "lock": ["loginctl", "lock-session"],
        "sleep": ["systemctl", "suspend"],
        "shutdown": ["shutdown", "-h", when],
        "restart": ["shutdown", "-r", when],
        "cancel": ["shutdown", "-c"],
    }[action]


class SystemModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        # A shutdown/restart Starlight scheduled, kept on disk: after a reload or restart
        # of Starlight the kill switch must still be able to cancel it.
        self._pending_file = ctx.data_dir / "pending_shutdown.json"
        self._pending: str | None = None
        try:
            saved = json.loads(self._pending_file.read_text("utf-8"))
        except (OSError, ValueError):
            saved = None
        try:  # a damaged "due" (text, null) counts as nothing pending
            due = float(saved.get("due", 0)) if isinstance(saved, dict) else 0.0
        except (TypeError, ValueError):
            ctx.logger.warning("damaged pending_shutdown.json ignored")
            due = 0.0
        if isinstance(saved, dict) and due > time.time():
            self._set_pending(str(saved.get("action", "shutdown")), due)
        else:
            self._clear_pending()

    def _set_pending(self, action: str, due: float) -> None:
        self._pending = action
        self._pending_file.parent.mkdir(parents=True, exist_ok=True)
        self._pending_file.write_text(json.dumps({"action": action, "due": due}))
        self.ctx.kill_switch.remove_cleanups(self.ctx.name)
        self.ctx.kill_switch.add_cleanup(self._cancel_on_kill, owner=self.ctx.name)

    def _clear_pending(self) -> None:
        self._pending = None
        self._pending_file.unlink(missing_ok=True)
        self.ctx.kill_switch.remove_cleanups(self.ctx.name)

    # -- power -----------------------------------------------------------------------------

    def _platform(self) -> str:
        return sys.platform

    @tool
    def big_folders(self, path: str = "~") -> dict[str, Any]:
        """What takes the space in a folder (your home folder if not said, or e.g. "C:\\"):
        the folders right inside it, biggest first, and the biggest files below it.
        Only sizes are read, never what is in the files."""
        root = Path(os.path.expandvars(path.strip() or "~")).expanduser()
        if not root.is_dir():
            raise ToolError(f"{root} is not a folder")
        folders, files, complete = folder_sizes(root)
        ranked = sorted(folders.items(), key=lambda kv: -kv[1])[:20]
        return {
            "folder": str(root),
            "biggest_folders": [{"name": n, "size": human_bytes(b)} for n, b in ranked if b],
            "biggest_files": [{"file": f, "size": human_bytes(b)} for f, b in files],
            "complete": complete or "it took too long: the numbers are what was counted so far",
        }

    @tool
    def startup_programs(self) -> dict[str, Any]:
        """The programs that start when you log in (Windows Run lists and Startup
        folder, or Linux autostart), and the ones you turned off here."""
        off = load_json(self.ctx.data_dir / "startup_off.json", {})
        return {"starting": startup_entries(self._platform()),
                "turned_off_here": sorted(off)}  # fmt: skip

    @tool(permission=Permission.CONFIRM)
    def startup_off(self, name: str) -> dict[str, Any]:
        """Stop a program from starting when you log in (yours, not all users'). It can be
        turned on again with system.startup_on. Asks first."""
        entry = next((e for e in startup_entries(self._platform())
                      if e["name"].casefold() == name.strip().casefold()), None)  # fmt: skip
        if entry is None:
            raise ToolError(
                f"{name} is not among the programs that start (system.startup_programs)"
            )
        store = self.ctx.data_dir / "startup_off.json"
        off = load_json(store, {})
        if entry["where"] == "Startup folder":
            source = Path(entry["command"])
            kept = self.ctx.data_dir / "startup_off" / source.name
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), kept)
            off[entry["name"]] = {"kind": "file", "from": str(source), "kept": str(kept)}
        elif entry["where"] == "Run (you)":
            set_run_entry(entry["name"], None)
            off[entry["name"]] = {"kind": "run", "command": entry["command"]}
        else:
            raise ToolError(
                f"{name} starts for all users; turning it off needs administrator rights "
                "(Task Manager, Startup apps)"
            )
        save_json(store, off)
        return {"turned_off": entry["name"]}

    @tool
    def startup_on(self, name: str) -> dict[str, Any]:
        """Let a program you turned off with system.startup_off start at login again."""
        store = self.ctx.data_dir / "startup_off.json"
        off = load_json(store, {})
        key_name = next((k for k in off if k.casefold() == name.strip().casefold()), None)
        if key_name is None:
            raise ToolError(f"{name} was not turned off here")
        entry = off.pop(key_name)
        if entry["kind"] == "file":
            Path(entry["from"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.move(entry["kept"], entry["from"])
        else:
            set_run_entry(key_name, entry["command"])
        save_json(store, off)
        return {"turned_on": key_name}

    def _run(self, command: list[str]) -> None:
        try:
            done = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ToolError(f"could not run {command[0]}: {exc}") from exc
        if done.returncode != 0:
            message = (done.stderr or done.stdout).strip()[:300]
            raise ToolError(f"{command[0]} failed: {message or f'exit code {done.returncode}'}")

    def _power(self, action: str, minutes: int = 0) -> list[str]:
        command = power_command(action, minutes, self._platform())
        self.ctx.logger.info("power", action=action, minutes=minutes)
        self._run(command)
        return command

    def _cancel_on_kill(self) -> None:
        """The kill switch also stops a shutdown or restart Starlight scheduled."""
        if self._pending is not None:
            with contextlib.suppress(ToolError):
                self._run(power_command("cancel", 0, self._platform()))
            self._pending = None
            self._pending_file.unlink(missing_ok=True)

    @tool
    def lock(self) -> dict[str, Any]:
        """Lock the screen (your programs keep running)."""
        self._power("lock")
        return {"locked": True}

    @tool(name="sleep", permission=Permission.CONFIRM)
    def sleep_pc(self) -> dict[str, Any]:
        """Put the PC to sleep now. Asks first."""
        self._power("sleep")
        return {"sleeping": True}

    def _schedule(self, action: str, in_minutes: int) -> dict[str, Any]:
        minutes = max(0, min(in_minutes, 24 * 60))
        self._power(action, minutes)
        if minutes:
            self._set_pending(action, time.time() + minutes * 60)
        return {
            action: True,
            "in_minutes": minutes,
            "note": "cancel it with system.cancel_shutdown" if minutes else "now",
        }

    # Not named shutdown(): that is the module's own unload hook.
    @tool(name="shutdown", permission=Permission.CONFIRM)
    def shutdown_pc(self, in_minutes: int = 0) -> dict[str, Any]:
        """Shut the PC down, now or in some minutes. Asks first; unsaved work in open
        programs may be lost."""
        return self._schedule("shutdown", in_minutes)

    @tool(name="restart", permission=Permission.CONFIRM)
    def restart_pc(self, in_minutes: int = 0) -> dict[str, Any]:
        """Restart the PC, now or in some minutes. Asks first; unsaved work in open
        programs may be lost."""
        return self._schedule("restart", in_minutes)

    @tool
    def cancel_shutdown(self) -> dict[str, Any]:
        """Cancel a planned shutdown or restart."""
        self._power("cancel")
        self._clear_pending()
        return {"cancelled": True}

    @tool
    def info(self) -> dict[str, Any]:
        """A summary of how the PC is doing: OS, uptime, CPU, memory, disk and battery."""
        ps = _psutil()
        result: dict[str, Any] = {
            "os": f"{platform.system()} {platform.release()}",
            "hostname": platform.node(),
            "cpu_cores": os.cpu_count(),
            "python": platform.python_version(),
        }
        boot = self._boot_time(ps)
        if boot is not None:
            result["uptime"] = human_duration(time.time() - boot)
        if ps is not None:
            result["cpu_percent"] = ps.cpu_percent(interval=0.2)
            mem = ps.virtual_memory()
            result["memory"] = {
                "total": human_bytes(mem.total),
                "used": human_bytes(mem.used),
                "available": human_bytes(mem.available),
                "percent": mem.percent,
            }
        else:
            total, available = self._memory_fallback()
            if total is not None:
                result["memory"] = {
                    "total": human_bytes(total),
                    "available": human_bytes(available),
                }
        result["disks"] = self.disks()
        battery = self._battery(ps)
        if battery is not None:
            result["battery"] = battery
        return result

    @tool(own_timeout=True)
    async def health_report(self, send: bool = False) -> dict[str, Any]:
        """A check-up of the PC: disk space, memory, how long since the last restart,
        programs that start at login, program updates (winget), backups and certificates.
        With send it is delivered as a message too (for a weekly automation)."""
        good: list[str] = []
        bad: list[str] = []
        for disk in self.disks():
            line = f"{disk['drive']}: {disk['free']} free ({disk['percent_used']}% used)"
            (bad if disk["percent_used"] >= 90 else good).append(line)
        ps = _psutil()
        if ps is not None and (memory := ps.virtual_memory().percent) >= 90:
            bad.append(f"memory is {memory}% full; close a program or restart")
        boot = self._boot_time(ps)
        if boot is not None:
            days = (time.time() - boot) / 86400
            since = f"last restart {human_duration(time.time() - boot)} ago"
            (bad if days > 14 else good).append(since + ("; restart soon" if days > 14 else ""))
        starting = startup_entries(self._platform())
        good.append(f"{len(starting)} programs start at login")
        await self._check_others(good, bad)
        text = "\n".join(
            ["PC check-up", *(f"⚠ {line}" for line in bad), *(f"✓ {line}" for line in good)]
        )
        if send:
            await self.ctx.deliver(text, title="PC check-up")
        return {"warnings": bad, "fine": good, "text": text}

    async def _check_others(self, good: list[str], bad: list[str]) -> None:
        """The parts that come from other modules, each left out when it is off."""
        if self.ctx.has_tool("apps.updates"):
            out = await self.ctx.call("apps.updates")
            if out.ok:
                count = len(out.output.get("updates", []))
                (bad if count else good).append(
                    f"{count} program updates waiting (say 'update all programs')"
                    if count
                    else "programs are up to date"
                )
        if self.ctx.has_tool("backup.list"):
            out = await self.ctx.call("backup.list")
            for plan in out.output.get("backups", []) if out.ok else []:
                newest = plan.get("newest")
                try:
                    age = (
                        time.time() - time.mktime(time.strptime(newest[:10], "%Y-%m-%d"))
                    ) / 86400
                except (TypeError, ValueError):
                    bad.append(f"backup {plan['name']} has never run")
                    continue
                line = f"backup {plan['name']}: newest from {newest[:10]}"
                (bad if age > 8 else good).append(line)
        if self.ctx.has_tool("certs.expiring"):
            out = await self.ctx.call("certs.expiring", {"days": 30})
            if out.ok:
                for cert in out.output.get("expiring", []):
                    bad.append(f"certificate for {cert.get('host')} expires in "
                               f"{cert.get('days_left')} days")  # fmt: skip

    @tool
    def disks(self) -> list[dict[str, Any]]:
        """Free and used space on each drive."""
        ps = _psutil()
        out: list[dict[str, Any]] = []
        mounts: list[str] = []
        if ps is not None:
            mounts = [p.mountpoint for p in ps.disk_partitions(all=False)]
        if not mounts:
            mounts = (
                [f"{d}:\\" for d in "CDEFGH" if os.path.exists(f"{d}:\\")]
                if sys.platform == "win32"
                else ["/"]
            )
        for mount in mounts:
            try:
                usage = shutil.disk_usage(mount)
            except OSError:
                continue
            out.append(
                {
                    "drive": mount,
                    "total": human_bytes(usage.total),
                    "used": human_bytes(usage.used),
                    "free": human_bytes(usage.free),
                    "percent_used": round(usage.used / usage.total * 100, 1) if usage.total else 0,
                }
            )
        return out

    @tool
    def processes(self, top: int = 0, by: str = "memory") -> list[dict[str, Any]]:
        """The programs using the most memory (by="memory") or CPU (by="cpu")."""
        ps = _psutil()
        limit = top or self.config.top_processes
        if by not in ("memory", "cpu"):
            raise ToolError("by must be 'memory' or 'cpu'")
        if ps is None:
            if by == "cpu":
                raise ToolError("ranking by CPU needs the 'system' extra (psutil)")
            return self._processes_fallback(limit)
        procs = list(ps.process_iter(["pid", "name", "memory_info"]))
        if by == "cpu":  # the first reading is always 0.0: measure over a short moment
            for proc in procs:
                with contextlib.suppress(ps.NoSuchProcess, ps.AccessDenied):
                    proc.cpu_percent(None)
            time.sleep(0.3)
        rows: list[dict[str, Any]] = []
        for proc in procs:
            try:
                mem = proc.info["memory_info"]
                row: dict[str, Any] = {
                    "pid": proc.info["pid"],
                    "name": proc.info["name"],
                    "memory": human_bytes(mem.rss if mem else 0),
                    "_mem": mem.rss if mem else 0,
                }
                if by == "cpu":
                    row["cpu_percent"] = proc.cpu_percent(None)
                rows.append(row)
            except (ps.NoSuchProcess, ps.AccessDenied):
                continue
        key = "_mem" if by == "memory" else "cpu_percent"
        rows.sort(key=lambda r: r[key], reverse=True)
        for row in rows:
            row.pop("_mem", None)
        return rows[:limit]

    @tool(permission=Permission.CONFIRM)
    def close_app(self, name: str) -> dict[str, Any]:
        """Close a running program by name (e.g. "notepad"). Asks first unless it is
        auto-approved. This ends the program - unsaved work in it is lost. The name must
        match the program's name (with or without ".exe"); only if none does, a part of the
        name of at least 3 letters is tried."""
        ps = _psutil()
        if ps is None:
            raise ToolError("closing programs needs the 'system' extra (psutil)")
        name = name.strip().lower()
        if not name:
            raise ToolError("give the program name")
        keep = {os.getpid(), os.getppid()}  # never close Starlight itself or what started it
        running = [
            (proc, (proc.info["name"] or "").lower())
            for proc in ps.process_iter(["pid", "name"])
            if proc.pid not in keep
        ]
        found = [proc for proc, pname in running if pname in (name, f"{name}.exe")]
        if not found and len(name) >= 3:  # "word" finds WINWORD.EXE, but "e" finds nothing
            found = [proc for proc, pname in running if name in pname]
        closed: list[int] = []
        for proc in found:
            try:
                proc.terminate()
                closed.append(proc.pid)
            except (ps.NoSuchProcess, ps.AccessDenied):
                continue
        if not closed:
            raise ToolError(f"no running program matching '{name}'")
        self.ctx.logger.info("closed app", name=name, pids=closed)
        return {"closed": name, "count": len(closed), "pids": closed}

    # -- fallbacks without psutil --------------------------------------------------------

    def _boot_time(self, ps: Any | None) -> float | None:
        if ps is not None:
            return float(ps.boot_time())
        if sys.platform.startswith("linux"):
            try:
                with open("/proc/uptime") as handle:
                    return time.time() - float(handle.read().split()[0])
            except OSError:
                return None
        return None

    def _memory_fallback(self) -> tuple[int | None, int | None]:
        if sys.platform.startswith("linux"):
            try:
                values: dict[str, int] = {}
                with open("/proc/meminfo") as handle:
                    for line in handle:
                        key, _, rest = line.partition(":")
                        values[key] = int(rest.split()[0]) * 1024  # kB -> bytes
                return values.get("MemTotal"), values.get("MemAvailable")
            except (OSError, ValueError):
                return None, None
        return None, None

    def _battery(self, ps: Any | None) -> dict[str, Any] | None:
        if ps is None or not hasattr(ps, "sensors_battery"):
            return None
        try:
            battery = ps.sensors_battery()
        except Exception:
            return None
        if battery is None:
            return None
        result: dict[str, Any] = {
            "percent": round(battery.percent),
            "plugged_in": battery.power_plugged,
        }
        if battery.secsleft is not None and battery.secsleft >= 0 and not battery.power_plugged:
            result["time_left"] = human_duration(battery.secsleft)
        return result

    def _processes_fallback(self, limit: int) -> list[dict[str, Any]]:
        """The programs using the most memory, without psutil."""
        try:
            if sys.platform == "win32":
                out = subprocess.run(
                    ["tasklist", "/fo", "csv", "/nh"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                ).stdout
                listed: list[tuple[int, dict[str, Any]]] = []
                for line in out.splitlines():
                    fields = [f.strip('"') for f in line.split('","')]
                    if len(fields) < 5 or not fields[1].isdigit():
                        continue
                    kb = "".join(c for c in fields[4] if c.isdigit())  # reads like "12.345 K"
                    if kb:
                        row = {
                            "pid": int(fields[1]),
                            "name": fields[0],
                            "memory": human_bytes(int(kb) * 1024),
                        }
                        listed.append((int(kb), row))
                listed.sort(key=lambda item: item[0], reverse=True)
                return [row for _, row in listed[:limit]]
            out = subprocess.run(
                ["ps", "-eo", "pid,comm,rss", "--sort=-rss"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
            rows = []
            for line in out.splitlines()[1 : limit + 1]:
                parts = line.split(None, 2)
                if len(parts) == 3:
                    rows.append(
                        {
                            "pid": parts[0],
                            "name": parts[1],
                            "memory": human_bytes(int(parts[2]) * 1024),
                        }
                    )
            return rows
        except (OSError, subprocess.SubprocessError, ValueError):
            return []
