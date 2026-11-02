"""Block E4 of the notes plan (docs/plan-notes-app.md): ```query blocks on the Notes page become
live tables: they render, refresh, sort by a click on a column, and show mistakes kindly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import SEND, open_chat
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notebook_query import NOTES
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL

BOARD = """# Board

```query
# my open work
where: folder is Notes and status is not done
where: and (status is not empty or name is Board)
sort: rating desc
fields: status, rating, tag
```

```query
where: status is
```
"""


def board(notebook: Path) -> Path:
    for relative, text in NOTES.items():
        (notebook / relative).parent.mkdir(parents=True, exist_ok=True)
        (notebook / relative).write_text(text, encoding="utf-8")
    note = notebook / "Notes" / "Board.md"
    note.write_text(BOARD, encoding="utf-8")
    return note


def test_the_lines_of_a_query_block() -> None:
    from starlight.notebook.query import QueryError, parse_block

    assert parse_block("where: status is open\nsort: due\nfields: status, due\nlimit: 5") == {
        "where": "status is open", "sort": "due", "fields": "status, due", "limit": 5,
    }  # fmt: skip
    assert parse_block("# a comment\nstatus is open and\n  tag is work")["where"] == (
        "status is open and tag is work"
    )  # (lines without a key are the condition)
    german = parse_block("wo: status ist offen\nsortieren: due desc\nfelder: status\nanzahl: 9")
    assert german == {"where": "status ist offen", "sort": "due desc", "fields": "status",
                      "limit": 9}  # fmt: skip
    with pytest.raises(QueryError, match="status: is not a query line"):
        parse_block("status: open")
    with pytest.raises(QueryError, match="limit: is a number"):
        parse_block("limit: many")


def test_the_api_makes_the_table(app: Any, notebook: Path) -> None:
    board(notebook)
    text = "where: tag is work\nsort: name\nfields: status"
    table = app.post("/api/notes/query", json={"text": text}, headers=LOCAL).json()
    assert [(n["name"], n["status"]) for n in table["notes"]] == [
        ("Alpha", "open"),
        ("Beta", "done"),
    ]
    wrong = app.post("/api/notes/query", json={"text": "where: status is"}, headers=LOCAL)
    assert wrong.status_code == 400 and "expected a value after 'is'" in wrong.json()["detail"]


@pytest.mark.parametrize(("scheme", "locale"), [("light", "en-US"), ("dark", "de-DE")])
async def test_a_query_block_is_a_live_table(
    static_site: str,
    app: Any,
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    locale: str,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    board(notebook)
    german = locale.startswith("de")
    async with notes_page(static_site, app, locale=locale, scheme=scheme) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FBoard.md")
        await page.wait_for_selector(".query-table")
        table = page.locator(".query-table").first

        async def rows() -> list[list[str]]:
            return await table.locator("tbody tr").evaluate_all(  # type: ignore[no-any-return]
                "rs => rs.map(r => [...r.cells].map(c => c.textContent))"
            )

        heads = await table.locator("th").all_text_contents()
        assert heads == ["Notiz" if german else "Note", "status", "rating", "tag"]
        assert await rows() == [
            ["Gamma", "in progress", "4.5", "garden, home"],
            ["Alpha", "open", "4", "work"],
            ["Board", "", "", ""],  # (no rating: last)
        ]
        await table.locator("th button", has_text="status").click()
        assert [r[0] for r in await rows()][:2] == ["Gamma", "Alpha"]  # in progress < open
        assert await table.locator("th").nth(1).get_attribute("aria-sort") == "ascending"
        await table.locator("th button", has_text="status").click()
        assert [r[0] for r in await rows()] == ["Alpha", "Gamma", "Board"]  # (empty: last)
        assert "▼" in await table.locator("th").nth(1).text_content()

        error = page.locator(".query-error")
        await error.wait_for()
        expected = (
            "Diese Tabelle ließ sich nicht erstellen:"
            if german
            else "This table could not be made:"
        )
        assert (await error.text_content()).startswith(expected)
        assert "expected a value after 'is'" in await error.text_content()

        alpha = notebook / "Notes" / "Alpha.md"
        alpha.write_text(alpha.read_text("utf-8").replace("status: open", "status: done"), "utf-8")
        read = app.get("/api/note", params={"path": "Notes/Alpha.md"}, headers=LOCAL)
        assert read.status_code == 200  # (reading a note updates the index at once)
        await page.locator(".query-again").first.click()  # ↻: look again
        await page.wait_for_function(
            "() => ![...document.querySelectorAll('.query-table')[0].tBodies[0].rows]"
            ".some(r => r.cells[0].textContent === 'Alpha')"
        )
        assert (await page.locator(".query-foot").first.text_content()).startswith("2 ")
        link = table.locator("tbody a.note-link").first
        assert await link.get_attribute("href") == "/notes?open=Notes%2FGamma"
        assert page.errors == []  # type: ignore[attr-defined]


async def test_in_the_chat_a_query_block_stays_code(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            answer = "Here:\n\n```query\nwhere: status is open\n```"
            await page.evaluate(SEND, {"type": "answer", "text": answer, "conversation": "c1"})
            body = page.locator(".msg.bot .body").last
            await body.locator("pre").wait_for()
            assert await body.locator(".query-table").count() == 0
            assert "where: status is open" in await body.locator("pre").text_content()
        finally:
            await browser.close()
