"""Bugs found in a debug pass of the modules, memory and skills - each test is the failing
case as it was reported, now working."""

from __future__ import annotations

import asyncio
import email
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.jsonfile import load_json, save_json
from starlight.memory import Memory
from starlight.skills.manager import SkillManager


class User:
    def __init__(self) -> None:
        self.told: list[str] = []

    async def confirm(self, question: str) -> bool:
        return True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        self.told.append(text)


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(name, args)
    assert result.ok, result.error
    return result.output


def instance(k: Kernel, name: str) -> Any:
    record = k.modules.records[name]
    assert record.instance is not None, record.error
    return record.instance


# -- lists kept in JSON files ------------------------------------------------------------------


def test_a_hand_edited_json_file_is_read_or_kept_never_wiped(tmp_path: Path) -> None:
    file = tmp_path / "todo.json"
    file.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"text": "Steuer"}]).encode())  # Notepad
    assert load_json(file, []) == [{"text": "Steuer"}]
    file.write_text('[{"text": "Steuer"},]', encoding="utf-8")  # a stray comma
    assert load_json(file, []) == []
    kept = list(tmp_path.glob("todo.unreadable-*.json"))
    assert len(kept) == 1 and "Steuer" in kept[0].read_text("utf-8")
    save_json(file, [{"text": "Neu"}])
    assert load_json(file, []) == [{"text": "Neu"}] and kept[0].exists()
    assert load_json(tmp_path / "none.json", {}) == {}


