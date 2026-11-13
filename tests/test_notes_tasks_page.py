"""Block D4 of the notes plan (docs/plan-notes-app.md): tasks on the Notes page. Boxes tick in
the reading view and with Ctrl+Enter in the editor (both write ``done:``); dates show as
chips (red when overdue, amber when today); the Tasks view groups the open tasks of all
notes."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_editing import SAVED, start_editing
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL

# "Today" is taken ONCE per test. A test that asked the clock each time wrote its notes on one
# day and compared them on the next when a run crossed midnight.
TODAY = dt.date.today()


@pytest.fixture(autouse=True)
def _one_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{__name__}.TODAY", dt.date.today())


def days(n: int) -> str:
    return (TODAY + dt.timedelta(days=n)).isoformat()


def versions(app: Any) -> int:
    kept = app.get("/api/note/versions", params={"path": "Notes/Week.md"}, headers=LOCAL)
    return len(kept.json()["versions"])


def week(notebook: Path) -> Path:
    note = notebook / "Notes" / "Week.md"
    note.write_text(
        "# Week\n"
        f"- [ ] Pay the bill due: {days(-2)} priority: high\n"
        f"- [ ] Call Tom due: {days(0)}\n"
        f"- [ ] Water plants due: {days(0)} repeat: every week\n"
        f"- [ ] Book the trip due: {days(5)} priority: low\n"
        "- [ ] Read a book\n"
        "- [x] Old thing done: 2026-10-01\n"
        "> - [ ] in a quote\n",
        encoding="utf-8",
    )
    return note


def test_the_api_ticks_by_line_or_id_and_lists_the_tasks(app: Any, notebook: Path) -> None:
    note = week(notebook)
    read = app.get("/api/note", params={"path": "Notes/Week.md"}, headers=LOCAL).json()
    assert [(t["line"], t["status"], t["text"]) for t in read["tasks"]][:2] == [
        (1, " ", "Pay the bill"),
        (2, " ", "Call Tom"),
    ]
    assert read["tasks"][1]["raw"] == f"- [ ] Call Tom due: {days(0)}"

    listed = app.get("/api/notes/tasks", headers=LOCAL).json()
    assert listed["date"] == days(0)
    groups = {g: [t["text"] for t in listed[g]] for g in ("overdue", "today", "upcoming", "none")}
    assert groups["overdue"] == ["Pay the bill"]
    assert groups["today"] == ["Call Tom", "Water plants"]
    assert groups["upcoming"] == ["Book the trip"]
    assert "Read a book" in groups["none"] and "Old thing" not in str(listed)
    assert "in a quote" not in str(listed)  # (a quote is not a task)

    body = {"path": "Notes/Week.md", "line": 2, "expected": read["tasks"][1]["raw"]}
    ticked = app.post("/api/note/task", json=body, headers=LOCAL)
    assert ticked.status_code == 200 and ticked.json()["changed"] is True
    assert f"- [x] Call Tom due: {days(0)} done: {days(0)}\n" in note.read_text("utf-8")
    again = app.post("/api/note/task", json=body, headers=LOCAL)  # the line is not as shown
    assert again.status_code == 409 and "changed meanwhile" in again.json()["detail"]

    water = next(t for t in listed["today"] if t["text"] == "Water plants")
    by_id = {"path": "Notes/Week.md", "id": water["id"], "done": True}
    repeated = app.post("/api/note/task", json=by_id, headers=LOCAL).json()
    assert repeated["repeated"] is True and repeated["next_due"] == days(7)
    assert f"- [ ] Water plants due: {days(7)} repeat: every week\n" in note.read_text("utf-8")
    bill = {"path": "Notes/Week.md", "id": listed["overdue"][0]["id"], "done": False}
    assert app.post("/api/note/task", json=bill, headers=LOCAL).json()["changed"] is False
    assert versions(app) == 2  # a version before each tick (none when nothing changed)
    bad = app.post("/api/note/task", json={"path": "Notes/Week.md"}, headers=LOCAL)
    assert bad.status_code == 400


def test_ticking_a_repeating_task_in_the_editor_adds_the_next_one(tmp_path: Path) -> None:
    from starlight.notebook import tasks

    today = dt.date(2026, 11, 2)
    before = "# Chores\n- [ ] Water plants due: 2026-11-02 repeat: every week\n- [ ] Dust\n"
    water = "- [ ] Water plants due: 2026-11-02 repeat: every week"
    ticked = before.replace(water, water.replace("[ ]", "[x]") + " done: 2026-11-02")
    after = tasks.with_next_ones(ticked, before, today)
    assert after.splitlines()[1:4] == [
        "- [x] Water plants due: 2026-11-02 repeat: every week done: 2026-11-02",
        "- [ ] Water plants due: 2026-11-09 repeat: every week",
        "- [ ] Dust",
    ]
    assert tasks.with_next_ones(after, ticked, today) == after  # (only once)
    written_ticked = "- [x] Done before repeat: every day done: 2026-11-01\n"
    assert tasks.with_next_ones(written_ticked, "", today) == written_ticked


def test_the_page_save_adds_the_next_repeating_task(app: Any, notebook: Path) -> None:
    week(notebook)
    read = app.get("/api/note", params={"path": "Notes/Week.md"}, headers=LOCAL).json()
    line = f"- [ ] Water plants due: {days(0)} repeat: every week"
    text = read["text"].replace(line, line.replace("[ ]", "[x]") + f" done: {days(0)}")
    body = {"text": text, "stamp": read["stamp"]}
    saved = app.put("/api/note", params={"path": "Notes/Week.md"}, json=body, headers=LOCAL).json()
    assert (
        f"done: {days(0)}\n- [ ] Water plants due: {days(7)} repeat: every week\n"
        in (saved["text"])
    )


async def test_ticking_in_the_reading_view_and_the_chips(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    note = week(notebook)
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FWeek.md")
        await page.wait_for_selector("#column .note-body li.task")
        rows = await page.locator("#column .note-body li.task").evaluate_all(
            "ls => ls.map(l => [l.querySelector('.task-text')?.textContent ?? l.textContent.trim(),"
            " [...l.querySelectorAll('.task-chip')].map(c => c.className), "
            " l.querySelector('input').disabled])"
        )
        assert rows[:4] == [
            ["Pay the bill", ["task-chip task-due overdue", "task-chip task-priority high"], False],
            ["Call Tom", ["task-chip task-due today"], False],
            ["Water plants", ["task-chip task-due today", "task-chip task-repeat"], False],
            ["Book the trip", ["task-chip task-due", "task-chip task-priority low"], False],
        ]
        assert rows[5][1] == ["task-chip task-done"]  # (done: no colour)
        assert rows[6][2] is True  # a task in a quote stays read-only
        chip = page.locator("#column .note-body li.task .task-due").first
        assert await chip.get_attribute("data-date") == days(-2)

        await page.locator("#column .note-body li.task .task-box").nth(1).click()
        await page.wait_for_function(
            "() => document.querySelectorAll('#column .note-body li.task')[1]"
            "?.dataset.status === 'done'"
        )
        assert f"- [x] Call Tom due: {days(0)} done: {days(0)}\n" in note.read_text("utf-8")
        await page.locator("#column .note-body li.task .task-box").nth(2).click()  # repeats
        await page.wait_for_function(
            "() => document.querySelectorAll('#column .note-body li.task').length === 8"
        )
        assert f"- [ ] Water plants due: {days(7)} repeat: every week\n" in note.read_text("utf-8")

        note.write_text(note.read_text("utf-8").replace("Read a book", "Read two books"), "utf-8")
        await page.locator("#column .note-body li.task .task-box").nth(5).click()  # changed
        await page.wait_for_selector("text=changed meanwhile")
        assert "- [ ] Read two books\n" in note.read_text("utf-8")  # nothing was overwritten
        assert page.errors == []  # type: ignore[attr-defined]
    assert versions(app) == 2


async def test_ctrl_enter_in_the_editor_writes_done(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    note = week(notebook)
    async with notes_page(static_site, app) as page:
        await start_editing(page, static_site, "Notes%2FWeek.md")
        await page.locator(".cm-line", has_text="Read a book").click()
        await page.keyboard.press("Control+Enter")
        await page.wait_for_function(SAVED)
        await page.wait_for_function(
            "() => document.querySelector('.cm-content').textContent.includes('Read a book done:')"
        )
        assert f"- [x] Read a book done: {days(0)}\n" in note.read_text("utf-8")
        await page.keyboard.press("Control+Enter")  # and open again: the date goes
        await page.wait_for_function(
            "() => !document.querySelector('.cm-content').textContent.includes('Read a book done:')"
        )
        await page.wait_for_function(SAVED)
        assert "- [ ] Read a book\n" in note.read_text("utf-8")

        await page.locator(".cm-line", has_text="Water plants").click()
        await page.keyboard.press("Control+Enter")  # a repeating one: the next comes on save
        await page.wait_for_function(
            f"() => document.querySelector('.cm-content').textContent.includes('due: {days(7)}')"
        )
        await page.wait_for_function(SAVED)
        assert f"- [ ] Water plants due: {days(7)} repeat: every week\n" in note.read_text("utf-8")
        assert page.errors == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(("scheme", "locale"), [("light", "en-US"), ("dark", "de-DE")])
async def test_the_tasks_view(
    static_site: str,
    app: Any,
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    locale: str,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    note = week(notebook)
    german = locale.startswith("de")
    async with notes_page(static_site, app, locale=locale, scheme=scheme) as page:
        await opened(page, f"{static_site}/notes.html")
        await page.locator('.quick button[data-view="tasks"]').click()
        await page.wait_for_selector(".task-group")
        groups = await page.locator(".task-group").evaluate_all(
            "gs => gs.map(g => [g.dataset.group, g.querySelector('h3').firstChild.textContent,"
            " [...g.querySelectorAll('.task-text')].map(t => t.textContent)])"
        )
        names = ["Überfällig", "Heute", "Demnächst", "Ohne Datum"] if german else [
            "Overdue", "Today", "Upcoming", "No date"]  # fmt: skip
        assert [g[:2] for g in groups][:3] == [
            ["overdue", names[0]], ["today", names[1]], ["upcoming", names[2]],
        ]  # fmt: skip
        assert groups[0][2] == ["Pay the bill"] and groups[1][2] == ["Call Tom", "Water plants"]
        assert groups[3][0] == "none" and "Read a book" in groups[3][2]
        assert await page.locator(".task-group .task-due.overdue").count() == 1
        assert await page.locator(".task-row .task-note").first.text_content() == "Week"

        await page.locator('.task-row:has-text("Call Tom") .task-box').click()
        await page.wait_for_selector('.task-row.done:has-text("Call Tom")')
        assert f"- [x] Call Tom due: {days(0)} done: {days(0)}\n" in note.read_text("utf-8")
        await page.locator('.task-row:has-text("Call Tom") .task-box').click()  # undone again
        await page.wait_for_selector('.task-row:not(.done):has-text("Call Tom")')
        assert f"- [ ] Call Tom due: {days(0)}\n" in note.read_text("utf-8")
        await page.locator('.task-row:has-text("Week") .task-note').first.click()
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Week'"
        )
        assert page.errors == []  # type: ignore[attr-defined]


async def test_the_tasks_view_without_tasks(
    static_site: str, app: Any, notebook: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    (notebook / "Notes" / "Shopping.md").write_text("Nothing to do.\n", encoding="utf-8")
    welcomed = tmp_path / "h" / "data" / "notebook" / "welcomed"  # (no welcome note)
    welcomed.parent.mkdir(parents=True, exist_ok=True)
    welcomed.write_text("1", encoding="utf-8")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html")
        await page.locator('.quick button[data-view="tasks"]').click()
        await page.wait_for_selector("text=No open tasks")
        assert page.errors == []  # type: ignore[attr-defined]
