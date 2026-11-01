"""Block A3 of the notes plan (docs/plan-notes-app.md): after the assistant changes a note or
a file, the chat says so under the answer (Changed: Shopping · Undo) and one click puts it
back, at the PC only; the terminal says the same and has /undo."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.notebook import History, write_note
from starlight.notebook.history import HistoryError
from tests.conftest import REPO
from tests.test_chat_page import SEND, open_chat
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_remote import LOCAL, client, home, pair, phone


def notes_home(root: Path, remote: bool = False, config: str = "") -> Path:
    home(root, remote=remote)
    shutil.copytree(REPO / "modules" / "notes", root / "modules" / "notes")
    if config:
        settings = root / "config" / "starlight.yaml"
        settings.write_text(settings.read_text("utf-8") + config, encoding="utf-8")
    return root


def ask(ws: Any, text: str) -> list[dict[str, Any]]:
    """Ask in the chat (saying yes to any question); the chips the answer brought."""
    ws.send_json({"type": "ask", "text": text})
    chips = []
    while True:
        m = ws.receive_json()
        if m["type"] == "changed":
            chips.append(m)
        elif m["type"] == "confirm":
            ws.send_json({"type": "reply", "id": m["id"], "value": "yes"})
        elif m["type"] == "error":
            pytest.fail(m["text"])
        elif m["type"] == "answer":
            return chips


def undo(c: Any, chip: dict[str, Any], headers: dict[str, str] = LOCAL) -> Any:
    return c.post(f"/api/changes/{chip['id']}/undo", headers=headers)


def test_the_chat_shows_each_change_and_undo_puts_it_back(tmp_path: Path, documents: Path) -> None:
    note = documents / "Starlight" / "Notes" / "Shopping.md"
    with client(notes_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:
        made = ask(ws, '!notes.note {"title": "Shopping", "text": "milk"}')
        added = ask(ws, '!notes.append {"title": "Shopping", "text": "eggs"}')
        assert [m["title"] for m in made + added] == ["Shopping", "Shopping"]
        assert made[0]["new"] and made[0]["undoable"]
        assert not added[0]["new"] and added[0]["undoable"]
        assert Path(added[0]["path"]).name == "Shopping.md"
        assert "version" not in added[0]  # the page gets an id, nothing to make a path from

        back = undo(c, added[0])
        assert back.status_code == 200 and "back as it was" in back.json()["undone"]
        assert "eggs" not in note.read_text("utf-8") and "milk" in note.read_text("utf-8")
        assert undo(c, added[0]).status_code == 404  # once
        assert c.post("/api/changes/made-up/undo", headers=LOCAL).status_code == 404

        gone = undo(c, made[0])  # a new note: undoing it puts it in the trash
        assert gone.status_code == 200 and "trash" in gone.json()["undone"]
        assert not note.exists()
        ask(ws, '!notes.restore_deleted {"title": "Shopping"}')
        assert "milk" in note.read_text("utf-8")  # (and from there it can come back)
        audit = (tmp_path / "h" / "logs" / "audit.log").read_text("utf-8")
    assert audit.count('"web.undo"') == 2


def test_only_the_pc_can_undo(tmp_path: Path, documents: Path) -> None:
    note = documents / "Starlight" / "Notes" / "Shopping.md"
    with client(notes_home(tmp_path / "h", remote=True)) as c:
        token = pair(c)
        with c.websocket_connect("/ws", headers=phone(token)) as ws:
            on_phone = ask(ws, '!notes.note {"title": "Shopping", "text": "milk"}')
        assert on_phone[0]["title"] == "Shopping" and not on_phone[0]["undoable"]
        assert undo(c, on_phone[0], phone(token)).status_code == 403
        assert undo(c, on_phone[0]).status_code == 404  # never offered, not even to the PC

        with c.websocket_connect("/ws", headers=LOCAL) as ws:
            at_pc = ask(ws, '!notes.append {"title": "Shopping", "text": "eggs"}')
        assert undo(c, at_pc[0], phone(token)).status_code == 403
        assert "eggs" in note.read_text("utf-8")
        assert undo(c, at_pc[0]).status_code == 200  # (the phone's try did not use it up)
    assert "eggs" not in note.read_text("utf-8")


def test_without_versions_only_a_new_note_can_be_undone(tmp_path: Path, documents: Path) -> None:
    root = notes_home(tmp_path / "h", config="notebook:\n  keep_versions: false\n")
    with client(root) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:
        made = ask(ws, '!notes.note {"title": "Ideas", "text": "one"}')
        added = ask(ws, '!notes.append {"title": "Ideas", "text": "two"}')
        assert made[0]["undoable"] and not added[0]["undoable"]
        assert undo(c, added[0]).status_code == 404


def test_a_failed_undo_can_be_tried_again(
    tmp_path: Path, documents: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import starlight.web.app as web

    tries: list[str] = []

    def locked_once(history: History, change: dict[str, object]) -> str:
        tries.append(str(change["path"]))
        if len(tries) == 1:
            raise OSError("Shopping.md is open in another program")
        return "Shopping is back as it was"

    monkeypatch.setattr(web, "undo_change", locked_once)
    with client(notes_home(tmp_path / "h")) as c, c.websocket_connect("/ws", headers=LOCAL) as ws:
        ask(ws, '!notes.note {"title": "Shopping", "text": "milk"}')
        added = ask(ws, '!notes.append {"title": "Shopping", "text": "eggs"}')
        failed = undo(c, added[0])
        assert failed.status_code == 400 and "another program" in failed.json()["detail"]
        assert undo(c, added[0]).status_code == 200  # the button tries again: it works
        assert undo(c, added[0]).status_code == 404
    assert len(tries) == 2


def test_undoing_a_change_keeps_the_file_as_it_was(tmp_path: Path) -> None:
    from starlight.notebook import undo_change

    history = History(tmp_path / "history.sqlite")
    letter = tmp_path / "letter.txt"
    write_note(letter, "Dear Tom,", "test", history)
    changed = write_note(letter, "Dear Anna,", "test", history).changed(letter)
    assert undo_change(history, changed) == "letter is back as it was"
    assert letter.read_text("utf-8") == "Dear Tom,"
    assert history.text(history.versions(letter)[0].id) == "Dear Anna,"  # the undo can be undone
    with pytest.raises(HistoryError, match="switched off"):
        undo_change(history, {**changed, "version": None})


# -- the terminal ----------------------------------------------------------------------------


class Terminal:
    """What the terminal chat printed."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, text: str) -> None:
        self.lines.append(text)

    error = info


