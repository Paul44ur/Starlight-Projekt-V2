from __future__ import annotations

import gc
import os
import shutil
import textwrap
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path

import pytest

from starlight.core import Kernel, Paths
from starlight.core.logging import shutdown_logging

REPO = Path(__file__).resolve().parent.parent


# --- Running the tests side by side (`pytest -n 4`) ---------------------------------------
# Every test gets an xdist group: its own file by default, so a file's tests stay in one
# worker and module-scoped fixtures keep working (like `--dist loadfile`). Files that must
# not overlap with each other share one named group, so they run one after another in a
# single worker. Without `-n` (or without xdist) the groups do nothing.
ONE_WORKER = "one-worker"
# The files in the one-worker group, each with the reason.
ONE_WORKER_FILES: dict[str, str] = {
    # The window tests share ONE X display (under xvfb) and fight over the keyboard focus
    # and the pointer when two windows are open at once. The files below import tkinter or
    # launcher_gui; most of their tests open a real window.
    "test_launcher_look.py": "tkinter window",
    "test_launcher_window.py": "tkinter window",
    "test_launcher_tray.py": "tkinter window",
    "test_launcher_model.py": "tkinter window",
    "test_launcher_night_sky.py": "tkinter window",
    "test_cleanup_launcher.py": "tkinter window",
    "test_app_window.py": "tkinter window",
    "test_deep_links.py": "tkinter window",
    "test_language_access.py": "tkinter window",
    "test_lmstudio.py": "tkinter window",
    "test_regressions.py": "tkinter window",
    "test_sweep_fixes.py": "tkinter window",
    "test_ui_layout.py": "tkinter window",
}


def xdist_group_for(path: str) -> str:
    """The xdist group of a test file (its path as pytest reports it)."""
    name = Path(path).name
    return ONE_WORKER if name in ONE_WORKER_FILES else name


def pytest_configure(config: pytest.Config) -> None:
    # The marker is registered by xdist itself; without xdist, say it here (no warning).
    config.addinivalue_line("markers", "xdist_group(name): run these tests in one xdist worker")
    if not config.pluginmanager.hasplugin("xdist"):
        return
    # `-n 4` alone should keep groups together, as `-n 4 --dist loadgroup` does. Without
    # `-n` nothing changes. The controller picks the scheduler here; a worker re-reads the
    # command line (where `--dist` is not given), so it is told to add the group to the
    # test ids, which is what the scheduler sorts by.
    if getattr(config.option, "dist", "no") == "load":
        config.option.dist = "loadgroup"
    if hasattr(config, "workerinput"):
        config.option.loadgroup = True


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if item.get_closest_marker("xdist_group") is None:
            item.add_marker(pytest.mark.xdist_group(name=xdist_group_for(str(item.path))))


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
def documents(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for the user's Documents folder, where Starlight saves the files it
    makes: tests never write into the real one."""
    folder = tmp_path_factory.mktemp("Documents").resolve()
    monkeypatch.setattr("starlight.core.output.documents_folder", lambda: folder)
    return folder


@pytest.fixture(autouse=True)
def _environment_back() -> Iterator[None]:
    """Code under test may change environment variables for the whole process (the
    launcher sets UV_CACHE_DIR, for example). They are put back after each test, so none
    reaches the next one: a leaked one once made a later test download every package again."""
    before = dict(os.environ)
    yield
    if dict(os.environ) != before:
        os.environ.clear()
        os.environ.update(before)


@pytest.fixture(autouse=True)
def _close_logs() -> object:
    yield
    shutdown_logging()
    # Collect each test's garbage right away: a bad finalizer (e.g. a native handle freed
    # at the wrong time) then fails in the test that created it, not in a later one.
    gc.collect()


def read_audit(paths: Paths) -> list[str]:
    import json

    file = paths.logs / "audit.log"
    if not file.exists():
        return []
    return [json.loads(line)["event"] for line in file.read_text().splitlines()]


@pytest.fixture
def use_module(paths: Paths) -> Callable[[str], Path]:
    """Copy one of the real modules from the repository into the test project."""

    def copy(name: str) -> Path:
        target = paths.modules / name
        shutil.copytree(
            REPO / "modules" / name, target, ignore=shutil.ignore_patterns("__pycache__")
        )
        return target

    return copy


class ScriptedProvider:
    """Plays back a list of responses and records what it was sent."""

    name = "scripted"

    def __init__(self, *responses: object) -> None:
        from starlight.core.model import ModelResponse

        self.responses = [
            r if isinstance(r, ModelResponse) else ModelResponse(content=str(r)) for r in responses
        ]
        self.calls: list[list[object]] = []

    async def chat(self, messages: list[object], tools: list[dict[str, object]]) -> object:
        from starlight.core.model import ModelResponse

        self.calls.append(list(messages))
        return self.responses.pop(0) if self.responses else ModelResponse(content="done")
