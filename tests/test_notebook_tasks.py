"""Block D3 of the notes plan (docs/plan-notes-app.md): a task anywhere in a note counts like a
to-do. Plain-word keys in English and German; typed natural dates become ISO dates when
Starlight or the Notes page saves; the to-do tools list, tick and reopen note tasks (in the
note: a version first, refused when the task changed); the briefing shows them."""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.notebook import markdown
from starlight.notebook.history import History
from starlight.notebook.index import Index
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_home_devices import call, fails
from tests.test_notes_editing import SAVED, start_editing, type_at_end
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page
from tests.test_remote import LOCAL

TODAY = dt.date(2026, 11, 2)  # a Monday


def test_tasks_in_any_list_form_with_english_or_german_keys() -> None:
    text = (
        "- [ ] Call Tom due: 2026-11-05 priority: high repeat: every month start: 2026-11-01\n"
        "* [x] Paid the bill done: 2026-11-01\n"
        "1. [ ] Order wood fällig: 2026-11-04 priorität: niedrig\n"
        "\t+ [ ] Water plants wiederholen: jeden Montag #home ^w1\n"
        "- [X] Old thing erledigt: 2026-10-30\n"
        "- [ ] Inline `due: tomorrow` stays text\n"
        "- [ ] Write due: next friday then more\n"
        "Not a task due: tomorrow\n"
    )
    found = [
        (t.text, t.status, t.due, t.start, t.done, t.priority, t.repeat)
        for t in markdown.parse(text).tasks
    ]
    assert found == [
        ("Call Tom", " ", "2026-11-05", "2026-11-01", "", "high", "every month"),
        ("Paid the bill", "x", "", "", "2026-11-01", "", ""),
        ("Order wood", " ", "2026-11-04", "", "", "low", ""),
        ("Water plants #home", " ", "", "", "", "", "jeden Montag"),
        ("Old thing", "X", "", "", "2026-10-30", "", ""),
        ("Inline `due: tomorrow` stays text", " ", "", "", "", "", ""),
        ("Write then more", " ", "next friday", "", "", "", ""),
    ]
    first = markdown.parse(text).tasks[0]
    assert first.span("due") == (15, 20, 30) and first.span("done") is None


@pytest.mark.parametrize(
    ("typed", "day"),
    [
        ("today", "2026-11-02"), ("heute", "2026-11-02"), ("tomorrow", "2026-11-03"),
        ("Morgen", "2026-11-03"), ("übermorgen", "2026-11-04"), ("yesterday", "2026-11-01"),
        ("Friday", "2026-11-06"), ("freitag", "2026-11-06"),
        ("Monday", "2026-11-09"),  # a weekday is the next one, not today
        ("next friday", "2026-11-06"), ("nächsten Freitag", "2026-11-06"),
        ("next week", "2026-11-09"), ("nächste Woche", "2026-11-09"),
        ("in 3 days", "2026-11-05"), ("in 2 Wochen", "2026-11-16"), ("in 1 month", "2026-12-02"),
        ("24.12.", "2026-12-24"), ("1.1.", "2027-01-01"), ("31.10.2027", "2027-10-31"),
        ("2026-11-05", "2026-11-05"),
        ("someday", None), ("fri", None), ("2026-02-30", None), ("31.2.", None),
        ("this week", None), ("in 3 weeks ago", None),
    ],
)  # fmt: skip
def test_natural_dates_in_english_and_german(typed: str, day: str | None) -> None:
    found = markdown.natural_date(typed, TODAY)
    assert (found.isoformat() if found else None) == day


