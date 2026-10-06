"""Run generated code away from everything that matters (Step 9).

* ``docker``: a throw-away container with no network (unless allowed), a read-only root
  filesystem, capped memory, CPU and process count, no capabilities and an unprivileged
  user. This is real isolation; use it when Docker Desktop is installed.
* ``process``: a separate Python process with a time limit, an empty environment, its own
  temporary folder, and audit hooks that block network access, the vault (keyring), the
  project's config/data/memory folders, ~/.ssh and starting other programs. This stops
  mistakes, not a determined attacker: prefer Docker.
* ``auto``: Docker if it is running, otherwise the process sandbox.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

GUARD = Path(__file__).with_name("guard.py")
GUARD_NAME = "_starlight_guard.py"


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    backend: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def summary(self, limit: int = 3000) -> str:
        text = f"exit code {self.exit_code}" + (" (timed out)" if self.timed_out else "")
        if self.stdout.strip():
            text += f"\n--- stdout ---\n{self.stdout[-limit:]}"
        if self.stderr.strip():
            text += f"\n--- stderr ---\n{self.stderr[-limit:]}"
        return text


class Sandbox(Protocol):
    name: str

    async def run(
        self,
        files: dict[str, str],
        command: list[str],
        timeout: float = 60.0,
        network: bool = False,
    ) -> RunResult:
        """Write ``files`` into a fresh folder and run ``command`` there.
        ``command[0] == "python"`` runs Python inside the sandbox."""
        ...

    def kill_all(self) -> None: ...


def _safe_files(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        target = (root / name).resolve()
        if not target.is_relative_to(root.resolve()) or name.startswith(GUARD_NAME):
            raise ValueError(f"invalid file name: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def python_command(command: list[str]) -> list[str]:
    """`pytest -q` and `python3 x.py` as the sandbox runs them."""
    if command and command[0] in ("pytest", "py.test"):
        return ["python", "-m", "pytest", *command[1:]]
    if command and command[0] in ("python3", "py"):
        return ["python", *command[1:]]
    return command


def _tail(data: bytes, limit: int = 20000) -> str:
    return data.decode(errors="replace")[-limit:]


class ProcessSandbox:
    name = "process"

    def __init__(self, work_root: Path, deny: list[Path], memory_mb: int = 1024) -> None:
        self.work_root = work_root
        self.deny = [str(p.resolve()) for p in deny]
        self.memory_mb = memory_mb
        self._procs: set[asyncio.subprocess.Process] = set()

    def _env(self, folder: Path, network: bool) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(folder),
            "USERPROFILE": str(folder),
            "APPDATA": str(folder),
            "LOCALAPPDATA": str(folder),
            "TEMP": str(folder),
            "TMP": str(folder),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "STARLIGHT_SANDBOX": json.dumps(
                {
                    "deny": self.deny,
                    "network": network,
                    "workdir": str(folder),
                    "memory_mb": self.memory_mb,
                }
            ),
        }
        for key in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC"):  # Windows needs these
            if key in os.environ:
                env[key] = os.environ[key]
        return env

    async def run(
        self,
        files: dict[str, str],
        command: list[str],
        timeout: float = 60.0,
        network: bool = False,
    ) -> RunResult:
        command = python_command(command)
        if not command or command[0] not in ("python", "python3"):
            raise ValueError("the process sandbox only runs Python (use Docker for more)")
        folder = self.work_root / uuid.uuid4().hex[:12]
        folder.mkdir(parents=True)
        try:
            _safe_files(folder, files)
            shutil.copy(GUARD, folder / GUARD_NAME)
            argv = [sys.executable, "-I", "-X", "utf8", GUARD_NAME, *command[1:]]
            started = time.perf_counter()
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=folder,
                env=self._env(folder, network),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._procs.add(proc)
            timed_out = False
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout)
            except TimeoutError:
                timed_out = True
                proc.kill()
                out, err = await proc.communicate()
            finally:
                self._procs.discard(proc)
            return RunResult(
                proc.returncode if proc.returncode is not None else -1,
                _tail(out),
                _tail(err),
                timed_out,
                round(time.perf_counter() - started, 3),
                self.name,
            )
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def kill_all(self) -> None:
        for proc in list(self._procs):
            with contextlib.suppress(ProcessLookupError):
                proc.kill()


class DockerSandbox:
    name = "docker"

    def __init__(
        self,
        work_root: Path,
        image: str = "python:3.12-slim",
        memory_mb: int = 1024,
        cpus: float = 1.0,
        docker: str = "docker",
    ) -> None:
        self.work_root = work_root
        self.image = image
        self.memory_mb = memory_mb
        self.cpus = cpus
        self.docker = docker
        self._containers: set[str] = set()

    async def run(
        self,
        files: dict[str, str],
        command: list[str],
        timeout: float = 60.0,
        network: bool = False,
    ) -> RunResult:
        folder = self.work_root / uuid.uuid4().hex[:12]
        folder.mkdir(parents=True)
        name = f"starlight-{folder.name}"
        command = python_command(command)
        try:
            _safe_files(folder, files)
            if os.name == "posix":  # the container's unprivileged user must write here
                for path in [folder, *folder.rglob("*")]:
                    path.chmod(0o777 if path.is_dir() else 0o666)
            argv = [
                self.docker, "run", "--rm", "--name", name,
                "--network", "bridge" if network else "none",
                "--memory", f"{self.memory_mb}m", "--cpus", str(self.cpus),
                "--pids-limit", "128", "--read-only", "--tmpfs", "/tmp:rw,size=256m",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--user", "65534:65534", "-e", "HOME=/tmp", "-e", "PYTHONDONTWRITEBYTECODE=1",
                "-v", f"{folder.resolve()}:/work", "-w", "/work",
                self.image, *command,
            ]  # fmt: skip
            started = time.perf_counter()
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            self._containers.add(name)
            timed_out = False
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout)
            except TimeoutError:
                timed_out = True
                self._kill(name)
                out, err = await proc.communicate()
            finally:
                self._containers.discard(name)
            return RunResult(
                proc.returncode if proc.returncode is not None else -1,
                _tail(out),
                _tail(err),
                timed_out,
                round(time.perf_counter() - started, 3),
                self.name,
            )
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def _kill(self, name: str) -> None:
        subprocess.run([self.docker, "kill", name], capture_output=True, timeout=15, check=False)

    def kill_all(self) -> None:
        for name in list(self._containers):
            self._kill(name)


def docker_available(docker: str = "docker") -> bool:
    """Docker is running *and* runs Linux containers (Docker Desktop on Windows can be
    switched to Windows containers, which cannot run the Python image)."""
    if shutil.which(docker) is None:
        return False
    try:
        result = subprocess.run(
            [docker, "info", "--format", "{{.OSType}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "linux"


def create_sandbox(
    kind: Literal["auto", "docker", "process"],
    work_root: Path,
    deny: list[Path],
    image: str = "python:3.12-slim",
    memory_mb: int = 1024,
) -> Sandbox:
    if kind == "docker" or (kind == "auto" and docker_available()):
        return DockerSandbox(work_root, image, memory_mb)
    return ProcessSandbox(work_root, deny, memory_mb)
