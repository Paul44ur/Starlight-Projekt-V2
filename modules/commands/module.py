"""Your own named commands: "run backup-photos", "restart the game server".

Starlight can run only the commands listed in config/modules/commands.yaml - never any other
program or shell line. Each command is a fixed program with fixed arguments; `{name}`
placeholders take a value when it runs (one argument each, never parsed by a shell, never
starting with "-"). A command asks before it runs unless you set `ask: false` for it.
Adding or changing a command always asks you. The kill switch stops a running command.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import sys
import time
from typing import Any

from pydantic import BaseModel, Field, field_validator

from starlight.core import Module, ModuleContext, ToolError, tool
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


class CommandsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self._running: set[asyncio.subprocess.Process] = set()
        ctx.kill_switch.add_cleanup(self._kill_all, owner=ctx.name)

    def _kill_all(self) -> None:
        for process in list(self._running):
            with contextlib.suppress(ProcessLookupError):
                process.kill()

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
                "commands": {
                    k: v.model_dump(exclude_defaults=True) for k, v in self.config.commands.items()
                },
            },
            "# The commands module (modules/commands): your own named commands. Starlight\n"
            "# can run only what is listed here.",
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
            )
        except OSError as exc:
            raise ToolError(f"could not start {argv[0]}: {exc}") from exc
        self._running.add(process)
        timed_out = False
        try:
            output, _ = await asyncio.wait_for(process.communicate(), command.timeout)
        except TimeoutError:
            timed_out = True
            process.kill()
            output, _ = await process.communicate()
        except asyncio.CancelledError:
            process.kill()
            raise
        finally:
            self._running.discard(process)
        text = output.decode("utf-8", errors="replace")
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
