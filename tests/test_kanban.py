"""Block G2 of the notes plan (docs/plan-notes-app.md): a task board. Tasks in notes and the
to-do list's own sit in three columns, open, doing and done; ``status: doing`` on a task
line marks the middle one. Cards move by dragging or with their buttons, and the assistant
moves them with todo.move."""

from __future__ import annotations

import datetime as dt
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from tests.conftest import REPO
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_home_devices import call, fails
from tests.test_notes_page import notebook as notebook  # (the fixture)
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL, client, home

TODAY = dt.date(2026, 11, 2)
WEEK = (
    "# Week\n"
    "- [ ] Paint the fence status: doing due: 2026-11-05 ^p1\n"
    "- [ ] Call Tom\n"
    "- [ ] Water plants repeat: every week due: 2026-11-02 status: läuft\n"
    "- [x] Paid the bill done: 2026-11-01\n"
)


def test_the_status_field_in_english_and_german() -> None:
    from starlight.notebook import markdown

    tasks = markdown.parse(
        WEEK + "- [ ] Plan status: offen\n- [ ] Write status: in Arbeit\n- [ ] Odd status: later\n"
    ).tasks
    assert [(t.text, t.stage) for t in tasks] == [
        ("Paint the fence", "doing"),
        ("Call Tom", ""),
        ("Water plants", "doing"),
        ("Paid the bill", ""),
        ("Plan", "open"),
        ("Write", "doing"),
        ("Odd", "later"),
    ]


def test_moving_a_task_between_columns(tmp_path: Path) -> None:
    from starlight.notebook import NoteConflict, markdown, tasks
    from starlight.notebook.history import History

    file = tmp_path / "Week.md"
    file.write_text(WEEK, encoding="utf-8")
    history = History(tmp_path / "history.sqlite")
    ids = tasks.numbered("Notes/Week.md", [t.text for t in markdown.parse(WEEK).tasks])

    def lines() -> list[str]:
        return file.read_text("utf-8").splitlines()

    tasks.move(file, "Notes/Week.md", ids[1], "doing", history, today=TODAY)
    assert lines()[2] == "- [ ] Call Tom status: doing"
    tasks.move(file, "Notes/Week.md", ids[0], "open", history, today=TODAY)
    assert lines()[1] == "- [ ] Paint the fence due: 2026-11-05 ^p1"  # (the block id stays)
    tasks.move(file, "Notes/Week.md", ids[3], "doing", history, today=TODAY)
    assert lines()[4] == "- [ ] Paid the bill status: doing"  # (done before: open again)
    moved = tasks.move(file, "Notes/Week.md", ids[2], "done", history, today=TODAY)
    assert moved.repeated and moved.next_due == "2026-11-09"
    assert lines()[3:5] == [
        "- [x] Water plants repeat: every week due: 2026-11-02 done: 2026-11-02",
        "- [ ] Water plants repeat: every week due: 2026-11-09",  # (the next one starts open)
    ]
    again = tasks.move(file, "Notes/Week.md", ids[1], "doing", history, today=TODAY)
    assert again.written is None  # (it was there already: nothing written)
    assert len(history.versions(file)) == 4  # a version before each change
    with pytest.raises(NoteConflict, match="as it was"):
        tasks.move(file, "Notes/Week.md", tasks.task_id("Notes/Week.md", "Gone"), "done", history)
    with pytest.raises(ValueError, match="open, doing or done"):
        tasks.move(file, "Notes/Week.md", ids[1], "later", history)


def test_an_older_index_learns_the_status_on_its_own(tmp_path: Path) -> None:
    from starlight.notebook.index import Index
    from starlight.notebook.tasks import note_tasks

    root = tmp_path / "Starlight"
    (root / "Notes").mkdir(parents=True)
    (root / "Notes" / "Week.md").write_text(WEEK, encoding="utf-8")
    db = tmp_path / "index.sqlite"
    Index(db, root).refresh()
    with sqlite3.connect(db) as old:  # as an index before block G2 was
        old.execute("ALTER TABLE tasks DROP COLUMN stage")
        old.execute("PRAGMA user_version = 1")
    index = Index(db, root)
    index.refresh()
    assert [t.stage for t in note_tasks(index)][:3] == ["doing", "", "doing"]


