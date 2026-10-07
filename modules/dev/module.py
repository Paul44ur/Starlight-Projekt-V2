"""Developer tools: git, Docker and network ports.

Reading is free: git status/log/diff/branches, the list of containers and their logs and
stats, and which program listens on which port. Changing things asks first: git commit and
pull, and starting, stopping or restarting a container. Commands are run directly (no shell)
with a time limit, and git never waits for a password prompt.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, Permission, ToolError, tool
from starlight.core.safepaths import expand, inside

CONTAINER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class Config(BaseModel):
    repos: dict[str, str] = Field(default_factory=dict)
    git: str = "git"
    docker: str = "docker"
    timeout: float = Field(default=60, gt=0)
    max_output: int = Field(default=12000, ge=1000)


class DevModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    async def _exec(self, program: str, *args: str, cwd: Path | None = None) -> str:
        """Run a program and return its output; a non-zero exit is a ToolError."""
        self.ctx.kill_switch.check()
        found = shutil.which(program)
        if found is None:
            raise ToolError(f"'{program}' is not installed (or not on PATH)")
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_PAGER": "cat", "LC_ALL": "C"}
        flags = 0x08000000 if sys.platform == "win32" else 0  # no console window
        process = await asyncio.create_subprocess_exec(
            found, *args, cwd=cwd, env=env, creationflags=flags,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        try:
            out, err = await asyncio.wait_for(process.communicate(), self.config.timeout)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ToolError(f"{program} took longer than {self.config.timeout:.0f} s") from exc
        text = out.decode("utf-8", errors="replace")
        if process.returncode != 0:
            message = (err.decode("utf-8", errors="replace") or text).strip()
            raise ToolError(message[-2000:] or f"{program} failed ({process.returncode})")
        limit = self.config.max_output
        return text if len(text) <= limit else text[:limit] + "\n… (cut)"

    # -- git -----------------------------------------------------------------------------

    def _repo(self, repo: str) -> Path:
        name = repo.strip()
        if not name:
            raise ToolError("which repository? a folder path or a name from dev.repos")
        path = expand(self.config.repos.get(name.lower(), name))
        if not path.is_dir():
            raise ToolError(f"{path} is not a folder")
        return path

    def _writable(self, path: Path) -> None:
        if inside(path, self.ctx.paths.root.resolve()):
            raise ToolError("Starlight does not change its own folder; do that yourself")

    async def _git(self, repo: str, *args: str) -> str:
        return await self._exec(self.config.git, "-C", str(self._repo(repo)), *args)

    @tool
    def repos(self) -> dict[str, str]:
        """Your named repositories (set in config/modules/dev.yaml)."""
        return dict(self.config.repos)

    @tool
    async def git_status(self, repo: str) -> dict[str, Any]:
        """The branch, how far ahead/behind it is, and the changed files of a repository
        (a folder path or a name from dev.repos)."""
        out = await self._git(repo, "status", "--porcelain=v1", "--branch")
        lines = out.splitlines()
        branch = lines[0][3:] if lines and lines[0].startswith("## ") else ""
        changes = [{"state": line[:2].strip() or "?", "file": line[3:]} for line in lines[1:]]
        return {"branch": branch, "clean": not changes, "changes": changes[:300]}

    @tool
    async def git_log(self, repo: str, count: int = 10, file: str = "") -> dict[str, Any]:
        """The latest commits (hash, date, author, message), optionally for one file."""
        args = [
            "log",
            f"-{max(1, min(count, 100))}",
            "--date=short",
            "--pretty=format:%h|%ad|%an|%s",
        ]
        if file.strip():
            args += ["--", file.strip()]
        out = await self._git(repo, *args)
        commits = []
        for line in out.splitlines():
            parts = line.split("|", 3)
            if len(parts) == 4:
                commits.append(dict(zip(("hash", "date", "author", "message"), parts, strict=True)))
        return {"commits": commits}

    @tool
    async def git_diff(self, repo: str, staged: bool = False, file: str = "") -> dict[str, Any]:
        """The uncommitted changes (or the staged ones), optionally for one file."""
        args = ["diff", "--stat", "--patch", *(["--cached"] if staged else [])]
        if file.strip():
            args += ["--", file.strip()]
        return {"diff": await self._git(repo, *args) or "(no changes)"}

    @tool
    async def git_branches(self, repo: str) -> dict[str, Any]:
        """Local branches and which one is checked out."""
        out = await self._git(repo, "branch", "--format=%(HEAD)|%(refname:short)|%(upstream:short)")
        branches = []
        for line in out.splitlines():
            head, name, upstream = [*line.split("|"), "", ""][:3]
            branches.append({"name": name, "current": head == "*", "tracks": upstream})
        return {"branches": branches}

    @tool(permission=Permission.CONFIRM)
    async def git_pull(self, repo: str) -> dict[str, Any]:
        """Fetch and merge the latest changes of the current branch (fast-forward only).
        Asks first."""
        path = self._repo(repo)
        self._writable(path)
        return {"output": await self._exec(self.config.git, "-C", str(path), "pull", "--ff-only")}

    @tool(permission=Permission.CONFIRM)
    async def git_commit(
        self, repo: str, message: str, all_changes: bool = False
    ) -> dict[str, Any]:
        """Commit the staged changes (or, with `all_changes`, every change to tracked
        files) with `message`. Never pushes. Asks first."""
        path = self._repo(repo)
        self._writable(path)
        if not message.strip():
            raise ToolError("give a commit message")
        args = ["-C", str(path), "commit", "-m", message.strip(), *(["-a"] if all_changes else [])]
        return {"output": await self._exec(self.config.git, *args)}

    # -- docker --------------------------------------------------------------------------

    def _container(self, name: str) -> str:
        if not CONTAINER.match(name.strip()):
            raise ToolError("give a container name or id (see dev.containers)")
        return name.strip()

    @tool
    async def containers(self, all: bool = False) -> dict[str, Any]:
        """Docker containers: name, image, state and ports (`all` includes stopped ones)."""
        args = ["ps", "--format", "{{.Names}}|{{.Image}}|{{.Status}}|{{.Ports}}"]
        out = await self._exec(self.config.docker, *args, *(["-a"] if all else []))
        rows = [line.split("|", 3) for line in out.splitlines() if line.count("|") >= 3]
        return {
            "containers": [
                dict(zip(("name", "image", "status", "ports"), row, strict=True)) for row in rows
            ]
        }

    @tool
    async def container_logs(self, container: str, lines: int = 50) -> dict[str, Any]:
        """The last lines of a container's log."""
        tail = str(max(1, min(lines, 1000)))
        out = await self._exec(
            self.config.docker, "logs", "--tail", tail, self._container(container)
        )
        return {"container": container, "log": out}

    @tool
    async def container_stats(self) -> dict[str, Any]:
        """CPU and memory use of the running containers."""
        out = await self._exec(
            self.config.docker, "stats", "--no-stream",
            "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}",
        )  # fmt: skip
        rows = [line.split("|", 2) for line in out.splitlines() if line.count("|") == 2]
        return {"stats": [dict(zip(("name", "cpu", "memory"), r, strict=True)) for r in rows]}

    @tool(permission=Permission.CONFIRM)
    async def container_restart(self, container: str) -> dict[str, Any]:
        """Restart a container. Asks first."""
        await self._exec(self.config.docker, "restart", self._container(container))
        return {"restarted": container}

    @tool(permission=Permission.CONFIRM)
    async def container_stop(self, container: str) -> dict[str, Any]:
        """Stop a container. Asks first."""
        await self._exec(self.config.docker, "stop", self._container(container))
        return {"stopped": container}

    @tool(permission=Permission.CONFIRM)
    async def container_start(self, container: str) -> dict[str, Any]:
        """Start a stopped container. Asks first."""
        await self._exec(self.config.docker, "start", self._container(container))
        return {"started": container}

    # -- ports ---------------------------------------------------------------------------

    @tool
    def ports(self, port: int = 0) -> dict[str, Any]:
        """Which programs listen on which network ports on this PC (`port` for one)."""
        try:
            import psutil
        except ImportError as exc:
            raise ToolError("this needs the 'system' extra (psutil)") from exc
        listening: dict[tuple[str, int], dict[str, Any]] = {}
        try:
            connections = psutil.net_connections(kind="inet")
        except psutil.AccessDenied as exc:
            raise ToolError("the system did not allow reading the port list") from exc
        for conn in connections:
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            if port and conn.laddr.port != port:
                continue
            program = ""
            if conn.pid:
                try:
                    program = psutil.Process(conn.pid).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    program = "?"
            key = (conn.laddr.ip, conn.laddr.port)
            listening[key] = {
                "port": conn.laddr.port,
                "address": conn.laddr.ip,
                "program": program,
                "pid": conn.pid,
                "reachable_from": "this PC only"
                if conn.laddr.ip in ("127.0.0.1", "::1")
                else "the network",
            }
        found = sorted(listening.values(), key=lambda r: (r["port"], r["address"]))
        if port and not found:
            return {"port": port, "in_use": False}
        return {"count": len(found), "listening": found}
