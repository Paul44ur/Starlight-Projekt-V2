"""Regression tests for the cleanup of backup, automations, apps, breach and briefing:
a damaged backup is reported (not a crash), folders that can't be read are not silently left
out, the small state files survive a BOM or garbage, and a few wording and rating fixes."""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from tests.conftest import REPO


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(
    paths: Paths, use_module: Callable[[str], Path], write_module: Callable[..., Path]
) -> Paths:
    for name in ("backup", "automations", "apps", "breach", "briefing"):
        use_module(name)
    write_module(
        "certs",
        """
        from starlight.core import Module, tool

        class Mod(Module):
            @tool
            def expiring(self, days: int = 0) -> dict:
                certs = [("old.example", -3), ("now.example", 0), ("soon.example", 1),
                         ("later.example", 12)]
                return {
                    "within_days": 30,
                    "expiring": [{"host": h, "days_left": d} for h, d in certs],
                    "unreachable": [],
                }
        """,
    )
    return paths


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(name, args)
    assert result.ok, result.error
    return result.output


async def fails(k: Kernel, name: str, /, **args: Any) -> str:
    result = await k.dispatcher.call(name, args)
    assert not result.ok, result.output
    return str(result.error)


def instance(k: Kernel, name: str) -> Any:
    record = k.modules.records[name]
    assert record.instance is not None, record.error
    return record.instance


# -- backup ------------------------------------------------------------------------------------


def flip_a_stored_byte(archive: Path, member: str) -> None:
    """Bit rot: change one byte of a file's compressed data inside the zip."""
    with zipfile.ZipFile(archive) as zipped:
        offset = zipped.getinfo(member).header_offset
    raw = bytearray(archive.read_bytes())
    name_length, extra_length = struct.unpack("<HH", raw[offset + 26 : offset + 30])
    raw[offset + 30 + name_length + extra_length + 2] ^= 0xFF
    archive.write_bytes(raw)


