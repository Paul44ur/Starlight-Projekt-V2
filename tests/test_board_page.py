"""Block F4 of the notes plan (docs/plan-notes-app.md): boards on the Notes page. Cards show as
text only (links only to http and https), and can be added, dragged, resized, connected
with arrows and removed; every change is saved with the board's stamp, so a board changed
elsewhere is never overwritten."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import answer
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened

SAVED = "() => document.querySelector('.board-state')?.textContent === 'Saved'"
BOARD = {
    "nodes": [
        {"id": "a1", "type": "text", "x": 0, "y": 0, "width": 240, "height": 100,
         "text": "**Ideas** <img src=x onerror='window.hacked=1'>"},
        {"id": "b2", "type": "text", "x": 400, "y": 0, "width": 240, "height": 100,
         "text": "Garden", "color": "4"},
        {"id": "n3", "type": "file", "x": 0, "y": 200, "width": 300, "height": 200,
         "file": "Notes/Shopping.md"},
        {"id": "l4", "type": "link", "x": 400, "y": 200, "width": 240, "height": 80,
         "url": "javascript:window.hacked=2"},
        {"id": "l5", "type": "link", "x": 400, "y": 320, "width": 240, "height": 80,
         "url": "https://example.org/roses"},
    ],
    "edges": [],
    "kept": {"by": "another program"},
}  # fmt: skip


def board(notebook: Path) -> Path:
    file = notebook / "Boards" / "Plan.canvas"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(BOARD), encoding="utf-8")
    return file


def saved(file: Path) -> dict[str, Any]:
    return json.loads(file.read_text("utf-8"))  # type: ignore[no-any-return]


async def drag(page: Any, start: tuple[float, float], end: tuple[float, float]) -> None:
    await page.mouse.move(*start)
    await page.mouse.down()
    await page.mouse.move(*end, steps=6)
    await page.mouse.up()


async def centre(page: Any, selector: str) -> tuple[float, float]:
    box = await page.locator(selector).bounding_box()
    assert box is not None
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


async def test_cards_are_text_and_links_are_web_links_only(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    board(notebook)
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?board=Boards%2FPlan.canvas")
        await page.wait_for_selector(".board-card")
        assert await page.locator(".board-card").count() == 5
        ideas = page.locator('.board-card[data-id="a1"]')
        assert await ideas.locator("strong").text_content() == "Ideas"
        assert await ideas.locator("img").count() == 0  # (shown as text)
        note = page.locator('.board-card[data-id="n3"]')
        assert await note.locator(".board-file").text_content() == "Shopping"
        await note.locator(".board-preview li").first.wait_for()
        assert "milk" in await note.locator(".board-preview").text_content()
        assert await page.locator('.board-card[data-id="l4"] a').count() == 0
        roses = page.locator('.board-card[data-id="l5"] a.board-link')
        assert await roses.get_attribute("href") == "https://example.org/roses"
        assert await roses.get_attribute("rel") == "noopener noreferrer"
        colour = await page.locator('.board-card[data-id="b2"]').evaluate(
            "c => c.style.getPropertyValue('--card')"
        )
        assert colour == "#30a46c"  # (colour 4: green)
        assert await page.evaluate("() => window.hacked") is None
        await note.locator(".board-file").click()  # a note card opens its note
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Shopping'"
        )
        assert page.errors == []  # type: ignore[attr-defined]


async def test_add_drag_resize_connect_remove_and_reload(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    file = board(notebook)
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?board=Boards%2FPlan.canvas")
        await page.wait_for_selector(".board-card")

        x, y = await centre(page, '.board-card[data-id="a1"]')
        await drag(page, (x, y), (x + 97, y + 63))  # (snaps to the 20-point grid)
        await page.wait_for_function(SAVED)
        ideas = next(n for n in saved(file)["nodes"] if n["id"] == "a1")
        assert (ideas["x"], ideas["y"]) == (100, 60)
        assert saved(file)["kept"] == {"by": "another program"}  # (others' fields stay)

        corner = await centre(page, '.board-card[data-id="b2"] .board-resize')
        await drag(page, corner, (corner[0] + 60, corner[1] + 40))
        await page.wait_for_function(SAVED)
        garden = next(n for n in saved(file)["nodes"] if n["id"] == "b2")
        assert (garden["width"], garden["height"]) == (300, 140)

        await page.locator('.board-card[data-id="a1"]').hover()
        dot = await centre(page, '.board-card[data-id="a1"] .board-dot-right')
        await drag(page, dot, await centre(page, '.board-card[data-id="b2"]'))
        await page.wait_for_selector(".board-edge")
        await page.wait_for_function(SAVED)
        [edge] = saved(file)["edges"]
        assert (edge["fromNode"], edge["fromSide"], edge["toNode"], edge["toSide"]) == (
            "a1", "right", "b2", "left",
        )  # fmt: skip

        await page.locator(".board-bar .tool").first.click()  # + Text
        await page.locator(".board-edit").fill("Call Tom")
        box = await page.locator(".board-stage").bounding_box()
        assert box is not None
        await page.mouse.click(box["x"] + box["width"] - 30, box["y"] + box["height"] - 30)
        await page.wait_for_function(SAVED)
        assert "Call Tom" in [n.get("text") for n in saved(file)["nodes"]]

        await page.locator('.board-card[data-id="b2"] .board-body').click()
        await page.keyboard.press("Delete")
        await page.wait_for_function(SAVED)
        after = saved(file)
        assert "b2" not in [n["id"] for n in after["nodes"]] and after["edges"] == []

        await page.reload()
        await page.wait_for_selector(".board-card")
        texts = await page.locator(".board-card").all_text_contents()
        assert any("Call Tom" in t for t in texts) and len(texts) == 5
        assert page.errors == []  # type: ignore[attr-defined]


async def test_a_board_changed_elsewhere_is_not_overwritten(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    file = board(notebook)
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?board=Boards%2FPlan.canvas")
        await page.wait_for_selector(".board-card")
        elsewhere = {**BOARD, "nodes": BOARD["nodes"][:1]}
        file.write_text(json.dumps(elsewhere) + "\n ", encoding="utf-8")  # (another program)
        x, y = await centre(page, '.board-card[data-id="b2"]')
        await drag(page, (x, y), (x + 40, y + 40))
        await page.wait_for_selector("text=Not saved: changed elsewhere")
        assert len(saved(file)["nodes"]) == 1  # nothing was overwritten
        await page.locator(".board-reload").click()
        await page.wait_for_function("() => document.querySelectorAll('.board-card').length === 1")
        assert page.errors == []  # type: ignore[attr-defined]


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
async def test_the_boards_view_lists_and_makes_boards(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch, locale: str
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    board(notebook)
    async with notes_page(static_site, app, locale=locale) as page:
        await opened(page, f"{static_site}/notes.html")
        await page.locator('.quick button[data-view="boards"]').click()
        await page.wait_for_selector(".card-note")
        assert await page.locator(".card-note .t").all_text_contents() == ["Plan"]
        make = page.locator("#column button.ok")
        assert await make.text_content() == (
            "\uff0b Neues Board" if locale == "de-DE" else "\uff0b New board"
        )
        await make.click()
        asked = await answer(page, text="Holiday")
        assert asked == (
            "Wie soll das Board heißen?"
            if locale == "de-DE"
            else "What should the board be called?"
        )
        await page.wait_for_selector(".board-stage")
        assert (notebook / "Boards" / "Holiday.canvas").is_file()
        assert await page.locator("h1.title").text_content() == "Holiday"
        assert page.errors == []  # type: ignore[attr-defined]
