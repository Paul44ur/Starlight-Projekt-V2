"""Run Starlight in the background (Step 10 packaging).

* Windows: two shortcuts in your Startup folder (core and viewer), started with
  pythonw.exe so no console window appears. No administrator rights needed, and they run
  in your own desktop session, which Step 8 needs (a Windows service could not see or
  control your desktop).
* Linux: systemd user units (``systemctl --user``), which run in your session.

``starlight service stop`` shuts both down cleanly, so the core still releases keys,
closes browsers and writes its logs: on Windows through a request file they watch, on Linux
with ``systemctl --user stop`` (SIGTERM, which both handle).
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# kind of background task -> the `starlight` subcommand it runs
SUBCOMMAND_FOR_KIND = {"core": "serve", "viewer": "viewer"}
SHUTDOWN_REQUEST = "stop\n"  # what `service stop` writes into a SHUTDOWN.<kind> file


def link_name(kind: str) -> str:
    """The Startup-folder shortcut of a kind (Windows). Existing ones keep this name."""
    return f"Starlight {kind}.lnk"


def unit_name(kind: str) -> str:
    """The systemd user unit of a kind (Linux). Existing ones keep this name."""
    return f"starlight-{kind}.service"


def shutdown_file(data_dir: Path, kind: str) -> Path:
    """Created by `service stop`; the running core or viewer exits cleanly when it appears."""
    return data_dir / f"SHUTDOWN.{kind}"


@dataclass
class Plan:
    """What `starlight service ...` will do."""

    files: dict[Path, str] = field(default_factory=dict)  # write these
    remove: list[Path] = field(default_factory=list)  # delete these
    commands: list[list[str]] = field(default_factory=list)  # run and wait
    spawn: list[list[str]] = field(default_factory=list)  # start in the background


def python_for_background() -> str:
    exe = Path(sys.executable)
    if sys.platform == "win32":
        quiet = exe.with_name("pythonw.exe")  # no console window
        if quiet.exists():
            return str(quiet)
    return str(exe)


def background_command(root: Path, kind: str) -> list[str]:
    return [
        python_for_background(),
        "-m",
        "starlight",
        "--home",
        str(root),
        SUBCOMMAND_FOR_KIND[kind],
    ]


def startup_folder() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def shortcut_script(link: Path, command: list[str], workdir: Path) -> str:
    """PowerShell that creates a .lnk (via the Windows shell, not VBScript)."""
    arguments = " ".join(f'"{part}"' if " " in part else part for part in command[1:])
    return "; ".join([
        "$shell = New-Object -ComObject WScript.Shell",
        f"$link = $shell.CreateShortcut({_ps_quote(str(link))})",
        f"$link.TargetPath = {_ps_quote(command[0])}",
        f"$link.Arguments = {_ps_quote(arguments)}",
        f"$link.WorkingDirectory = {_ps_quote(str(workdir))}",
        "$link.WindowStyle = 7",  # 7: minimized
        "$link.Save()",
    ])  # fmt: skip


def windows_plan(root: Path, action: str, data_dir: Path, folder: Path | None = None) -> Plan:
    folder = folder or startup_folder()
    links = {kind: folder / link_name(kind) for kind in SUBCOMMAND_FOR_KIND}
    result = Plan()
    if action == "install":
        for kind, link in links.items():
            script = shortcut_script(link, background_command(root, kind), root)
            result.commands.append(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
            )
    if action in ("install", "start"):
        result.remove += [shutdown_file(data_dir, kind) for kind in SUBCOMMAND_FOR_KIND]
        result.spawn += [background_command(root, kind) for kind in SUBCOMMAND_FOR_KIND]
    if action in ("stop", "uninstall"):
        result.files.update(
            {shutdown_file(data_dir, kind): SHUTDOWN_REQUEST for kind in SUBCOMMAND_FOR_KIND}
        )
    if action == "uninstall":
        result.remove += list(links.values())
    return result


def _systemd_word(part: str) -> str:
    """One word of ExecStart=: systemd splits on spaces, reads quotes and backslashes, and
    expands % and $ itself."""
    part = part.replace("\\", "\\\\").replace("%", "%%").replace("$", "$$")
    if any(c.isspace() or c in "\"'" for c in part):
        part = '"' + part.replace('"', '\\"') + '"'
    return part


def systemd_unit(root: Path, kind: str) -> str:
    description = "Starlight core" if kind == "core" else "Starlight statistics viewer"
    command = " ".join(_systemd_word(part) for part in background_command(root, kind))
    return f"""[Unit]
Description={description}
After=graphical-session.target network-online.target

[Service]
ExecStart={command}
WorkingDirectory={str(root).replace("%", "%%")}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def linux_plan(root: Path, action: str, unit_dir: Path | None = None) -> Plan:
    unit_dir = unit_dir or Path.home() / ".config" / "systemd" / "user"
    units = {kind: unit_name(kind) for kind in SUBCOMMAND_FOR_KIND}
    result = Plan()
    systemctl = ["systemctl", "--user"]
    if action == "install":
        result.files = {unit_dir / unit: systemd_unit(root, kind) for kind, unit in units.items()}
        result.commands = [[*systemctl, "daemon-reload"]] + [
            [*systemctl, "enable", "--now", unit] for unit in units.values()
        ]
    elif action == "uninstall":
        result.commands = [[*systemctl, "disable", "--now", u] for u in units.values()]
        result.remove = [unit_dir / unit for unit in units.values()]
    elif action in ("start", "stop"):
        result.commands = [[*systemctl, action, u] for u in units.values()]
    return result


def plan(root: Path, action: str, data_dir: Path) -> Plan:
    if sys.platform == "win32":
        return windows_plan(root, action, data_dir)
    return linux_plan(root, action)


def apply(service_plan: Plan) -> int:
    for path, content in service_plan.files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    code = 0
    for command in service_plan.commands:  # all of them, even after one failed
        returncode = subprocess.run(command, check=False).returncode
        code = code or returncode
    # After the commands: `systemctl disable` still needs the unit files it disables.
    for path in service_plan.remove:
        path.unlink(missing_ok=True)
    flags = 0
    if sys.platform == "win32":  # checked per platform by mypy, so no ignore needed
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    for command in service_plan.spawn:
        subprocess.Popen(
            command,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=sys.platform != "win32",
        )
    return code