@pytest.fixture
def week(paths: Paths, use_module: Callable[[str], Path], documents: Path) -> Path:
    for name in ("todo", "notes"):
        use_module(name)
    note = documents / "Starlight" / "Notes" / "Week.md"
    note.parent.mkdir(parents=True)
    note.write_text(WEEK, encoding="utf-8")
    return note


async def test_todo_move_for_the_assistant(paths: Paths, week: Path) -> None:
    async with Kernel(paths) as k:
        await call(k, "todo.add", text="buy milk")
        moved = await call(k, "todo.move", id="milk", to="doing")
        assert moved == {"moved": "doing", "text": "buy milk"}
        listed = (await call(k, "todo.list"))["tasks"]
        stages = {t["text"]: t.get("stage") for t in listed}
        assert stages["buy milk"] == "doing" and stages["Paint the fence"] == "doing"
        assert stages["Call Tom"] is None
        await call(k, "todo.move", id="milk", to="Erledigt")  # (German too)
        assert "buy milk" not in str(await call(k, "todo.list"))
        everything = (await call(k, "todo.list", include_done=True))["tasks"]
        milk = next(t for t in everything if t["text"] == "buy milk")
        assert milk.get("done") is True and "stage" not in milk
        await call(k, "todo.move", id=milk["id"], to="open")  # (a finished one: by its id)
        back = next(t for t in (await call(k, "todo.list"))["tasks"] if t["text"] == "buy milk")
        assert "done" not in back and "stage" not in back
        note = await call(k, "todo.move", id="call tom", to="doing")
        assert note["note"] == "Week" and note["changed"]["version"] is not None
        assert "- [ ] Call Tom status: doing\n" in week.read_text("utf-8")
        assert "open, doing or done" in await fails(k, "todo.move", id="call tom", to="later")


@pytest.fixture
def board_app(tmp_path: Path, notebook: Path) -> Iterator[Any]:
    root = home(tmp_path / "h")
    for name in ("notes", "todo"):
        shutil.copytree(REPO / "modules" / name, root / "modules" / name)
    (notebook / "Notes" / "Week.md").write_text(WEEK, encoding="utf-8")
    (notebook / "Notes" / "Shopping.md").write_text("# Shopping\nNothing to do.\n", "utf-8")
    with client(root) as c:
        c.post("/api/notes/welcome", json={}, headers=LOCAL)  # (and no welcome note's tasks:)
        (notebook / "Notes" / "Welcome.md").unlink(missing_ok=True)
        yield c


def test_the_board_api(board_app: Any, notebook: Path, tmp_path: Path) -> None:
    import json

    todo = tmp_path / "h" / "data" / "modules" / "todo" / "todo.json"  # (the list's own)
    todo.parent.mkdir(parents=True, exist_ok=True)
    todo.write_text(json.dumps([{"id": "abc123", "text": "buy milk", "list": "inbox",
                                 "priority": "normal", "due": None, "done": False,
                                 "created": "2026-11-02T10:00:00"}]), "utf-8")  # fmt: skip
    columns = board_app.get("/api/notes/kanban", headers=LOCAL).json()
    assert [(c["text"], c["kind"]) for c in columns["open"]] == [
        ("buy milk", "todo"), ("Call Tom", "note"),
    ]  # fmt: skip
    moved = board_app.post("/api/notes/kanban/move", json={"id": "abc123", "to": "doing"},
                           headers=LOCAL)  # fmt: skip
    assert moved.status_code == 200 and json.loads(todo.read_text("utf-8"))[0]["stage"] == "doing"
    columns = board_app.get("/api/notes/kanban", headers=LOCAL).json()
    assert [c["text"] for c in columns["open"]] == ["Call Tom"]
    assert [c["text"] for c in columns["doing"]] == ["Water plants", "Paint the fence", "buy milk"]
    assert [c["text"] for c in columns["done"]] == ["Paid the bill"]
    assert columns["todo"] is True
    call_tom = columns["open"][0]
    body = {"id": call_tom["id"], "to": "done", "path": call_tom["path"]}
    assert board_app.post("/api/notes/kanban/move", json=body, headers=LOCAL).json()["changed"]
    week = (notebook / "Notes" / "Week.md").read_text("utf-8")
    assert f"- [x] Call Tom done: {dt.date.today().isoformat()}\n" in week
    stale = board_app.post("/api/notes/kanban/move", json={**body, "to": "open", "id": "nffffff"},
                           headers=LOCAL)  # fmt: skip
    assert stale.status_code == 409
    bad = board_app.post("/api/notes/kanban/move", json={"id": "x", "to": "later"}, headers=LOCAL)
    assert bad.status_code == 400