async def test_verify_reports_real_damage_instead_of_crashing(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    source, drive = tmp_path_factory.mktemp("docs"), tmp_path_factory.mktemp("drive")
    (source / "a.txt").write_text("hello world " * 400)
    (source / "b.txt").write_text("second file " * 400)
    async with Kernel(project, ui=User(True)) as k:
        await call(k, "backup.add", name="docs", source=str(source), target=str(drive))
        made = await call(k, "backup.run", name="docs")
        assert (await call(k, "backup.verify", name="docs")) == {
            "backup": Path(made["backup"]).name, "ok": True, "damaged": [], "files": 2,
        }  # fmt: skip
        archive = Path(made["backup"])
        flip_a_stored_byte(archive, "a.txt")
        rotten = await call(k, "backup.verify", name="docs")
        assert rotten["ok"] is False and rotten["damaged"] == ["a.txt"]

        archive.write_bytes(archive.read_bytes()[:-30])  # a copy that was cut off
        cut = await call(k, "backup.verify", name="docs")
        assert cut["ok"] is False and "can't be opened" in cut["error"]

        archive.write_bytes(b"this is not a zip file")
        junk = await call(k, "backup.verify", name="docs")
        assert junk["ok"] is False and "can't be opened" in junk["error"]


async def test_verify_notices_a_missing_manifest_or_file(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    source, drive = tmp_path_factory.mktemp("docs"), tmp_path_factory.mktemp("drive")
    (source / "a.txt").write_text("alpha")
    (source / "b.txt").write_text("beta")
    async with Kernel(project, ui=User(True)) as k:
        await call(k, "backup.add", name="docs", source=str(source), target=str(drive))
        archive = Path((await call(k, "backup.run", name="docs"))["backup"])
        with zipfile.ZipFile(archive) as zipped:
            entries = {n: zipped.read(n) for n in zipped.namelist()}
        with zipfile.ZipFile(archive, "w") as zipped:  # b.txt lost from the zip
            for name, data in entries.items():
                if name != "b.txt":
                    zipped.writestr(name, data)
        lost = await call(k, "backup.verify", name="docs")
        assert lost["ok"] is False and lost["damaged"] == ["b.txt"]
        with zipfile.ZipFile(archive, "w") as zipped:  # no manifest at all
            zipped.writestr("a.txt", "alpha")
        no_list = await call(k, "backup.verify", name="docs")
        assert no_list["ok"] is False and "list of files" in no_list["error"]


async def test_a_folder_that_cannot_be_read_is_reported_not_left_out(
    project: Paths, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, drive = tmp_path_factory.mktemp("photos"), tmp_path_factory.mktemp("drive")
    (source / "ok").mkdir()
    (source / "ok" / "a.txt").write_text("fine")
    (source / "locked").mkdir()
    (source / "locked" / "b.txt").write_text("hidden")
    blocked: list[str] = [str(source / "locked")]
    real_scandir = os.scandir

    def scandir(path: Any = None) -> Any:
        if str(path) in blocked:
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    async with Kernel(project, ui=User(True)) as k:
        await call(k, "backup.add", name="photos", source=str(source), target=str(drive))
        result = await call(k, "backup.run", name="photos")
        assert result["files"] == 1 and result["skipped"] == ["locked"]
        with zipfile.ZipFile(result["backup"]) as zipped:
            manifest = json.loads(zipped.read("starlight-backup.json"))
        assert [f["path"] for f in manifest["files"] if "error" in f] == ["locked"]

        blocked.append(str(source))  # the folder itself can't be listed: no empty "backup"
        assert "can't read" in await fails(k, "backup.run", name="photos")
    assert len(list((drive / "photos").glob("*.zip"))) == 1
    assert not list((drive / "photos").glob("*.partial"))


async def test_adding_a_backup_again_says_it_replaces_the_old_one(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    source, drive = tmp_path_factory.mktemp("docs"), tmp_path_factory.mktemp("drive")
    user = User(True, True, False)
    async with Kernel(project, ui=user) as k:
        await call(k, "backup.add", name="docs", source=str(source), target=str(drive), keep=30)
        assert "already exists" not in user.questions[0]
        await call(k, "backup.add", name="docs", source=str(source), target=str(drive), keep=3)
        assert "already exists" in user.questions[1] and "keeping 30" in user.questions[1]
        assert "not confirmed" in await fails(
            k, "backup.add", name="docs", source=str(source), target=str(drive), keep=9
        )
        assert instance(k, "backup").config.sets["docs"].keep == 3  # no answer, no change
        # a keep that makes no sense is a clear message, not a raw validation error
        message = await fails(
            k, "backup.add", name="more", source=str(source), target=str(drive), keep=0
        )
        assert "not valid" in message and "keep" in message
        assert "ValidationError" not in message


# -- automations -------------------------------------------------------------------------------


def automations(k: Kernel) -> Any:
    return instance(k, "automations")


async def test_automations_start_with_a_bom_or_a_damaged_state_file(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        await call(k, "automations.add", name="daily", when="every day at 18:00", say="hello there")
    state = project.module_data("automations") / "state.json"

    later = (datetime.now() + timedelta(days=3)).replace(microsecond=0)
    state.write_text("﻿" + json.dumps({"daily": later.isoformat()}), encoding="utf-8")
    async with Kernel(project, ui=User()) as k:  # Notepad's "UTF-8 with BOM" is fine
        assert automations(k)._next["daily"] == later

    for text in ('{"daily": "next tuesday"}', '{"daily": "2026-10-07T18:00:00+02:00"}'):
        state.write_text(text, encoding="utf-8")
        async with Kernel(project, ui=User()) as k:  # a bad time is replaced, not fatal
            assert automations(k)._next["daily"] > datetime.now()

    state.write_text('["not", "a", "dict"]', encoding="utf-8")
    async with Kernel(project, ui=User()) as k:
        assert "daily" in automations(k)._next
    assert list(state.parent.glob("state.unreadable-*.json"))  # kept, not overwritten


async def test_a_due_rule_still_runs_when_its_schedule_cannot_be_saved(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        told: list[str] = []
        k.bus.subscribe("deliver", lambda e: told.append(e.data["text"]))
        await call(k, "automations.add", name="ping", when="every 2 hours", say="ping!")
        auto = automations(k)
        folder = project.root / "not-a-file"
        folder.mkdir()
        auto.state_file = folder  # replacing a folder with a file fails
        due = auto._next["ping"]
        assert await auto.tick(due) == ["ping"]
        assert told == ["Automation: ping!"]
        assert auto._next["ping"] > due  # and it is not due again right away


async def test_the_history_is_trimmed_in_one_piece(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        await call(k, "automations.add", name="ping", when="every 2 hours", say="ping!")
        auto = automations(k)
        auto.history_file.write_text("".join(f'{{"n": {n}}}\n' for n in range(1005)))
        auto._record({"n": 1005})
        lines = auto.history_file.read_text("utf-8").splitlines()
        assert len(lines) == 500 and json.loads(lines[-1]) == {"n": 1005}
        assert not list(auto.history_file.parent.glob(".*.tmp"))


async def test_a_rule_name_cannot_be_added_twice(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        await call(k, "automations.add", name="ping", when="every 2 hours", say="ping!")
        assert "pick another name" in await fails(
            k, "automations.add", name="ping", when="every 3 hours", say="again"
        )


# -- apps --------------------------------------------------------------------------------------


class FakeDesktop:
    def __init__(self) -> None:
        self.started: list[str] = []

    def programs(self, extra: list[Path]) -> list[Any]:
        from starlight_modules.apps.backends import Program  # type: ignore[import-not-found]

        return [Program(n, f"/menu/{n}.lnk") for n in ("Spotify", "Steam")]

    def start(self, program: Any) -> None:
        self.started.append(program.name)


async def test_the_apps_yes_list_reads_a_bom_file_and_survives_garbage(project: Paths) -> None:
    user = User(True)
    data = project.module_data("apps")
    data.mkdir(parents=True, exist_ok=True)
    yes_list = data / "allowed.json"
    yes_list.write_text('﻿["Spotify"]', encoding="utf-8")  # saved by Notepad
    async with Kernel(project, ui=user) as k:
        desktop = FakeDesktop()
        instance(k, "apps")._backend = desktop
        await call(k, "apps.open", name="spotify")
        assert user.questions == [] and desktop.started == ["Spotify"]  # it was on the list

        yes_list.write_text("{not json", encoding="utf-8")
        await call(k, "apps.open", name="steam")  # damaged: set aside, so it asks again
        assert len(user.questions) == 1
    assert list(data.glob("allowed.unreadable-*.json"))
    assert json.loads(yes_list.read_text("utf-8")) == ["Steam"]


def linux_backend(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> Any:
    """The Linux window backend with wmctrl/xdotool faked: `outcome` is the CompletedProcess
    they return, or the exception they raise."""
    sys.path.insert(0, str(REPO / "modules"))
    try:
        from apps import backends  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)

    def run(command: list[str], **kwargs: Any) -> Any:
        if isinstance(outcome, Exception):
            raise outcome
        return subprocess.CompletedProcess(command, *outcome)

    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(subprocess, "run", run)
    return backends


def test_a_failing_wmctrl_is_an_error_not_a_silent_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backends = linux_backend(monkeypatch, (1, "", "Cannot open display.\n"))
    window = backends.Window(1, "Inbox", "")
    for action in (
        lambda b: b.focus(window),
        lambda b: b.close(window),
        lambda b: b.show(window, "maximize"),
        lambda b: b.show(window, "minimize"),
        lambda b: b.windows(),
    ):
        with pytest.raises(backends.AppsUnavailable, match="failed: Cannot open display"):
            action(backends.LinuxBackend())


def test_a_hanging_wmctrl_is_an_error_too(monkeypatch: pytest.MonkeyPatch) -> None:
    backends = linux_backend(monkeypatch, subprocess.TimeoutExpired("wmctrl", 5))
    with pytest.raises(backends.AppsUnavailable, match="wmctrl did not answer"):
        backends.LinuxBackend().windows()


def test_a_working_wmctrl_still_lists_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    table = "0x04200003  0 host Inbox - Mail\n0x04200007  1 host Terminal\n"
    backends = linux_backend(monkeypatch, (0, table, ""))
    found = backends.LinuxBackend().windows()
    assert [(w.id, w.title) for w in found] == [
        (0x4200003, "Inbox - Mail"),
        (0x4200007, "Terminal"),
    ]


# -- breach and briefing -----------------------------------------------------------------------


async def test_repeated_characters_lower_the_strength_rating(project: Paths) -> None:
    async with Kernel(project) as k:
        varied = await call(k, "breach.how_safe", password="Tr0ub4dor&3-xkcd-horse")
        repeats = await call(k, "breach.how_safe", password="Tr0ub4dor&3-xkcd-hoooorse")
        only_a = await call(k, "breach.how_safe", password="a" * 29)
    assert varied["rating"] == "strong" and varied["weaknesses"] == []
    assert repeats["rating"] == "ok" and repeats["score"] == varied["score"] - 8
    assert "a character repeats three or more times" in repeats["weaknesses"]
    assert only_a["rating"] == "weak" and only_a["out_of"] == 40


async def test_the_briefing_says_when_a_certificate_has_already_expired(project: Paths) -> None:
    async with Kernel(project) as k:
        out = await call(k, "briefing.briefing", sections="certs")
    lines = out["text"].splitlines()
    assert "⚠ Certificate for old.example expired 3 days ago" in lines
    assert "⚠ Certificate for now.example expires today" in lines
    assert "⚠ Certificate for soon.example expires in 1 day" in lines
    assert "⚠ Certificate for later.example expires in 12 days" in lines