async def test_the_terminal_says_what_changed_and_undo_puts_it_back(
    paths: Paths, use_module: Any, documents: Path
) -> None:
    from starlight import cli

    use_module("notes")
    note = documents / "Starlight" / "Notes" / "Shopping.md"
    view, changes = Terminal(), []
    async with Kernel(paths) as k:
        for request in (
            '!notes.note {"title": "Shopping", "text": "milk"}',
            '!notes.append {"title": "Shopping", "text": "eggs"}',
        ):
            since = len(changes)
            await cli._ask(k, request, lambda token: None, None, changes)
            cli._show_changes(view, changes, since)  # type: ignore[arg-type]
        assert view.lines == [
            "New: Shopping (undo: /undo)",
            "Changed: Shopping (undo: /undo)",
        ]
        cli._undo(k, view, changes)  # type: ignore[arg-type]
        assert "eggs" not in note.read_text("utf-8") and "milk" in note.read_text("utf-8")
        cli._undo(k, view, changes)  # type: ignore[arg-type]
        assert not note.exists()
        cli._undo(k, view, changes)  # type: ignore[arg-type]
    assert view.lines[2:] == [
        "Shopping is back as it was.",
        "Shopping removed (it is in the trash).",
        "Nothing to undo.",
    ]
    assert "/undo" in cli.HELP


# -- the page --------------------------------------------------------------------------------

UNDO_FETCH = """() => {
  window.calls = [];
  window.fetch = async (url, init) => {
    window.calls.push([url, (init || {}).method]);
    return new Response(JSON.stringify({undone: "Shopping is back as it was"}),
                        {headers: {"content-type": "application/json"}});
  };
}"""


async def test_the_page_shows_a_chip_under_the_answer_and_undoes_it(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            path = "C:/Users/Paul/Documents/Starlight/Notes/Shopping.md"
            for chip in (
                {"id": "t1", "title": "Shopping", "path": path, "new": False, "undoable": True},
                {"id": "t2", "title": "<b>Ideas</b>", "path": "x", "new": True, "undoable": False},
            ):
                await page.evaluate(SEND, {"type": "changed", **chip})
            await page.evaluate(SEND, {"type": "token", "text": "Added eggs."})
            await page.evaluate(SEND, {"type": "answer", "text": "Added eggs.", "stats": {}})
            chips = page.locator(".msg.bot .changerow .change")
            assert await chips.count() == 2
            first, second = chips.nth(0), chips.nth(1)
            assert await first.get_attribute("title") == path  # where it is, on hover
            assert (await first.text_content() or "").startswith("Changed:Shopping")
            assert await second.locator("b").text_content() == "<b>Ideas</b>"  # text, not HTML
            assert await second.locator("button").count() == 0  # not undoable: no button
            body = page.locator(".msg.bot .body").last
            assert await body.text_content() == "Added eggs."  # the chips sit under the answer

            await page.evaluate(UNDO_FETCH)
            await first.locator("button").click()
            await page.wait_for_function(
                "() => document.querySelector('.change').classList.contains('undone')"
            )
            assert await page.evaluate("() => window.calls") == [["/api/changes/t1/undo", "POST"]]
            assert await first.text_content() == "Undone:Shopping"
            assert await first.locator("button").count() == 0
        finally:
            await browser.close()