@pytest.mark.parametrize(("scheme", "locale"), [("light", "en-US"), ("dark", "de-DE")])
async def test_the_board_on_the_page(
    static_site: str,
    board_app: Any,
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    locale: str,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    german = locale.startswith("de")
    week = notebook / "Notes" / "Week.md"
    async with notes_page(static_site, board_app, locale=locale, scheme=scheme) as page:
        await opened(page, f"{static_site}/notes.html?view=tasks")
        await page.wait_for_selector(".task-group")  # (the list first)
        await page.locator('.layout-switch [data-layout="columns"]').click()
        await page.wait_for_selector(".kanban-col .kanban-card")
        heads = await page.locator(".kanban-col h3").evaluate_all(
            "hs => hs.map(h => h.firstChild.textContent)"
        )
        assert heads == (
            ["Offen", "In Arbeit", "Erledigt"] if german else ["Open", "Doing", "Done"]
        )

        async def cards(stage: str) -> list[str]:
            return await page.locator(  # type: ignore[no-any-return]
                f'.kanban-col[data-stage="{stage}"] .kanban-text'
            ).all_text_contents()

        assert await cards("open") == ["Call Tom"]
        assert await cards("doing") == ["Water plants", "Paint the fence"]
        assert await cards("done") == ["Paid the bill"]
        card = page.locator('.kanban-col[data-stage="open"] .kanban-card', has_text="Call Tom")
        await card.locator('button[data-to="doing"]').click()  # with its button
        await page.wait_for_function(
            "() => [...document.querySelectorAll('.kanban-col[data-stage=\"doing\"] .kanban-text')]"
            ".some(t => t.textContent === 'Call Tom')"
        )
        assert "- [ ] Call Tom status: doing\n" in week.read_text("utf-8")

        await page.drag_and_drop(  # by dragging
            '.kanban-col[data-stage="doing"] .kanban-card:has-text("Paint the fence")',
            '.kanban-col[data-stage="done"]',
        )
        await page.wait_for_function(
            "() => [...document.querySelectorAll('.kanban-col[data-stage=\"done\"] .kanban-text')]"
            ".some(t => t.textContent === 'Paint the fence')"
        )
        today = dt.date.today().isoformat()
        assert f"- [x] Paint the fence due: 2026-11-05 done: {today} ^p1\n" in week.read_text(
            "utf-8"
        )
        await page.reload()  # the columns are remembered
        await page.wait_for_selector(".kanban-col .kanban-card")
        await opened(page, f"{static_site}/notes.html?path=Notes%2FWeek.md")
        await page.wait_for_selector("#column .note-body li.task")
        doing = await page.locator("#column .note-body .task-stage").all_text_contents()
        assert doing == ["\u25b6 In Arbeit" if german else "\u25b6 Doing"] * 2  # Call Tom, Water
        assert page.errors == []  # type: ignore[attr-defined]