def test_typed_dates_become_iso_dates_and_nothing_else_changes() -> None:
    text = (
        "# Plan\r\n"
        "- [ ] Call Tom due: tomorrow priority: high ^t1\r\n"
        "- [ ] Pay fällig: nächsten Freitag\r\n"
        "- [ ] Clean start: in 3 days due: 24.12.\r\n"
        "- [ ] Someday due: someday\r\n"
        "The meeting is due: tomorrow\r\n"
        "```\r\n- [ ] code due: tomorrow\r\n```\r\n"
    )
    assert markdown.iso_dates(text, TODAY) == (
        "# Plan\r\n"
        "- [ ] Call Tom due: 2026-11-03 priority: high ^t1\r\n"
        "- [ ] Pay fällig: 2026-11-06\r\n"
        "- [ ] Clean start: 2026-11-05 due: 2026-12-24\r\n"
        "- [ ] Someday due: someday\r\n"
        "The meeting is due: tomorrow\r\n"
        "```\r\n- [ ] code due: tomorrow\r\n```\r\n"
    )
    before = "- [ ] Old due: tomorrow\n"  # (written on another day: its words stay)
    now = markdown.iso_dates(before + "- [ ] New due: tomorrow\n", TODAY, before=before)
    assert now == "- [ ] Old due: tomorrow\n- [ ] New due: 2026-11-03\n"
    assert markdown.iso_dates("No tasks here.", TODAY) == "No tasks here."


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_note_tasks_their_ids_and_what_is_left_out(tmp_path: Path) -> None:
    from starlight.notebook import tasks

    root = tmp_path / "Starlight"
    plan = root / "Notes" / "Plan.md"
    write(plan, "# Plan\n- [ ] Call Tom due: 2026-11-05\n- [ ] Call Tom\n- [ ] \n- [x] Paid\n")
    write(root / "Templates" / "Meeting.md", "- [ ] Prepare {{title}}\n")
    write(root / "Clippings" / "Page.md", "---\nsource: https://x.example/\n---\n- [ ] Send me\n")
    old = root / "Notes" / "Old.md"
    write(old, "- [ ] Typed elsewhere due: tomorrow\n")
    written = dt.datetime(2026, 10, 1, 12, 0).timestamp()
    os.utime(old, (written, written))
    index = Index(tmp_path / "index.sqlite", root)
    found = tasks.note_tasks(index)
    assert [(t.path, t.title, t.text, t.status, t.due) for t in found] == [
        ("Notes/Old.md", "Old", "Typed elsewhere", " ", "2026-10-02"),  # from the day written
        ("Notes/Plan.md", "Plan", "Call Tom", " ", "2026-11-05"),
        ("Notes/Plan.md", "Plan", "Call Tom", " ", ""),
        ("Notes/Plan.md", "Plan", "Paid", "x", ""),
    ]
    ids = [t.id for t in found]
    assert len(set(ids)) == 4 and all(re.fullmatch(r"n[0-9a-f]{6}", i) for i in ids)
    assert found[3].view() == {
        "id": ids[3], "text": "Paid", "note": "Plan", "path": "Notes/Plan.md",
        "priority": "normal", "done": True,
    }  # fmt: skip

    # moved down, with another date and a priority: the same ids
    write(plan, "# Plan\n\nFirst this.\n- [ ] Call Tom due: 2026-11-06 priority: high\n"
          "- [ ] Call Tom\n- [x] Paid\n")  # fmt: skip
    index.refresh(force=True)
    again = tasks.note_tasks(index)
    assert [t.id for t in again] == ids and again[1].line == 3
    write(plan, "# Plan\n- [ ] Call Tom Jones\n")  # another text: another task
    index.refresh(force=True)
    assert tasks.note_tasks(index)[1].id not in ids


def test_ticking_writes_back_keeps_a_version_and_refuses_a_changed_line(tmp_path: Path) -> None:
    from starlight.notebook import NoteConflict, tasks

    file = tmp_path / "Plan.md"
    file.write_bytes(
        b"\xef\xbb\xbf# Plan\r\n- [ ] Call Tom due: 2026-11-05 #work ^t1\r\n"
        b"- [ ] Water plants due: 2026-11-02 repeat: every week\r\n"
    )
    history = History(tmp_path / "history.sqlite")

    def lines() -> list[str]:
        raw = file.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf") and b"\n" not in raw.replace(b"\r\n", b"")
        return raw.decode("utf-8-sig").split("\r\n")  # (its BOM and CRLF are kept)

    call_tom = "- [ ] Call Tom due: 2026-11-05 #work ^t1"
    first = tasks.tick(file, history, line=1, expected=call_tom, today=TODAY)
    assert first.written is not None and first.written.version is not None
    assert len(history.versions(file)) == 1  # the note as it was is kept
    assert lines()[1] == "- [x] Call Tom due: 2026-11-05 #work done: 2026-11-02 ^t1"

    with pytest.raises(NoteConflict, match="changed meanwhile"):  # the line is not as shown
        tasks.tick(file, history, line=1, expected=call_tom, done=False, today=TODAY)
    with pytest.raises(NoteConflict, match="not a task"):
        tasks.tick(file, history, line=0, expected="# Plan", today=TODAY)
    assert len(history.versions(file)) == 1  # nothing was written

    tasks.tick(file, history, line=1, expected=lines()[1], done=False, today=TODAY)
    assert lines()[1] == call_tom  # opened again: the done date goes

    water = "- [ ] Water plants due: 2026-11-02 repeat: every week"
    ticked = tasks.tick(file, history, line=2, expected=water, today=TODAY)
    assert ticked.repeated and ticked.next_due == "2026-11-09"
    assert lines()[2:4] == [
        "- [x] Water plants due: 2026-11-02 repeat: every week done: 2026-11-02",
        "- [ ] Water plants due: 2026-11-09 repeat: every week",  # the next one, below
    ]
    assert tasks.tick(file, history, line=2, expected=lines()[2]).written is None  # (done)

    gone = tasks.task_id("Notes/Plan.md", "Call Tom (changed)")
    with pytest.raises(NoteConflict, match="as it was"):
        tasks.tick_by_id(file, "Notes/Plan.md", gone, history)


