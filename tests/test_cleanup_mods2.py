"""Regression tests from the cleanup of calc, calendar, certs, clipboard, code, commands
and dev: a calendar that cannot be read is never overwritten, relative times make timed
events, imported events keep distinct ids, a bad reply from the code model costs one
attempt, a program that outlives its time limit is stopped with everything it started, and
the calculator, units and dates say "no" instead of giving a wrong or cryptic answer."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths, ToolError
from starlight.core.model import ModelResponse, ToolCall
from starlight.core.schedule import has_clock_time, names_time_of_day
from starlight.core.vault import MemoryBackend
from tests.conftest import ScriptedProvider


class Yes:
    async def confirm(self, question: str) -> bool:
        return True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("calendar", "todo", "commands", "dev", "certs"):
        use_module(name)
    return paths


async def call(k: Kernel, tool: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(tool, args)
    assert result.ok, result.error
    return result.output


async def fails(k: Kernel, tool: str, /, **args: Any) -> str:
    result = await k.dispatcher.call(tool, args)
    assert not result.ok, result.output
    return str(result.error)


def instance(k: Kernel, name: str) -> Any:
    record = k.modules.records[name]
    assert record.instance is not None, record.error
    return record.instance


def calendar_kernel(project: Paths) -> Kernel:
    return Kernel(project, ui=Yes(), vault_backend=MemoryBackend())


NOW = datetime(2026, 10, 7, 8, 0)  # a Wednesday morning

# -- calendar ----------------------------------------------------------------------------


async def test_an_unreadable_calendar_is_never_overwritten(
    project: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with calendar_kernel(project) as k:
        cal = instance(k, "calendar")
        cal._now = lambda: NOW
        for title in ("One", "Two", "Three"):
            await call(k, "calendar.add_event", title=title, start="today 10:00")
        before = cal.file.read_bytes()
        assert before.count(b"BEGIN:VEVENT") == 3

        original = Path.read_text

        def locked(self: Path, *args: Any, **kwargs: Any) -> str:
            if self.name == "calendar.ics":
                raise PermissionError("held by the virus scanner")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", locked)
        assert "could not read the calendar" in await fails(
            k, "calendar.add_event", title="Four", start="today 11:00"
        )
        assert "could not read the calendar" in await fails(k, "calendar.remove", id="one")
        assert "could not read the calendar" in await fails(k, "calendar.agenda")
        assert await cal.check_alerts(NOW) == []  # the alert job just waits for the next round
        monkeypatch.undo()
        assert cal.file.read_bytes() == before  # nothing was written over it

        await call(k, "calendar.add_event", title="Four", start="today 11:00")
        assert cal.file.read_bytes().count(b"BEGIN:VEVENT") == 4


async def test_a_calendar_with_bad_bytes_is_refused_not_replaced(project: Paths) -> None:
    async with calendar_kernel(project) as k:
        cal = instance(k, "calendar")
        cal._now = lambda: NOW
        cal.file.parent.mkdir(parents=True, exist_ok=True)
        cal.file.write_bytes(b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nSUMMARY:Gr\xfc\xdfe\r\n")
        assert "could not read the calendar" in await fails(
            k, "calendar.add_event", title="New", start="today 10:00"
        )
        assert cal.file.read_bytes().endswith(b"Gr\xfc\xdfe\r\n")


async def test_a_missing_calendar_is_just_empty(project: Paths) -> None:
    async with calendar_kernel(project) as k:
        cal = instance(k, "calendar")
        cal._now = lambda: NOW
        assert not cal.file.exists()
        assert (await call(k, "calendar.agenda"))["count"] == 0
        await call(k, "calendar.add_event", title="First", start="today 10:00")
        assert cal.file.exists()


def test_relative_times_name_a_moment_and_days_do_not() -> None:
    for text in ("in 2 hours", "in 30 minutes", "in 2h", "in an hour", "In 90 min", "18:30", "6pm"):
        assert names_time_of_day(text), text
    for text in ("in 3 days", "friday", "tomorrow", "24.12.2026", "in 2 weeks"):
        assert not names_time_of_day(text), text
    assert not has_clock_time("in 2 hours")  # has_clock_time itself is unchanged


async def test_relative_times_make_timed_events_in_calendar_and_todo(project: Paths) -> None:
    async with calendar_kernel(project) as k:
        cal = instance(k, "calendar")
        cal._now = lambda: NOW
        two = await call(k, "calendar.add_event", title="Parcel", start="in 2 hours")
        assert two["start"] == "2026-10-07 10:00" and "all_day" not in two
        half = await call(k, "calendar.add_event", title="Tea", start="in 30 minutes")
        assert half["start"] == "2026-10-07 08:30"
        days = await call(k, "calendar.add_event", title="Trip", start="in 3 days")
        assert days["all_day"] is True and days["date"] == "2026-10-10"
        todo = instance(k, "todo")
        todo._now = lambda: NOW
        assert todo._due("in 2 hours") == "2026-10-07T10:00"
        assert todo._due("in 3 days") == "2026-10-10"


async def test_a_long_reminder_fires_when_it_is_due(project: Paths) -> None:
    async with calendar_kernel(project) as k:
        cal = instance(k, "calendar")
        cal._now = lambda: NOW
        await call(
            k,
            "calendar.add_event",
            title="Flight",
            start="2026-10-08 08:00",
            remind_minutes=1440,
        )
        assert await cal.check_alerts(datetime(2026, 10, 7, 7, 59)) == []  # a day and a minute
        assert await cal.check_alerts(datetime(2026, 10, 7, 8, 1)) == ["Flight"]
        assert await cal.check_alerts(datetime(2026, 10, 7, 9, 0)) == []  # only once


async def test_imported_events_with_a_common_uid_prefix_have_their_own_ids(
    project: Paths,
) -> None:
    prefix = "040000008200E00074C5B7101A82E008"  # every Outlook UID begins like this
    text = "BEGIN:VCALENDAR\r\n" + "".join(
        f"BEGIN:VEVENT\r\nUID:{prefix}00000000{tail}\r\nDTSTART:20261007T10{minute}00\r\n"
        f"DTEND:20261007T11{minute}00\r\nSUMMARY:{title}\r\nEND:VEVENT\r\n"
        for tail, minute, title in (("AAAA", "00", "Standup"), ("BBBB", "30", "Budget review"))
    )
    async with calendar_kernel(project) as k:
        cal = instance(k, "calendar")
        cal._now = lambda: NOW
        cal.file.parent.mkdir(parents=True, exist_ok=True)
        cal.file.write_text(text + "END:VCALENDAR\r\n", encoding="utf-8")
        native = await call(k, "calendar.add_event", title="Gym", start="today 18:00")
        events = (await call(k, "calendar.agenda"))["events"]
        ids = {e["title"]: e["id"] for e in events}
        assert len(set(ids.values())) == 3 and len(native["id"]) == 8
        removed = await call(k, "calendar.remove", id=ids["Budget review"])
        assert removed["title"] == "Budget review"
        removed = await call(k, "calendar.remove", id=native["id"])
        assert removed["title"] == "Gym"
        left = [e["title"] for e in (await call(k, "calendar.agenda"))["events"]]
        assert left == ["Standup"]


async def test_two_uids_with_one_short_id_are_never_guessed(project: Paths) -> None:
    async with calendar_kernel(project) as k:
        cal = instance(k, "calendar")
        cal._now = lambda: NOW
        await call(k, "calendar.add_event", title="Gym", start="today 18:00")
        await call(k, "calendar.add_event", title="Yoga", start="today 19:00")
        events = cal._local()
        for event in events:  # force the clash by hand: both uuids start the same
            event.uid = "abcdef12" + event.uid[8:]
        cal._save(events)
        assert "matches 2 events" in await fails(k, "calendar.remove", id="abcdef12")
        assert (await call(k, "calendar.remove", id="yoga"))["title"] == "Yoga"


# -- code --------------------------------------------------------------------------------

GOOD = json.dumps(
    {
        "files": {
            "main.py": "def two():\n    return 2\n",
            "test_main.py": "from main import two\nassert two() == 2\nprint('ALL TESTS PASSED')\n",
        },
        "test_command": "python test_main.py",
    }
)


@pytest.mark.parametrize(
    ("files", "command"),
    [
        ({"../x.py": "print(1)"}, "python ../x.py"),  # invalid file name
        ({"main.py": "print(1)"}, "node main.js"),  # the process sandbox only runs Python
        ({"main.py": "print(1)"}, "python 'main.py"),  # unbalanced quote
    ],
)
async def test_a_bad_reply_from_the_code_model_costs_one_attempt(
    paths: Paths, use_module: Callable[[str], Path], files: dict[str, str], command: str
) -> None:
    use_module("code")
    (paths.module_configs / "code.yaml").write_text("sandbox: process\ntimeout: 20\n")
    paths.config_file.write_text(paths.config_file.read_text() + "memory:\n  auto_extract: false\n")
    bad = json.dumps({"files": files, "test_command": command})
    task = {"task": "a function that returns two"}
    provider = ScriptedProvider(
        ModelResponse(tool_calls=[ToolCall("code.write_code", task)]), bad, GOOD, "Done."
    )
    async with Kernel(paths, provider=provider, ui=Yes()) as k:
        answer = await k.ask("Write me a function that returns two")
        await k.drain()
    output = answer.tool_results[0].output
    assert output["success"] is True, output
    assert output["attempts"] == 2
    # the model was told what was wrong, then answered again
    assert "The tests failed" in provider.calls[2][-1].content  # type: ignore[attr-defined]


# -- commands ----------------------------------------------------------------------------


async def test_a_bad_command_is_explained_and_a_value_cannot_end_in_a_line_break(
    project: Paths,
) -> None:
    from modules.commands.module import SAFE_VALUE

    assert SAFE_VALUE.match("abc") and not SAFE_VALUE.match("abc\n")
    assert not SAFE_VALUE.match("a\nb") and not SAFE_VALUE.match("-x")
    async with Kernel(project, ui=Yes()) as k:
        message = await fails(k, "commands.add", name="x", run=["{prog}", "a"])
        assert "program itself can't be a placeholder" in message
        assert "errors.pydantic.dev" not in message
        assert "run" in await fails(k, "commands.add", name="x", run=[])
        script = "import sys; print(sys.argv[1])"
        await call(k, "commands.add", name="say", run=[sys.executable, "-c", script, "{w}"])
        assert "not allowed" in await fails(k, "commands.run", name="say", values={"w": "hi\n"})
        assert (await call(k, "commands.run", name="say", values={"w": "hi"}))["ok"]


# -- clipboard ---------------------------------------------------------------------------


def test_clipboard_talks_utf8_and_reports_a_program_that_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modules.clipboard import backends

    seen: list[dict[str, Any]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="﻿Größe", stderr="")

    monkeypatch.setattr(backends.subprocess, "run", fake_run)
    backend = backends.Backend("fake", ["get"], ["set"])
    assert backend.get() == "Größe"  # a byte order mark from PowerShell is dropped
    backend.set("Größe")
    assert all(s["encoding"] == "utf-8" and s["errors"] == "replace" for s in seen)
    assert seen[1]["input"] == "Größe"

    def timeout(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 10)

    def vanished(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(command[0])

    for failing in (timeout, vanished):
        monkeypatch.setattr(backends.subprocess, "run", failing)
        with pytest.raises(backends.ClipboardUnavailable):
            backend.get()
        with pytest.raises(backends.ClipboardUnavailable):
            backend.set("x")


def test_clipboard_powershell_is_switched_to_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    from modules.clipboard import backends

    monkeypatch.setattr(backends.sys, "platform", "win32")
    backend = backends.detect()
    assert backend is not None
    for command in (backend.get_cmd, backend.set_cmd):
        assert "[Console]::InputEncoding" in command[-1] and "UTF8" in command[-1]
    assert backend.get_cmd[-1].endswith("Get-Clipboard -Raw")
    assert backend.set_cmd[-1].endswith("$input | Set-Clipboard")


# -- calc --------------------------------------------------------------------------------


def test_the_calculator_refuses_what_is_not_a_real_number() -> None:
    from modules.calc.module import evaluate

    for formula in ("(-8)^0.5", "round(1e308*10)", "1e308*10", "round((-8)^0.5)"):
        with pytest.raises(ToolError):
            evaluate(formula)
    with pytest.raises(ToolError, match="not a real number"):
        evaluate("(-8)^0.5")
    assert evaluate("round(10**400, -399)") == 10**400  # a huge integer still rounds
    assert evaluate("8^(1/3)") == 2


def test_small_unit_results_keep_their_digits() -> None:
    from modules.calc.module import convert

    assert convert(1, "b", "tb") == 1e-12
    assert convert(1, "kb", "pb") == pytest.approx(1e-12)
    assert convert(1, "s", "yr") == pytest.approx(3.1688087814e-08)
    assert convert(1, "GiB", "MB") == pytest.approx(1073.741824)
    assert convert(72, "F", "C") == pytest.approx(22.2222, abs=1e-3)


def test_the_next_29_february_is_the_next_leap_year() -> None:
    from modules.calc.module import parse_date

    assert parse_date("29.02.", date(2024, 3, 1), future=True) == date(2028, 2, 29)
    assert parse_date("29.02.", date(2024, 2, 1), future=True) == date(2024, 2, 29)
    assert parse_date("1.3.", date(2026, 10, 1), future=True) == date(2027, 3, 1)


# -- certs -------------------------------------------------------------------------------


async def test_a_broken_servers_file_gives_the_clear_configuration_error(project: Paths) -> None:
    (project.config / "servers.yaml").write_text("servers: [unclosed\n")
    async with Kernel(project) as k:
        message = await fails(k, "certs.check_all")
        assert "servers.yaml" in message and "no servers are configured" not in message
        assert "servers.yaml" in await fails(k, "certs.expiring")
    (project.config / "servers.yaml").write_text("servers: just-a-word\n")
    async with Kernel(project) as k:
        assert "no servers are configured" in await fails(k, "certs.check_all")


# -- dev ---------------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX shell script")
async def test_a_git_call_that_outlives_its_limit_is_stopped_with_its_children(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    folder = tmp_path_factory.mktemp("slowgit")
    slow = folder / "slowgit"
    slow.write_text("#!/bin/sh\nsleep 8 &\nsleep 8\n")  # a child that keeps the output open
    slow.chmod(0o755)
    (project.module_configs / "dev.yaml").write_text(
        f"git: '{slow}'\ntimeout: 1\nrepos: {{mine: '{folder.as_posix()}'}}\n"
    )
    async with Kernel(project, ui=Yes()) as k:
        started = time.monotonic()
        message = await fails(k, "dev.git_branches", repo="mine")
        assert "took longer than 1 s" in message
        assert time.monotonic() - started < 5


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_repo_names_ignore_case_and_non_ascii_file_names_are_not_escaped(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    repo = tmp_path_factory.mktemp("repo")
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    (repo / "Größe.txt").write_text("x\n", encoding="utf-8")
    (project.module_configs / "dev.yaml").write_text(f"repos: {{Mine: '{repo.as_posix()}'}}\n")
    async with Kernel(project, ui=Yes()) as k:
        assert (await call(k, "dev.repos")) == {"Mine": repo.as_posix()}
        for name in ("Mine", "mine", "MINE"):
            status = await call(k, "dev.git_status", repo=name)
            assert status["changes"] == [{"state": "??", "file": "Größe.txt"}]
