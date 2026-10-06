from __future__ import annotations

import shutil
import textwrap
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from starlight.core import Kernel, Paths
from starlight.core.logging import shutdown_logging

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    """An empty Starlight project with only the module template."""
    p = Paths(tmp_path)
    p.ensure()
    shutil.copytree(REPO / "modules" / "_template", p.modules / "_template")
    p.config_file.write_text("kill_switch:\n  hotkey: null\n  poll_interval: 0.02\n")
    return p


type ModuleWriter = Callable[..., Path]


@pytest.fixture
def write_module(paths: Paths) -> ModuleWriter:
    """Write modules/<name>/ with a manifest and the given module.py source."""

    def write(name: str, source: str, **manifest: object) -> Path:
        folder = paths.modules / name
        folder.mkdir(exist_ok=True)
        lines = [f"name: {name}", "entrypoint: module:Mod"]
        lines += [f"{k}: {v}" for k, v in manifest.items()]
        (folder / "module.yaml").write_text("\n".join(lines) + "\n")
        (folder / "module.py").write_text(textwrap.dedent(source))
        return folder

    return write


@pytest.fixture
async def kernel(paths: Paths) -> AsyncIterator[Kernel]:
    k = Kernel(paths)
    await k.start()
    yield k
    await k.stop()


@pytest.fixture(autouse=True)
def _close_logs() -> object:
    yield
    shutdown_logging()


def read_audit(paths: Paths) -> list[str]:
    import json

    file = paths.logs / "audit.log"
    if not file.exists():
        return []
    return [json.loads(line)["event"] for line in file.read_text().splitlines()]