@pytest.mark.parametrize(
    ("rule", "line", "next_line"),
    [
        ("every day", "due: 2026-11-02", "due: 2026-11-03"),
        ("täglich", "fällig: 2026-11-02", "fällig: 2026-11-03"),
        ("every 2 weeks", "due: 2026-11-02", "due: 2026-11-16"),
        ("jeden Montag", "due: 2026-11-04", "due: 2026-11-09"),
        ("monatlich", "due: 2026-01-31", "due: 2026-02-28"),
        ("every year", "due: 2028-02-29", "due: 2029-02-28"),
        ("alle 3 Tage", "start: 2026-11-01 due: 2026-11-03", "start: 2026-11-04 due: 2026-11-06"),
        ("every week", "", ""),  # no date: it just comes back
    ],
)
def test_repeating_tasks_come_back_on_their_next_day(
    tmp_path: Path, rule: str, line: str, next_line: str
) -> None:
    from starlight.notebook import tasks

    file = tmp_path / "Chores.md"
    task = f"- [ ] Chore {line} repeat: {rule}".replace("  ", " ")
    file.write_text(task + "\n", encoding="utf-8")
    tasks.tick(file, None, line=0, expected=task, today=TODAY)
    expected = f"- [ ] Chore {next_line} repeat: {rule}".replace("  ", " ")
    assert file.read_text("utf-8").splitlines()[1] == expected


@pytest.fixture
def garden(paths: Paths, use_module: Callable[[str], Path], documents: Path) -> Path:
    for name in ("todo", "notes", "briefing"):
        use_module(name)
    today = dt.date.today()
    note = documents / "Starlight" / "Notes" / "Garden.md"
    write(
        note,
        f"# Garden\n- [ ] Call Tom about the fence due: {today} priority: high\n"
        f"- [ ] Buy seeds\n- [ ] Buy seeds\n- [x] Mow the lawn done: {today}\n",
    )
    return note


async def test_note_tasks_are_listed_due_and_ticked_like_todos(paths: Paths, garden: Path) -> None:
    today = dt.date.today()
    async with Kernel(paths) as k:
        await call(k, "todo.add", text="pay rent", due="today")
        await call(k, "todo.add", text="buy milk", list="shopping")
        listed = (await call(k, "todo.list"))["tasks"]
        assert [(t["text"], t.get("note")) for t in listed] == [
            ("Call Tom about the fence", "Garden"),  # due today, high: first
            ("pay rent", None),
            ("buy milk", None),
            ("Buy seeds", "Garden"),
            ("Buy seeds", "Garden"),
        ]
        tom = listed[0]
        assert tom["path"] == "Notes/Garden.md" and tom["priority"] == "high"
        shopping = await call(k, "todo.list", list="shopping")
        assert [t["text"] for t in shopping["tasks"]] == ["buy milk"]  # (notes are on no list)
        due = await call(k, "todo.due")
        assert [t["text"] for t in due["due"]] == ["Call Tom about the fence", "pay rent"]

        done = await call(k, "todo.done", id="call tom")  # "done: call Tom"
        assert done["note"] == "Garden" and done["changed"]["version"] is not None
        text = garden.read_text("utf-8")
        line = f"- [x] Call Tom about the fence due: {today} priority: high done: {today}\n"
        assert line in text
        assert "Call Tom" not in str(await call(k, "todo.due"))

        assert "matches 2 tasks" in await fails(k, "todo.done", id="seeds")
        seeds = [t["id"] for t in listed if t["text"] == "Buy seeds"]
        assert len(set(seeds)) == 2
        await call(k, "todo.done", id=seeds[1])
        assert f"- [ ] Buy seeds\n- [x] Buy seeds done: {today}\n" in garden.read_text("utf-8")

        reopened = await call(k, "todo.reopen", id=tom["id"])
        assert reopened["reopened"] is True and reopened["note"] == "Garden"
        assert f"- [ ] Call Tom about the fence due: {today} priority: high\n" in garden.read_text(
            "utf-8"
        )
        finished = (await call(k, "todo.list", include_done=True))["tasks"]
        assert ("Mow the lawn", True) in [(t["text"], t.get("done")) for t in finished]
        assert "in the note 'Garden'" in await fails(k, "todo.change", id="fence", due="friday")
        assert "in the note 'Garden'" in await fails(k, "todo.remove", id=seeds[0])

        assert (await call(k, "todo.done", id=seeds[1]))["already"] is True  # (nothing written)
        before = garden.read_text("utf-8").replace("the fence", "the gate")
        garden.write_text(before, encoding="utf-8")  # changed in another program meanwhile
        await fails(k, "todo.done", id=tom["id"])
        assert garden.read_text("utf-8") == before  # nothing was overwritten
    assert len(History(paths.data / "history" / "history.sqlite").versions(garden)) == 3


