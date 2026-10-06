"""Run Starlight in the background (Step 10 packaging).

* Windows: two Task Scheduler tasks that start at logon (core and viewer). Not a Windows
  service on purpose: services run in an isolated session and could not see or control
  your desktop (Step 8).
* Linux: systemd user units (``systemctl --user``), which run in your session.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

TASKS = {"core": "serve", "viewer": "viewer"}


@dataclass
class Plan:
    """What `starlight service ...` will do: files to write and commands to run."""

    files: dict[Path, str]
    commands: list[list[str]]


def python_for_background() -> str:
    exe = Path(sys.executable)
    if sys.platform == "win32":
        quiet = exe.with_name("pythonw.exe")  # no console window
        if quiet.exists():
            return str(quiet)
    return str(exe)


def _windows_name(kind: str) -> str:
    return f"Starlight\\{kind.capitalize()}"


def windows_plan(root: Path, action: str) -> Plan:
    commands: list[list[str]] = []
    for kind, subcommand in TASKS.items():
        name = _windows_name(kind)
        if action == "install":
            run = f'"{python_for_background()}" -m starlight --home "{root}" {subcommand}'
            commands.append([
                "schtasks", "/Create", "/TN", name, "/TR", run, "/SC", "ONLOGON",
                "/RL", "LIMITED", "/F",
            ])  # fmt: skip
            commands.append(["schtasks", "/Run", "/TN", name])
        elif action == "uninstall":
            commands.append(["schtasks", "/End", "/TN", name])
            commands.append(["schtasks", "/Delete", "/TN", name, "/F"])
        else:
            commands.append(["schtasks", "/Query", "/TN", name])
    return Plan({}, commands)


def systemd_unit(root: Path, kind: str) -> str:
    subcommand = TASKS[kind]
    description = "Starlight core" if kind == "core" else "Starlight statistics viewer"
    return f"""[Unit]
Description={description}
After=graphical-session.target network-online.target

[Service]
ExecStart={python_for_background()} -m starlight --home {root} {subcommand}
WorkingDirectory={root}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def linux_plan(root: Path, action: str, unit_dir: Path | None = None) -> Plan:
    unit_dir = unit_dir or Path.home() / ".config" / "systemd" / "user"
    units = {kind: f"starlight-{kind}.service" for kind in TASKS}
    files: dict[Path, str] = {}
    commands: list[list[str]] = []
    if action == "install":
        files = {unit_dir / unit: systemd_unit(root, kind) for kind, unit in units.items()}
        commands = [["systemctl", "--user", "daemon-reload"]] + [
            ["systemctl", "--user", "enable", "--now", unit] for unit in units.values()
        ]
    elif action == "uninstall":
        commands = [["systemctl", "--user", "disable", "--now", u] for u in units.values()]
    else:
        commands = [["systemctl", "--user", "status", "--no-pager", *units.values()]]
    return Plan(files, commands)


def plan(root: Path, action: str) -> Plan:
    if sys.platform == "win32":
        return windows_plan(root, action)
    return linux_plan(root, action)


def apply(service_plan: Plan) -> int:
    for path, content in service_plan.files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    code = 0
    for command in service_plan.commands:
        result = subprocess.run(command, check=False)
        code = code or result.returncode
    return code
