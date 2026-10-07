"""How your PC is doing: CPU, memory, disk space, battery, uptime and running programs.

Reading is safe and needs no confirmation. Closing a program asks first.

Uses ``psutil`` when it is installed (the ``system`` extra, part of ``all``) for the full
picture; without it, it still reports what the standard library can.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import time
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool


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


class Config(BaseModel):
    top_processes: int = Field(default=10, ge=1, le=50)


class SystemModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

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
            return self._processes_fallback(limit)
        rows: list[dict[str, Any]] = []
        for proc in ps.process_iter(["pid", "name", "memory_info", "cpu_percent"]):
            try:
                mem = proc.info["memory_info"]
                rows.append(
                    {
                        "pid": proc.info["pid"],
                        "name": proc.info["name"],
                        "memory": human_bytes(mem.rss if mem else 0),
                        "_mem": mem.rss if mem else 0,
                        "cpu_percent": proc.info.get("cpu_percent") or 0.0,
                    }
                )
            except (ps.NoSuchProcess, ps.AccessDenied):
                continue
        key = "_mem" if by == "memory" else "cpu_percent"
        rows.sort(key=lambda r: r[key], reverse=True)
        for row in rows:
            row.pop("_mem", None)
        return rows[:limit]

    @tool(permission=Permission.CONFIRM)
    def close_app(self, name: str) -> dict[str, Any]:
        """Close a running program by name (e.g. "notepad"). Asks first. This ends the
        program - unsaved work in it is lost."""
        ps = _psutil()
        if ps is None:
            raise ToolError("closing programs needs the 'system' extra (psutil)")
        name = name.strip().lower()
        if not name:
            raise ToolError("give the program name")
        closed: list[int] = []
        for proc in ps.process_iter(["pid", "name"]):
            try:
                pname = (proc.info["name"] or "").lower()
                if name in pname or pname == f"{name}.exe":
                    if proc.pid == os.getpid():
                        continue  # never close Starlight itself
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
        import subprocess

        try:
            if sys.platform == "win32":
                out = subprocess.run(
                    ["tasklist", "/fo", "csv", "/nh"], capture_output=True, text=True, timeout=10
                ).stdout
                rows = []
                for line in out.splitlines():
                    fields = [f.strip('"') for f in line.split('","')]
                    if len(fields) >= 5:
                        rows.append({"name": fields[0], "pid": fields[1], "memory": fields[4]})
                return rows[:limit]
            out = subprocess.run(
                ["ps", "-eo", "pid,comm,rss", "--sort=-rss"],
                capture_output=True,
                text=True,
                timeout=10,
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