async def test_the_briefing_shows_a_note_task(paths: Paths, garden: Path) -> None:
    async with Kernel(paths) as k:
        out = await call(k, "briefing.briefing", sections="todo")
    assert "Call Tom about the fence" in out["text"]


async def test_note_tasks_can_be_left_out(paths: Paths, garden: Path) -> None:
    async with Kernel(paths) as k:
        assert (await call(k, "todo.list"))["count"] == 3  # (the garden's open ones)
    (paths.module_configs / "todo.yaml").write_text("note_tasks: false\n")
    async with Kernel(paths) as k:
        assert (await call(k, "todo.list"))["count"] == 0


async def test_the_list_works_when_the_starlight_folder_setting_is_wrong(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("todo")
    inside = paths.root / "my files"  # (inside Starlight's own folder: refused)
    paths.config_file.write_text(
        paths.config_file.read_text() + f"output:\n  folder: '{inside.as_posix()}'\n"
    )
    async with Kernel(paths) as k:
        await call(k, "todo.add", text="pay rent", due="today")
        listed = await call(k, "todo.list")
        assert [t["text"] for t in listed["tasks"]] == ["pay rent"]
        assert (await call(k, "todo.due"))["count"] == 1
        assert (await call(k, "todo.done", id="rent"))["text"] == "pay rent"


async def test_starlight_writes_typed_dates_as_iso_dates(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    for name in ("notes", "journal"):
        use_module(name)
    today = dt.date.today()
    tomorrow = (today + dt.timedelta(days=1)).isoformat()
    friday = markdown.natural_date("friday", today)
    assert friday is not None
    async with Kernel(paths) as k:
        await call(
            k,
            "notes.note",
            title="Errands",
            text="- [ ] Bank due: tomorrow\n- [ ] Post fällig: Freitag",
        )
        await call(k, "notes.append", title="Errands", text="- [ ] Books due: in 3 days")
        await call(k, "journal.write", text="- [ ] Ring Anna due: tomorrow")
    errands = (documents / "Starlight" / "Notes" / "Errands.md").read_text("utf-8")
    in_three = (today + dt.timedelta(days=3)).isoformat()
    assert f"Bank due: {tomorrow}\n" in errands and f"Post fällig: {friday}\n" in errands
    assert f"Books due: {in_three}\n" in errands
    day = (documents / "Starlight" / "Journal" / f"{today}.md").read_text("utf-8")
    assert f"Ring Anna due: {tomorrow}" in day


def test_the_page_saves_typed_dates_as_iso_dates(app: Any, notebook: Path) -> None:
    file = notebook / "Notes" / "Plan.md"
    file.write_text(file.read_text("utf-8") + "- [ ] Old due: tomorrow\n", encoding="utf-8")
    opened = app.get("/api/note", params={"path": "Notes/Plan.md"}, headers=LOCAL).json()
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    text = opened["text"] + "- [ ] Call Tom due: tomorrow\n"
    body = {"text": text, "stamp": opened["stamp"]}
    saved = app.put("/api/note", params={"path": "Notes/Plan.md"}, json=body, headers=LOCAL).json()
    assert saved["text"].endswith(f"- [ ] Old due: tomorrow\n- [ ] Call Tom due: {tomorrow}\n")
    assert file.read_text("utf-8") == saved["text"]  # (the old line was typed on another day)
    body = {"text": saved["text"] + "More.\n", "stamp": saved["stamp"]}
    plain = app.put("/api/note", params={"path": "Notes/Plan.md"}, json=body, headers=LOCAL).json()
    assert "text" not in plain  # saving changed nothing: no text comes back


async def test_the_editor_shows_the_saved_iso_date(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    file = notebook / "Notes" / "Plan.md"
    async with notes_page(static_site, app) as page:
        await start_editing(page, static_site)
        await type_at_end(page, "\n- [ ] Call Tom due: tomorrow")
        await page.wait_for_function(
            "() => document.querySelector('.cm-content').textContent"
            f".includes('Call Tom due: {tomorrow}')"
        )
        await page.wait_for_function(SAVED)
        assert file.read_text("utf-8").endswith(f"- [ ] Call Tom due: {tomorrow}")
        await page.keyboard.type(" soon")  # the cursor stayed at the end of the line
        await page.wait_for_function(
            f"() => document.querySelector('.cm-content').textContent.endsWith('{tomorrow} soon')"
        )
        await page.wait_for_function(SAVED)
        assert file.read_text("utf-8").endswith(f"- [ ] Call Tom due: {tomorrow} soon")
        assert page.errors == []  # type: ignore[attr-defined]
