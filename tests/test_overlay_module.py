"""The overlay module (docs/plan-overlay.md 4.4, package W3): it starts and watches the
overlay process, forwards the voice module's events, and points at targets.

The overlay process here is a fake: a small Python script that answers "hello" with
"ready", writes every line it receives into a file and can be told to crash or to stay
silent. The desktop module is a stand-in whose ``find_target`` gives scripted answers. No
Qt anywhere.
"""

from __future__ import annotations

import asyncio
import importlib.util
import itertools
import json
import sys
import textwrap
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, KillSwitchEngaged, Paths, ToolError
from starlight.core.modules import discover, import_module_class
from tests.conftest import REPO, ModuleWriter
from tests.test_home_devices import call, fails, instance

# The module starts `sys.executable -m starlight.overlay`. The tests point sys.executable at
# a script with a "#!" line, which Windows cannot run.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake child is a #! script")

FAKE_CHILD = """
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
mode = open(os.path.join(here, "mode")).read().strip()
with open(os.path.join(here, "starts"), "a") as f:
    f.write(f"{time.time()}\\n")
if mode == "crash":
    sys.exit(3)
received = open(os.path.join(here, "received"), "a")
for line in sys.stdin:
    received.write(f"{time.time()} {line}")
    received.flush()
    msg = json.loads(line)
    if msg["type"] == "hello" and mode != "silent":
        ready = {"type": "ready", "v": 1, "exclude_from_capture": mode != "visible",
                 "screens": [{"name": "1", "x": 0, "y": 0, "w": 1920, "h": 1080, "dpr": 1.0},
                             {"name": "2", "x": 1920, "y": 0, "w": 1920, "h": 1080, "dpr": 1.0}]}
        print(json.dumps(ready), flush=True)
        print("not json", flush=True)  # skipped, never fatal
    elif msg["type"] == "ask":
        answer = {"type": "answer", "id": msg["id"], "choice": msg["choices"][0]["id"]}
        print(json.dumps(answer), flush=True)
    elif msg["type"] == "quit":
        sys.exit(0)
"""

FAKE_DESKTOP = '''
import time
from typing import Any
from starlight.core import Module, tool

class Mod(Module):
    async def setup(self, ctx):
        self.calls: list[dict[str, Any]] = []
        self.called_at: list[float] = []
        self.answers: list[dict[str, Any]] = []

    @tool
    def find_target(self, target: str, window: str = "") -> dict[str, Any]:
        """Stand-in for W4's find_target."""
        self.calls.append({"target": target, "window": window})
        self.called_at.append(time.time())
        if self.answers:
            return self.answers.pop(0)
        return {"found": True, "box": [2100, 40, 90, 30], "monitor": 2, "method": "uia",
                "label": "Einstellungen", "confidence": "sure", "window": "Editor"}
'''

MISS = {"found": False, "target": "rocket", "tried": ["uia", "ocr"], "windows": ["Editor"]}


