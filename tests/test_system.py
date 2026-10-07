"""The system module: how the PC is doing (read-only), and closing a program (asks first)."""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths


class User:
    def __init__(self, approve: bool) -> None:
        self.approve = approve

    async def confirm(self, question: str) -> bool:
        return self.approve

    async def ask(self, q: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("system")
    return paths


def sysmod(k: Kernel) -> Any:
    module = k.modules.records["system"].instance
    assert module is not None
    return module


async def test_byte_and_time_formatting(project: Paths) -> None:
    import sys as _sys

    async with Kernel(project, ui=User(True)) as k:
        helpers = _sys.modules[type(sysmod(k)).__module__]
    human_bytes, human_duration = helpers.human_bytes, helpers.human_duration
    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(5 * 1024**3) == "5.0 GB"
    assert human_bytes(None) is None
    assert human_duration(90) == "1m"
    assert human_duration(3600 * 26 + 60) == "1d 2h 1m"


async def test_info_and_disks(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        info = await k.dispatcher.call("system.info")
        disks = await k.dispatcher.call("system.disks")
    assert info.ok and info.output["cpu_cores"] >= 1
    assert "os" in info.output and isinstance(info.output["disks"], list)
    assert disks.ok and disks.output  # at least one drive
    assert all("free" in d and "total" in d for d in disks.output)


async def test_processes_includes_this_program(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        result = await k.dispatcher.call("system.processes", {"top": 50})
    assert result.ok and result.output  # a non-empty list of running programs
    assert all("name" in p and "memory" in p and "pid" in p for p in result.output)
    assert len(result.output) <= 50


async def test_close_app_asks_first_and_reports_no_match(project: Paths) -> None:
    async with Kernel(project, ui=User(False)) as k:
        denied = await k.dispatcher.call("system.close_app", {"name": "notepad"})
    assert not denied.ok and "not confirmed" in str(denied.error)  # CONFIRM gated

    async with Kernel(project, ui=User(True)) as k:
        missing = await k.dispatcher.call("system.close_app", {"name": "no_such_program_xyz"})
    assert not missing.ok and "no running program" in str(missing.error)


@pytest.mark.skipif(sys.platform == "win32", reason="uses the POSIX 'sleep' command")
async def test_close_app_closes_a_matching_program(project: Paths) -> None:
    pytest.importorskip("psutil")
    proc = subprocess.Popen(["sleep", "30"])
    time.sleep(0.3)
    try:
        async with Kernel(project, ui=User(True)) as k:
            result = await k.dispatcher.call("system.close_app", {"name": "sleep"})
        assert result.ok and proc.pid in result.output["pids"]
        assert proc.wait(timeout=5) is not None  # it was terminated
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