async def test_todo_and_reminders_survive_a_byte_order_mark(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("todo")
    use_module("reminders")
    (paths.module_configs / "reminders.yaml").write_text("check_interval: 0\n")
    async with Kernel(paths, ui=User()) as k:
        for text in ("Steuer", "Milch"):
            await call(k, "todo.add", text=text)
            await call(k, "reminders.remind", when="in 2h", text=text)
        for name in ("todo", "reminders"):
            file = instance(k, name).file
            file.write_bytes(b"\xef\xbb\xbf" + file.read_bytes())
        await call(k, "todo.add", text="Neu")
        await call(k, "reminders.remind", when="in 3h", text="Neu")
        for name in ("todo", "reminders"):
            items = json.loads(instance(k, name).file.read_text("utf-8"))
            assert [i["text"] for i in items] == ["Steuer", "Milch", "Neu"]


async def test_a_german_time_in_a_task_keeps_its_time(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("todo")
    async with Kernel(paths, ui=User()) as k:
        added = await call(k, "todo.add", text="Arzt", due="tomorrow 18.30")
        assert added["due"].endswith("T18:30")


# -- memory and skills -------------------------------------------------------------------------


async def test_topics_with_the_same_file_name_stay_apart(paths: Paths) -> None:
    memory = Memory(paths)
    first, _ = await memory.remember("Paul kann C# seit 2015.", topic="C# Kenntnisse")
    second, merged = await memory.remember("Paul lernt gerade C++.", topic="C++ Kenntnisse")
    assert not merged and first.id != second.id
    contents = {e.content for e in memory.store.all()}
    assert {"Paul kann C# seit 2015.", "Paul lernt gerade C++."} <= contents


async def test_memory_files_with_a_bom_or_an_old_encoding_are_read(paths: Paths) -> None:
    memory = Memory(paths)
    memory.profile.set("name", "Paul")
    profile = paths.memory / "profile.md"
    profile.write_bytes(b"\xef\xbb\xbf" + profile.read_bytes())
    assert memory.profile.read() == {"name": "Paul"}
    memory.profile.set("language", "Deutsch")
    assert "---" not in memory.profile.read().get("preferences", "")
    (paths.memory / "notiz.md").write_bytes("Größe: 1,80 m".encode("cp1252"))
    await memory.remember("Paul fährt Rad.", topic="Sport")  # used to fail for every memory
    assert any("Größe" in e.content for e in memory.store.all())


def test_one_broken_skill_file_does_not_hide_the_others(paths: Paths) -> None:
    skills = SkillManager(paths)
    skills.draft("wetter-check", "Wetter", "1. Call weather.now")
    skills.draft("backup-check", "Backup", "1. Call backup.verify")
    (paths.skills / "wetter-check" / "SKILL.md").write_text(
        "---\nname: wetter-check\ndescription: Wetter: morgens\n---\n1. x\n", encoding="utf-8"
    )
    assert [s.name for s in skills.all()] == ["backup-check"]


# -- files ----------------------------------------------------------------------------------------


async def test_a_backup_with_a_very_old_file_still_runs(
    paths: Paths, use_module: Callable[[str], Path], tmp_path_factory: pytest.TempPathFactory
) -> None:
    use_module("backup")
    source, target = tmp_path_factory.mktemp("Fotos"), tmp_path_factory.mktemp("Ext")
    (source / "neu.txt").write_text("hallo")
    (source / "alt.txt").write_text("aus 1970")
    os.utime(source / "alt.txt", (0, 0))  # zip can't store dates before 1980
    async with Kernel(paths, ui=User()) as k:
        await call(k, "backup.add", name="fotos", source=str(source), target=str(target))
        ran = await call(k, "backup.run", name="fotos")
    assert ran.get("ok", True) and list((target / "fotos").glob("*.zip"))


async def test_file_search_finds_each_file_once_and_reads_windows_encodings(
    paths: Paths,
    use_module: Callable[[str], Path],
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path_factory.mktemp("Paul")
    for folder in ("Documents", "Desktop", "Downloads"):
        (home / folder).mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    docs = home / "Documents"
    (docs / "rechnung-2026.pdf").write_bytes(b"%PDF")
    (docs / "kunden.csv").write_bytes("Name;Ort\nMüller;Köln\n".encode("cp1252"))  # Excel
    (docs / "log.txt").write_bytes("Fehler beim Start\r\n".encode("utf-16"))  # PowerShell 5
    use_module("filesearch")
    async with Kernel(paths, ui=User()) as k:
        found = await call(k, "filesearch.find", name_pattern="rechnung")
        assert found["count"] == 1
        assert (await call(k, "filesearch.grep", text="Müller"))["files"] == 1
        assert (await call(k, "filesearch.grep", text="Fehler"))["files"] == 1


async def test_a_pdf_name_with_dots_keeps_them(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    pytest.importorskip("reportlab")
    use_module("pdf")
    async with Kernel(paths, ui=User()) as k:
        dated = await call(k, "pdf.to_pdf", content="Hallo", path="Rechnung 12.10.2026")
        typed = await call(k, "pdf.to_pdf", content="Hallo", path="notes.txt")
    assert str(dated["saved"]).endswith("Rechnung 12.10.2026.pdf")
    assert str(typed["saved"]).endswith("notes.pdf")


# -- news, mail, prices, calendar, calc ----------------------------------------------------------


def test_an_atom_entry_links_to_the_article_not_its_comments() -> None:
    from modules.news.module import parse_feed

    feed = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Post</title>
    <link rel="replies" href="https://blog.ex/feeds/1/comments/default"/>
    <link rel="edit" href="https://blog.ex/edit/1"/>
    <link rel="alternate" href="https://blog.ex/2026/10/post.html"/></entry></feed>"""
    assert parse_feed(feed)[0]["link"] == "https://blog.ex/2026/10/post.html"


def test_mail_with_raw_utf8_headers_and_a_forwarded_mail_attached() -> None:
    from modules.mail.module import _body, _decode, _reply_target

    raw = (
        "From: Jürgen Müller <j@x.de>\r\nSubject: Grüße\r\nMIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="b"\r\n\r\n--b\r\n'
        "Content-Type: text/html; charset=utf-8\r\n\r\n<p>Bitte die Rechnung prüfen.</p>\r\n"
        "--b\r\nContent-Type: message/rfc822\r\nContent-Disposition: attachment\r\n\r\n"
        "Subject: alt\r\nContent-Type: text/plain\r\n\r\nText der ALTEN Mail\r\n--b--\r\n"
    ).encode()
    message = email.message_from_bytes(raw)
    assert _reply_target(message) == "Jürgen Müller <j@x.de>"
    assert _decode(message["Subject"]) == "Grüße"
    assert "Rechnung prüfen" in _body(message) and "ALTEN" not in _body(message)


def test_a_price_split_over_html_tags_is_read_whole() -> None:
    from modules.watch.module import find_price

    assert find_price("TV\n49\n,99\n€") == 49.99  # 49<sup>,99</sup> €
    assert find_price("1.299 ,00\xa0€") == 1299.0  # <span>1.299</span><span>,00 €</span>
    assert find_price("1\u202f299,00 €") == 1299.0
    assert find_price("49,99 €") == 49.99


def test_calendar_files_from_outlook_and_old_repeats() -> None:
    from modules.calendar import ics

    text = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a\r\nSUMMARY:Zahnarzt\r\n"
        'DTSTART;TZID="(UTC+01:00) Amsterdam, Berlin, Bern, Rom, Stockholm, Wien":'
        "20261013T090000\r\nDESCRIPTION:Datei: D:\\\\Projekte\\\\neu\\\\notizen.txt\r\n"
        "END:VEVENT\r\nBEGIN:VEVENT\r\nUID:b\r\nSUMMARY:Kaputt\r\nDTSTART:2026-xx\r\n"
        "END:VEVENT\r\nBEGIN:VEVENT\r\nUID:c\r\nSUMMARY:Standup\r\nDTSTART:20200106T091500\r\n"
        "RRULE:FREQ=DAILY\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:d\r\nSUMMARY:Team\r\n"
        "DTSTART:20261013T100000\r\nRRULE:FREQ=MONTHLY;BYDAY=2TU\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    events = {e.uid: e for e in ics.parse(text)}
    assert set(events) == {"a", "c", "d"}  # the broken one is skipped, not the calendar
    assert events["a"].notes == "Datei: D:\\Projekte\\neu\\notizen.txt"
    day = datetime(2026, 10, 9)
    assert events["c"].occurrences(day, day.replace(hour=23)) == [datetime(2026, 10, 9, 9, 15)]
    team = events["d"].occurrences(datetime(2026, 10, 1), datetime(2027, 1, 31))
    assert [t.date().isoformat() for t in team] == [
        "2026-10-13",
        "2026-11-10",
        "2026-12-08",
        "2027-01-12",
    ]


def test_calc_works_like_a_pocket_calculator() -> None:
    from modules.calc.module import evaluate

    assert evaluate("249.90 + 19%") == pytest.approx(297.381)
    assert evaluate("100 - 20%") == 80
    assert evaluate("round(2.5)") == 3 and evaluate("round(0.125, 2)") == 0.13
    started = time.monotonic()
    for formula in ("(10**5000)**3000", "10**4000 * 10**4000"):
        with pytest.raises(Exception, match=r"too large|digits"):
            evaluate(formula)
    assert time.monotonic() - started < 1


def test_the_ssh_read_only_check_refuses_commands_that_change_things() -> None:
    from modules.network.readonly import is_read_only

    for command in (
        "env reboot",
        "ip link set eth0 down",
        "journalctl --vacuum-time=1s",
        "git branch -D main",
        "hostnamectl set-hostname x",
        "date -s 2020-01-01",
        "hostname evil",
        "dmesg -C",
    ):
        assert not is_read_only(command), command
    for command in ("ip -br addr", "journalctl -u nginx -n 50", "git branch -a", "date +%F"):
        assert is_read_only(command), command


# -- automations and commands ----------------------------------------------------------------------


def test_a_condition_on_a_missing_field_does_not_fire() -> None:
    from modules.automations.module import matches

    assert not matches("days_left < 14", {}) and matches("days_left < 14", {"days_left": 3})
    assert matches("status != done", {})


async def test_an_automation_approval_covers_its_own_step_only(
    paths: Paths, write_module: Callable[..., Path], use_module: Callable[[str], Path]
) -> None:
    from starlight.core.model import ModelResponse, ToolCall

    write_module(
        "probe",
        """
        from starlight.core import Module, Permission, tool
        CALLS = []
        class Mod(Module):
            @tool(permission=Permission.CONFIRM)
            def wipe(self, what: str) -> str:
                "wipe something"
                CALLS.append(what)
                return f"wiped {what}"
        """,
    )
    use_module("automations")

    class Steered:
        """In the rule's "ask" step, the model is talked into wiping everything."""

        name = "steered"

        async def chat(self, messages: list[Any], tools: list[Any]) -> ModelResponse:
            if messages[-1].role == "user" and "wiped" in messages[-1].content:
                return ModelResponse(tool_calls=[ToolCall("probe.wipe", {"what": "EVERYTHING"})])
            return ModelResponse(content="done")

    async with Kernel(paths, ui=User(), provider=Steered()) as k:
        await call(
            k,
            "automations.add",
            name="clean",
            when="every day at 03:00",
            tool="probe.wipe",
            args={"what": "tmp"},
            ask="Say what was wiped: {result}",
        )
        await call(k, "automations.run_now", name="clean")
        probe = sys.modules[type(instance(k, "probe")).__module__]
    assert probe.CALLS == ["tmp"]  # not "EVERYTHING"


@pytest.mark.skipif(sys.platform == "win32", reason="sh syntax; see the Windows test below")
async def test_a_command_that_times_out_returns_its_output_and_stops_its_children(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("commands")
    (paths.module_configs / "commands.yaml").write_text("shell: true\nshell_timeout: 2\n")
    async with Kernel(paths, ui=User()) as k:
        partial = await call(k, "commands.shell", command="echo step1-done; sleep 30")
        started = time.monotonic()
        child = await call(k, "commands.shell", command="sleep 8 & echo started")
        took = time.monotonic() - started
    assert partial["timed_out"] and "step1-done" in partial["output"]
    assert child["timed_out"] and "started" in child["output"] and took < 6


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
async def test_a_background_program_is_stopped_on_windows_too(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    psutil = pytest.importorskip("psutil")
    use_module("commands")
    (paths.module_configs / "commands.yaml").write_text("shell: true\nshell_timeout: 4\n")
    async with Kernel(paths, ui=User()) as k:
        started = time.monotonic()
        # cmd has exited at once, but the ping it started in the background still holds
        # the output open; taskkill /T can't find it any more (its parent is gone).
        result = await call(k, "commands.shell", command='cmd /c "start /b ping -n 31 127.0.0.1"')
        took = time.monotonic() - started
    await asyncio.sleep(0.5)
    left = [
        p
        for p in psutil.process_iter(["name", "cmdline"])
        if (p.info["name"] or "").lower() == "ping.exe" and "31" in (p.info["cmdline"] or [])
    ]
    assert result["timed_out"] and took < 8  # without the job: 5 s more, waiting for ping
    assert not left  # and ping would still be running
