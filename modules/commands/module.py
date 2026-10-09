"""Your own named commands ("run backup-photos", "restart the game server"), and - when
you switch it on - any command line.

Named commands are listed in config/modules/commands.yaml. Each is a fixed program with
fixed arguments; `{name}` placeholders take a value when it runs (one argument each, never
parsed by a shell, never starting with "-"). A command asks before it runs unless you set
`ask: false` for it. Adding or changing a command always asks you.

`commands.shell` runs any command line (PowerShell on Windows, /bin/sh elsewhere). It is
off unless `shell: true` is set in commands.yaml, and it asks before every command, showing
it exactly. It can do anything you could type in a terminal, including changing
Starlight's own settings, so from a phone it never runs (`web.remote.blocked`).

The kill switch stops a running command.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.config import write_yaml_config

NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
SAFE_VALUE = re.compile(r"^[^\-\n\r\x00][^\n\r\x00]{0,500}$")


class Command(BaseModel):
    run: list[str] = Field(min_length=1)
    description: str = ""
    ask: bool = True
    timeout: float = Field(default=120, gt=0, le=24 * 3600)
    cwd: str = ""

    @field_validator("run")
    @classmethod
    def _program_is_fixed(cls, run: list[str]) -> list[str]:
        if PLACEHOLDER.search(run[0]):
            raise ValueError("the program itself can't be a placeholder")
        return run

    def placeholders(self) -> list[str]:
        return sorted({m for part in self.run for m in PLACEHOLDER.findall(part)})


class Config(BaseModel):
    max_output: int = Field(default=8000, ge=500)
    commands: dict[str, Command] = Field(default_factory=dict)
    shell: bool = False  # True: commands.shell may run any command line
    # Seconds a command line may run at most. Keep it under tools.timeout (120 s by
    # default), which stops any tool, so the output so far still comes back.
    shell_timeout: float = Field(default=110, gt=0, le=24 * 3600)


# PowerShell writes its own output as UTF-8 then (umlauts survive the pipe).
_UTF8 = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "


def shell_argv(line: str, platform: str = sys.platform) -> list[str]:
    """The program that runs a command line: PowerShell on Windows, /bin/sh elsewhere."""
    if platform == "win32":
        return ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _UTF8 + line]
    return ["/bin/sh", "-c", line]


def decode_output(data: bytes) -> str:
    """Program output as text: UTF-8, else (older Windows programs) the console's own code
    page, e.g. cp850 on a German Windows."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if sys.platform == "win32":
        import ctypes

        page = f"cp{ctypes.windll.kernel32.GetOEMCP()}"
        with contextlib.suppress(LookupError):
            return data.decode(page, errors="replace")
    return data.decode("utf-8", errors="replace")


class Job:
    """Windows: a job object holding a program and everything it starts, so a timeout or
    the kill switch stops them all - also those still running after the program itself
    has exited (`start /b`, a background task), which taskkill /T can no longer find."""

    def __init__(self, pid: int) -> None:
        self.handle: int | None = None
        if sys.platform != "win32":
            return
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        job = kernel32.CreateJobObjectW(None, None)
        # PROCESS_SET_QUOTA | PROCESS_TERMINATE: what joining a job needs
        process = kernel32.OpenProcess(0x0100 | 0x0001, False, pid)
        joined = bool(
            job
            and process
            and kernel32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(process))
        )
        if process:
            kernel32.CloseHandle(wintypes.HANDLE(process))
        if joined:
            self.handle = job
        elif job:
            kernel32.CloseHandle(wintypes.HANDLE(job))

    def kill(self) -> bool:
        if self.handle is None or sys.platform != "win32":
            return False
        import ctypes
        from ctypes import wintypes

        return bool(ctypes.windll.kernel32.TerminateJobObject(wintypes.HANDLE(self.handle), 1))

    def close(self) -> None:
        if self.handle is not None and sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self.handle))
        self.handle = None


def kill_tree(process: asyncio.subprocess.Process, job: Job | None = None) -> None:
    """Stop a program and everything it started. A shell's children would otherwise keep
    running, and keep its output open - also when the shell itself has already exited."""
    if sys.platform == "win32":
        if not (job is not None and job.kill()) and process.returncode is None:
            import subprocess

            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
    else:
        import os
        import signal

        # Its own process group (see _execute), which outlives the shell that led it.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()


async def _read_all(stream: asyncio.StreamReader | None, chunks: list[bytes]) -> None:
    """Collect a program's output as it comes, so what it wrote is there even if it has to
    be stopped."""
    if stream is None:
        return
    while data := await stream.read(65536):
        chunks.append(data)


class CommandsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self._running: dict[asyncio.subprocess.Process, Job | None] = {}
        ctx.kill_switch.add_cleanup(self._kill_all, owner=ctx.name)

    def _kill_all(self) -> None:
        for process, job in list(self._running.items()):
            kill_tree(process, job)

    def _get(self, name: str) -> tuple[str, Command]:
        key = name.strip().lower()
        command = self.config.commands.get(key)
        if command is None:
            known = ", ".join(sorted(self.config.commands)) or "none yet"
            raise ToolError(f"no command '{name}' (yours: {known})")
        return key, command

    def _save(self) -> None:
        write_yaml_config(
            self.ctx.paths.module_configs / "commands.yaml",
            {
                "max_output": self.config.max_output,
                "shell": self.config.shell,
                "shell_timeout": self.config.shell_timeout,
                "commands": {
                    k: v.model_dump(exclude_defaults=True) for k, v in self.config.commands.items()
                },
            },
            "# The commands module (modules/commands): your own named commands, and any\n"
            "# command line with commands.shell (it asks first) when shell is true.",
        )

    # -- tools ---------------------------------------------------------------------------

    @tool(name="list")
    def list_commands(self) -> dict[str, Any]:
        """Your named commands: what each does, what it needs, and whether it asks first."""
        return {
            "commands": [
                {
                    "name": name,
                    "description": c.description,
                    "needs": c.placeholders(),
                    "asks_first": c.ask,
                }
                for name, c in sorted(self.config.commands.items())
            ]
        }

    @tool
    async def run(self, name: str, values: dict[str, str] | None = None) -> dict[str, Any]:
        """Run one of your named commands (see commands.list). `values` fills its
        placeholders, e.g. {"name": "survival"}."""
        key, command = self._get(name)
        given = {k.lower(): str(v) for k, v in (values or {}).items()}
        missing = [p for p in command.placeholders() if p not in given]
        if missing:
            raise ToolError(f"'{key}' needs: {', '.join(missing)}")
        for field, value in given.items():
            if not SAFE_VALUE.match(value):
                raise ToolError(
                    f"the value for {field} is not allowed "
                    "(empty, a line break, or it starts with '-')"
                )
        argv = [PLACEHOLDER.sub(lambda m: given[m.group(1)], part) for part in command.run]
        if command.ask and not await self.ctx.ui.confirm(f"Run '{key}': {' '.join(argv)}?"):
            raise ToolError("not confirmed, nothing ran")
        return await self._execute(key, argv, command)

    @tool(permission=Permission.CONFIRM)
    async def shell(self, command: str, cwd: str = "", timeout: float = 120) -> dict[str, Any]:
        """Run any command line (PowerShell on Windows, sh on Linux/macOS) and return its
        exit code and output. `cwd`: the folder to run in (default: your home folder).
        Prefer a named command (commands.run) when one fits."""
        if not self.config.shell:
            raise ToolError(
                "running any command line is switched off; set `shell: true` in "
                "config/modules/commands.yaml, or use a named command (commands.list)"
            )
        line = command.strip()
        if not line or "\x00" in line:
            raise ToolError("give the command line to run")
        folder = Path(cwd).expanduser() if cwd.strip() else Path.home()
        if not folder.is_dir():
            raise ToolError(f"{folder} is not a folder")
        seconds = max(1.0, min(timeout, self.config.shell_timeout))
        run = Command(run=shell_argv(line), timeout=seconds, cwd=str(folder))
        self.ctx.logger.info("shell", command=line[:500], cwd=str(folder))
        result = await self._execute("shell", run.run, run)
        result["command"] = line
        return result

    async def _execute(self, key: str, argv: list[str], command: Command) -> dict[str, Any]:
        self.ctx.kill_switch.check()
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=command.cwd or None,
                creationflags=flags,
                start_new_session=sys.platform != "win32",  # one group to stop
            )
        except OSError as exc:
            raise ToolError(f"could not start {argv[0]}: {exc}") from exc
        job = Job(process.pid) if sys.platform == "win32" else None
        self._running[process] = job
        chunks: list[bytes] = []
        reader = asyncio.create_task(_read_all(process.stdout, chunks))
        timed_out = False
        try:
            try:
                async with asyncio.timeout(command.timeout):
                    # The output ends when the program and all it started are done.
                    await asyncio.shield(reader)
                    await process.wait()
            except TimeoutError:
                timed_out = True
                kill_tree(process, job)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(reader, 5)  # the rest of what it wrote
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), 5)
        except asyncio.CancelledError:
            kill_tree(process, job)
            reader.cancel()
            raise
        finally:
            self._running.pop(process, None)
            if job is not None:
                job.close()
        text = decode_output(b"".join(chunks))
        limit = self.config.max_output
        if len(text) > limit:
            text = "…" + text[-limit:]
        self.ctx.logger.info("command ran", command=key, exit_code=process.returncode)
        return {
            "command": key,
            "exit_code": process.returncode,
            "ok": process.returncode == 0 and not timed_out,
            "timed_out": timed_out,
            "seconds": round(time.monotonic() - started, 1),
            "output": text,
        }

    @tool
    async def add(
        self,
        name: str,
        run: list[str],
        description: str = "",
        ask: bool = True,
        timeout: float = 120,
    ) -> dict[str, Any]:
        """Add (or replace) a named command. `run` is the program and its arguments as a
        list, e.g. ["robocopy", "D:\\\\Photos", "E:\\\\Backup", "/MIR"]; `{name}` in an
        argument is filled in when it runs. `ask=false` lets it run without asking each
        time. Always asks you before saving."""
        key = name.strip().lower()
        if not NAME.match(key):
            raise ToolError("a name is lowercase letters, digits, - or _ (max 40)")
        try:
            command = Command(run=run, description=description, ask=ask, timeout=timeout)
        except ValueError as exc:
            raise ToolError(str(exc).splitlines()[-1]) from exc
        verb = "Replace" if key in self.config.commands else "Add"
        question = f"{verb} the command '{key}': {' '.join(run)}"
        question += (
            "? It will ask before each run." if ask else "? It will run WITHOUT asking each time."
        )
        if not await self.ctx.ui.confirm(question):
            raise ToolError("not confirmed, nothing changed")
        self.config.commands[key] = command
        self._save()
        return {"saved": key, "needs": command.placeholders(), "asks_first": ask}

    @tool
    def remove(self, name: str) -> dict[str, Any]:
        """Delete a named command."""
        key, _ = self._get(name)
        del self.config.commands[key]
        self._save()
        return {"removed": key}