class Child:
    """The fake overlay process's folder: what it was told and how often it started."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.script = folder / "python"

    def mode(self, mode: str) -> None:
        (self.folder / "mode").write_text(mode)

    def start_times(self) -> list[float]:
        file = self.folder / "starts"
        return [float(t) for t in file.read_text().split()] if file.exists() else []

    @property
    def starts(self) -> int:
        return len(self.start_times())

    def timed(self) -> list[tuple[float, dict[str, Any]]]:
        """Each line received, with the time it arrived."""
        file = self.folder / "received"
        if not file.exists():
            return []
        lines = [line.split(" ", 1) for line in file.read_text().splitlines()]
        return [(float(at), json.loads(msg)) for at, msg in lines]

    def received(self) -> list[dict[str, Any]]:
        return [msg for _, msg in self.timed()]

    def types(self) -> list[str]:
        return [m["type"] for m in self.received()]


async def until(check: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


@pytest.fixture
def child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Child:
    fake = Child(tmp_path / "child")
    fake.folder.mkdir()
    fake.script.write_text(f"#!{sys.executable}\n" + textwrap.dedent(FAKE_CHILD))
    fake.script.chmod(0o755)
    fake.mode("ok")
    monkeypatch.setattr(sys, "executable", str(fake.script))
    # The overlay extra counts as installed (only PySide6 is pretended).
    real = importlib.util.find_spec

    def find_spec(name: str, package: str | None = None) -> Any:
        return object() if name == "PySide6" else real(name, package)

    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    return fake


def project(
    paths: Paths, use_module: Callable[[str], Path], write_module: ModuleWriter, **config: object
) -> None:
    if not (paths.modules / "overlay").exists():
        use_module("overlay")
        write_module("desktop", FAKE_DESKTOP)
    settings = {"enabled": True, "point_seconds": 5, **config}
    (paths.module_configs / "overlay.yaml").write_text(
        "".join(f"{k}: {json.dumps(v)}\n" for k, v in settings.items())
    )


@pytest.fixture
async def started(
    paths: Paths, use_module: Callable[[str], Path], write_module: ModuleWriter, child: Child
) -> AsyncIterator[Kernel]:
    project(paths, use_module, write_module)
    async with Kernel(paths) as k:
        await until(lambda: instance(k, "overlay").running)
        yield k


def answers(k: Kernel) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    async def keep(event: Any) -> None:
        seen.append(event.data)

    k.bus.subscribe("overlay.answer", keep)
    return seen


# -- starting, restarting, stopping ---------------------------------------------------------


async def test_it_starts_the_overlay_and_says_hello(started: Kernel, child: Child) -> None:
    hello = child.received()[0]
    assert hello == {
        "type": "hello",
        "v": 1,
        "accent": "#4FC8FF",
        "orb_monitor": 1,
        "orb": "always",
        "reduce_motion": False,
        "language": "de",
    }
    status = await call(started, "overlay.overlay_status")
    assert status["running"] and status["enabled"] and status["installed"]
    assert [s["x"] for s in status["screens"]] == [0, 1920]
    assert status["hidden_from_screenshots"] is True


async def test_off_or_not_installed_starts_nothing(
    paths: Paths,
    use_module: Callable[[str], Path],
    write_module: ModuleWriter,
    child: Child,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project(paths, use_module, write_module, enabled=False)
    async with Kernel(paths) as k:
        await asyncio.sleep(0.3)
        assert child.starts == 0
        # The model then answers in words: the hit, but not shown.
        shown = await call(k, "overlay.point_at", target="settings")
        assert shown["found"] and shown["shown"] is False
        assert (await call(k, "overlay.overlay_status"))["running"] is False
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, package=None: None)
    project(paths, use_module, write_module)
    async with Kernel(paths) as k:
        await asyncio.sleep(0.3)
        assert child.starts == 0
        status = await call(k, "overlay.overlay_status")
        assert not status["installed"] and "not installed" in status["last_error"]


def gaps(times: list[float]) -> list[float]:
    return [round(b - a, 2) for a, b in itertools.pairwise(times)]


async def test_a_crash_restarts_it_with_growing_waits_then_it_gives_up(
    started: Kernel, child: Child
) -> None:
    overlay = instance(started, "overlay")
    overlay.backoff_first = 0.1
    child.mode("crash")  # every new start crashes at once
    overlay._proc.kill()
    await until(lambda: overlay._gave_up)
    assert child.starts == 5  # the first start, then four restarts; the 5th failure stops it
    waits = gaps(child.start_times())[1:]  # (the first gap is the test's own time)
    assert len(waits) == 3
    for wait, expected in zip(waits, [0.2, 0.4, 0.8], strict=True):  # doubled each time
        assert expected <= wait < expected + 0.15, waits
    status = await call(started, "overlay.overlay_status")
    assert status["gave_up"] and not status["running"] and "exit code 3" in status["last_error"]
    await asyncio.sleep(1.0)
    assert child.starts == 5  # and it stays stopped


async def test_the_wait_has_a_ceiling_and_old_failures_are_forgotten(
    started: Kernel, child: Child
) -> None:
    overlay = instance(started, "overlay")
    overlay.backoff_first, overlay.backoff_max = 0.05, 0.1
    overlay.give_up_window = 0.12  # only the failures of the last 0.12 s count
    child.mode("crash")
    overlay._proc.kill()
    await until(lambda: child.starts >= 9)
    assert not overlay._gave_up
    for wait in gaps(child.start_times())[1:]:
        assert wait < 0.1 + 0.1  # never longer than the ceiling (plus the start itself)


async def test_after_a_crash_it_comes_back_and_says_hello_again(
    started: Kernel, child: Child
) -> None:
    overlay = instance(started, "overlay")
    overlay.backoff_first = 0.05
    overlay._proc.kill()
    await until(lambda: child.starts == 2 and overlay.running)
    overlay._proc.kill()
    await until(lambda: child.starts == 3 and overlay.running)
    assert child.types().count("hello") == 3
    assert overlay._restarts == 2 and not overlay._gave_up


async def test_a_silent_child_is_stopped_after_the_wait_for_ready(
    paths: Paths, use_module: Callable[[str], Path], write_module: ModuleWriter, child: Child
) -> None:
    child.mode("silent")
    project(paths, use_module, write_module)
    async with Kernel(paths) as k:
        overlay = instance(k, "overlay")
        overlay.ready_timeout = 0.3  # (the first try still waits the full 10 s otherwise)
        overlay.backoff_first = 0.05
        await until(lambda: "no answer" in overlay.last_error, seconds=15)
        assert not overlay.running


async def test_shutdown_says_quit(
    paths: Paths, use_module: Callable[[str], Path], write_module: ModuleWriter, child: Child
) -> None:
    project(paths, use_module, write_module)
    async with Kernel(paths) as k:
        overlay = instance(k, "overlay")
        await until(lambda: overlay.running)
        proc = overlay._proc
    assert child.types()[-1] == "quit"
    assert proc.returncode == 0  # it ended by itself, not killed


# -- pointing ---------------------------------------------------------------------------------


async def test_point_at_a_hit_draws_a_line_and_a_caption(started: Kernel, child: Child) -> None:
    desktop = instance(started, "desktop")
    hit = await call(started, "overlay.point_at", target="settings", window="Editor")
    assert desktop.calls == [{"target": "settings", "window": "Editor"}]
    assert hit["found"] and hit["shown"] is True and hit["box"] == [2100, 40, 90, 30]
    await until(lambda: "caption" in child.types())
    point, caption = child.received()[-2:]
    assert point["type"] == "point" and point["box"] == [2100, 40, 90, 30]
    assert point["label"] == "Einstellungen" and point["confidence"] == "sure"
    assert point["seconds"] == 5
    assert caption == {
        "type": "caption",
        "text": "Hier ist es: Einstellungen",
        "who": "starlight",
        "seconds": 5,
    }
    overlay = instance(started, "overlay")
    assert overlay.last_target["label"] == "Einstellungen"
    assert time.time() - overlay.last_target["at"] < 5
    # A "maybe" says so.
    desktop.answers.append({**desktop.find_target("x"), "confidence": "maybe", "label": "Datei"})
    await call(started, "overlay.point_at", target="file")
    await until(lambda: child.types().count("caption") == 2)
    assert child.received()[-1]["text"] == "Vielleicht hier: Datei"
    assert child.received()[-2]["confidence"] == "maybe"
    # clear_pointer takes it down.
    assert (await call(started, "overlay.clear_pointer")) == {"cleared": True}
    await until(lambda: child.types()[-1] == "clear")


async def test_a_miss_is_returned_unchanged_and_points_at_nothing(
    started: Kernel, child: Child
) -> None:
    instance(started, "desktop").answers.append(dict(MISS))
    before = child.types()
    assert await call(started, "overlay.point_at", target="rocket") == MISS
    await asyncio.sleep(0.2)
    assert child.types() == before
    assert instance(started, "overlay").last_target is None


async def test_a_bad_box_from_find_target_is_not_drawn(started: Kernel, child: Child) -> None:
    desktop = instance(started, "desktop")
    desktop.answers.append({**desktop.find_target("x"), "box": [10, 10, 0, 30]})
    before = child.types()
    hit = await call(started, "overlay.point_at", target="settings")
    assert hit["shown"] is False
    await asyncio.sleep(0.2)
    assert child.types() == before


async def test_when_the_overlay_shows_in_screenshots_it_clears_first(
    paths: Paths, use_module: Callable[[str], Path], write_module: ModuleWriter, child: Child
) -> None:
    child.mode("visible")  # ready says exclude_from_capture: false
    project(paths, use_module, write_module)
    async with Kernel(paths) as k:
        await until(lambda: instance(k, "overlay").running)
        await call(k, "overlay.point_at", target="settings")
        await until(lambda: "caption" in child.types())
        [screenshot] = instance(k, "desktop").called_at
        cleared = [at for at, msg in child.timed() if msg["type"] == "clear"]
        assert len(cleared) == 1 and screenshot - cleared[0] >= 0.14
        assert child.types()[-3:] == ["clear", "point", "caption"]


async def test_hidden_from_screenshots_it_does_not_clear_first(
    started: Kernel, child: Child
) -> None:
    await call(started, "overlay.point_at", target="settings")
    await until(lambda: "caption" in child.types())
    assert "clear" not in child.types()


async def test_find_target_failing_is_an_error(started: Kernel) -> None:
    await started.modules.unload("desktop")
    assert "desktop.find_target" in await fails(started, "overlay.point_at", target="settings")


# -- kill switch ------------------------------------------------------------------------------


async def test_the_kill_switch_clears_cancels_the_question_and_blocks_pointing(
    started: Kernel, child: Child
) -> None:
    seen = answers(started)
    overlay = instance(started, "overlay")
    overlay._open_asks.add("q1")  # an open question the child has not answered
    started.kill_switch.trigger("test", source="test")
    await until(lambda: child.types()[-1:] == ["state"])
    assert child.types()[-3:] == ["clear", "cancel_ask", "state"]
    assert child.received()[-2] == {"type": "cancel_ask", "id": "q1"}
    assert child.received()[-1] == {"type": "state", "state": "asleep"}
    await until(lambda: seen == [{"id": "q1", "choice": None}])  # answered "no"
    with pytest.raises(KillSwitchEngaged):
        await started.dispatcher.call("overlay.point_at", {"target": "settings"})
    with pytest.raises(ToolError):
        await overlay.point_at("settings")
    # Events are not forwarded while it is engaged.
    before = len(child.received())
    await started.bus.publish("voice.state", {"state": "listening"}, source="voice")
    await asyncio.sleep(0.2)
    assert len(child.received()) == before
    started.kill_switch.reset()


# -- events -----------------------------------------------------------------------------------


async def test_voice_events_are_forwarded(started: Kernel, child: Child) -> None:
    bus = started.bus
    await bus.publish("voice.state", {"state": "listening"}, source="voice")
    await bus.publish("voice.level", {"level": 0.5}, source="voice")
    await bus.publish("voice.caption", {"text": "wo ist das Menü", "who": "you"}, source="voice")
    await bus.publish("voice.speech", {"envelope": [0.1, 0.9], "rate": 20}, source="voice")
    await bus.publish("voice.state", {"state": "nonsense"}, source="voice")  # skipped
    await bus.publish("voice.caption", {"who": "you"}, source="voice")  # skipped
    await bus.publish("voice.state", {"state": "idle"}, source="voice")
    await until(lambda: child.types()[-1:] == ["state"] and len(child.types()) >= 6)
    assert child.received()[1:] == [
        {"type": "state", "state": "listening"},
        {"type": "level", "level": 0.5},
        {"type": "caption", "text": "wo ist das Menü", "who": "you", "seconds": 4.0},
        {"type": "speech", "envelope": [0.1, 0.9], "rate": 20.0},
        {"type": "state", "state": "idle"},
    ]


async def test_a_question_goes_to_the_card_and_its_answer_comes_back(
    started: Kernel, child: Child
) -> None:
    seen = answers(started)
    ask = {
        "id": "a1",
        "question": "Klicken?",
        "choices": [{"id": "yes", "label": "Ja"}, {"id": "no", "label": "Nein"}],
        "timeout": 20,
    }
    await started.bus.publish("overlay.ask", ask, source="voice")
    await until(lambda: seen == [{"id": "a1", "choice": "yes"}])
    assert child.received()[-1]["type"] == "ask"
    assert instance(started, "overlay")._open_asks == set()
    await started.bus.publish("overlay.cancel_ask", {"id": "a1"}, source="voice")
    await until(lambda: child.types()[-1] == "cancel_ask")


async def test_mic_levels_are_cut_to_fifteen_a_second(started: Kernel, child: Child) -> None:
    for i in range(40):
        await started.bus.publish("voice.level", {"level": i / 40}, source="voice")
    await asyncio.sleep(0.3)
    assert child.types().count("level") == 15
    await asyncio.sleep(1.0)  # a new second: allowed again
    await started.bus.publish("voice.level", {"level": 0.9}, source="voice")
    await until(lambda: child.types().count("level") == 16)


def test_the_language_comes_from_the_profile() -> None:
    found, _ = discover(REPO / "modules")
    module = import_module_class(*found["overlay"]).__module__
    overlay = sys.modules[module]
    assert overlay.language_of({}) == "de"
    assert overlay.language_of({"language": "German"}) == "de"
    assert overlay.language_of({"language": "English"}) == "en"
    assert overlay.language_of({"language": "en"}) == "en"
