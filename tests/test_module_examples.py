"""The three worked examples in docs/module-examples.md: their files are taken from the page
as they are written there, loaded as modules and used - so the page stays correct. Also
the per-model comparison in the statistics viewer, the nightly check, the crash detector
and the release notes after an update."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from tests.conftest import REPO, ScriptedProvider

PAGE = REPO / "docs" / "module-examples.md"
FILE = re.compile(r"<!-- file: (\S+) -->\n```\w+\n(.*?)```", re.DOTALL)


def example_files() -> dict[str, str]:
    return dict(FILE.findall(PAGE.read_text(encoding="utf-8")))


def install(paths: Paths, *names: str) -> None:
    for path, text in example_files().items():
        name = path.split("/")[1]
        if name in names:
            target = paths.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            (target.parent / "__init__.py").touch()


def instance(k: Kernel, name: str) -> Any:
    record = k.modules.records[name]
    assert record.instance is not None, record.error
    return record.instance


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    return await k.dispatcher.call(name, args)


def test_the_page_has_all_six_files() -> None:
    assert sorted(example_files()) == [
        "modules/downloads/module.py", "modules/downloads/module.yaml",
        "modules/plants/module.py", "modules/plants/module.yaml",
        "modules/quotes/module.py", "modules/quotes/module.yaml",
    ]  # fmt: skip


async def test_example_1_quotes(paths: Paths) -> None:
    install(paths, "quotes")
    async with Kernel(paths) as k:
        found = await call(k, "quotes.quote", topic="courage")
        assert found.ok and "Hemingway" in found.output["quote"]
        kept = await call(k, "quotes.remember", text="Less is more", author="Mies")
        assert kept.ok and kept.output["you_have"] == 1
        assert (await call(k, "quotes.quote", topic="less")).output[
            "quote"
        ] == "Less is more - Mies"
        missing = await call(k, "quotes.quote", topic="cheese")
        assert not missing.ok and "quotes.remember" in str(missing.error)


async def test_example_2_plants(paths: Paths) -> None:
    install(paths, "plants")
    (paths.module_configs / "plants.yaml").write_text("plants: {basil: 3, cactus: 14}\n")
    async with Kernel(paths) as k:
        plants = instance(k, "plants")
        told: list[str] = []
        k.bus.subscribe("deliver", lambda e: told.append(e.data["text"]))
        thirsty: list[Any] = []
        k.bus.subscribe("plants.thirsty", lambda e: thirsty.append(e.data["plants"]))
        assert await plants.check() == ["basil", "cactus"]  # never watered yet
        await call(k, "plants.watered", plant="basil")
        await call(k, "plants.watered", plant="cactus")
        assert await plants.check() == []
        plants._today = lambda: dt.date.today() + dt.timedelta(days=4)
        assert await plants.check() == ["basil"]
        assert told[-1] == "Plants: Water: basil" and thirsty[-1] == ["basil"]
        wrong = await call(k, "plants.watered", plant="fern")
        assert not wrong.ok and "basil, cactus" in str(wrong.error)


async def test_example_3_old_downloads(paths: Paths, tmp_path: Path) -> None:
    install(paths, "downloads")
    folder = tmp_path / "Downloads"
    folder.mkdir()
    old, new = folder / "old.zip", folder / "new.pdf"
    old.write_bytes(b"x" * 100)
    new.write_bytes(b"y")
    long_ago = time.time() - 90 * 86400
    os.utime(old, (long_ago, long_ago))

    class Says:
        def __init__(self, yes: bool) -> None:
            self.yes, self.asked = yes, 0

        async def confirm(self, question: str) -> bool:
            self.asked += 1
            return self.yes

        async def ask(self, question: str, secret: bool = False) -> str | None:
            return None

        async def notify(self, text: str) -> None:
            return None

    no = Says(False)
    async with Kernel(paths, ui=no) as k:
        listed = await call(k, "downloads.old_downloads", folder=str(folder), days=30)
        assert listed.output == {"count": 1, "files": ["old.zip"]} and no.asked == 0
        refused = await call(k, "downloads.tidy", folder=str(folder), days=30)
        assert not refused.ok and no.asked == 1 and old.exists()
    yes = Says(True)
    async with Kernel(paths, ui=yes) as k:
        done = await call(k, "downloads.tidy", folder=str(folder), days=30)
        assert done.ok and done.output["moved"] == 1
    assert (folder / "Old downloads" / "old.zip").exists() and new.exists()


# -- models compared in the statistics viewer -------------------------------------------------


async def test_tool_calls_are_counted_for_the_model_that_asked(
    paths: Paths, use_module: Any
) -> None:
    from starlight.core.model import ModelResponse, ToolCall
    from starlight.viewer import StatsReader

    use_module("hello")
    usage = {"model": "qwen-test", "tokens_per_second": 42.0, "duration_s": 1.5}
    provider = ScriptedProvider(
        ModelResponse(content="", tool_calls=[ToolCall("hello.greet", {"name": "Ann"}, "c1")],
                      usage=usage),
        ModelResponse(content="Done.", usage=usage),
    )  # fmt: skip
    async with Kernel(paths, provider=provider) as k:
        await k.ask("greet Ann")
    models = StatsReader(paths.stats_db).summary(60)["models"]
    assert models == [{"model": "qwen-test", "answers": 2, "tokens_per_second": 42.0,
                       "seconds_per_answer": 1.5, "tool_calls": 1, "tool_failures_percent": 0.0,
                       "exam": None}]  # fmt: skip


# -- the nightly check ----------------------------------------------------------------------------


def test_the_nightly_comparison() -> None:
    from starlight.models.nightly import compare, cron_line, schedule_command

    before = {"exam": {"score": 8, "of": 8, "passed": ["a", "b"]}, "tokens_per_second": 40.0}
    same = {"exam": {"score": 8, "of": 8, "passed": ["a", "b"]}, "tokens_per_second": 38.0}
    worse = {"exam": {"score": 7, "of": 8, "passed": ["a"]}, "tokens_per_second": 20.0}
    assert compare(None, same) == [] and compare(before, same) == []
    assert compare(before, worse) == [
        "tool-use exam 7/8, was 8/8 (now failing: b)", "speed 20.0 tokens/s, was 40.0",
    ]  # fmt: skip
    command = schedule_command(Path("C:/Starlight"))
    assert command[:4] == ["schtasks", "/Create", "/SC", "DAILY"] and "model nightly" in command[-2]
    assert schedule_command(Path("C:/Starlight"), remove=True)[1] == "/Delete"
    assert cron_line(Path("/opt/starlight")).startswith("15 3 * * * cd /opt/starlight")


async def test_a_nightly_run_is_saved_and_compared(tmp_path: Path) -> None:
    from starlight.models.nightly import nightly

    logs = tmp_path / "logs"
    first = await nightly(ScriptedProvider(), logs)  # answers "done" to everything
    assert first["worse"] == [] and first["exam"]["of"] >= 6
    assert (logs / "nightly.log").read_text().count("exam ") == 1
    saved = json.loads(next((logs / "nightly").glob("*.json")).read_text())
    assert saved["exam"]["score"] == first["exam"]["score"]


# -- the crash detector ---------------------------------------------------------------------------


def test_a_crash_is_written_down_and_told_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlight.core import crashes

    logs = tmp_path / "logs"
    monkeypatch.setattr(sys, "excepthook", lambda *a: None)
    crashes.watch(logs)
    try:
        raise KeyError("the_missing_key")
    except KeyError:
        sys.excepthook(*sys.exc_info())  # what Python does with an uncaught error
    assert "the_missing_key" in (logs / "crash.log").read_text()
    what = crashes.unseen(logs)
    assert what is not None and what.startswith("KeyError: 'the_missing_key' (")
    crashes.mark_seen(logs)
    assert crashes.unseen(logs) is None
    sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)  # being stopped: no crash
    assert crashes.unseen(logs) is None
    assert "support bundle" in crashes.message(what)


async def test_the_next_start_offers_a_support_bundle(paths: Paths) -> None:
    from starlight.core import crashes

    crashes.record(paths.logs, "Traceback (most recent call last):\nValueError: boom")
    with paths.config_file.open("a") as config:
        config.write("diagnostics:\n  crash_reports: true\n")
    Kernel.CRASH_NOTE_AFTER = 0.01
    try:
        async with Kernel(paths) as k:
            told: list[str] = []
            k.bus.subscribe("deliver", lambda e: told.append(e.data["text"]))
            for _ in range(100):
                if told:
                    break
                await asyncio.sleep(0.02)
        assert told and "ValueError: boom" in told[0] and "support-bundle" in told[0]
        async with Kernel(paths) as k:  # told once
            assert crashes.unseen(paths.logs) is None
    finally:
        Kernel.CRASH_NOTE_AFTER = 20.0


async def test_crashes_are_not_told_unless_asked(paths: Paths) -> None:
    from starlight.core import crashes

    crashes.record(paths.logs, "ValueError: boom")
    async with Kernel(paths):
        pass
    assert crashes.unseen(paths.logs) is not None  # still there for when it is switched on


# -- what's new after an update -------------------------------------------------------------------


def test_the_release_notes_come_once_after_an_update(tmp_path: Path) -> None:
    sys.path.insert(0, str(REPO / "launcher"))
    try:
        import starlight_launcher as launcher
    finally:
        sys.path.pop(0)
    docs = tmp_path / "docs"
    docs.mkdir()
    notes = docs / "release-notes.md"
    notes.write_text("## Starlight V2 1.00.00\n\nFirst.\n")
    assert launcher.whats_new(tmp_path) is None  # a first install: nothing is "new"
    notes.write_text("## Starlight V2 1.01.00\n\n### New\n- **Hey Starlight** ([more](x))\n")
    assert launcher.whats_new(tmp_path) == (
        "Starlight V2 1.01.00",
        "### New\n- **Hey Starlight** ([more](x))",
    )
    assert launcher.whats_new(tmp_path) is None  # once
    assert (
        launcher.short_notes("### New\n- **Hey Starlight** ([more](x))")
        == "New\n- Hey Starlight (more)"
    )

    class Ui:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def say(self, text: str = "") -> None:
            self.lines.append(text)

        def step(self, text: str) -> None:
            self.lines.append("== " + text)

    notes.write_text("## Starlight V2 1.02.00\n\nFaster.\n")
    ui = Ui()
    launcher.tell_whats_new(tmp_path, ui)  # type: ignore[arg-type]
    assert ui.lines == ["== What's new in Starlight V2 1.02.00", "   Faster."]
