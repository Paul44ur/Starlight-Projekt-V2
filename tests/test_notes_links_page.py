"""Block C1 of the notes plan (docs/plan-notes-app.md) on the Notes page: embedded notes
(loops stopped), links to notes not written yet, backlink cards with the line that links,
previews, [[ suggestions while writing, and a rename that asks about the links."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import answer
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened

TITLE = "() => document.querySelector('h1.title, .title-input')"
SAVED = "() => document.getElementById('save-state')?.textContent === 'Saved'"

MORE = {
    "Notes/Week.md": "# Week\n\n![[Shopping#Food]]\n\nThen [[Missing one]].\n",
    "Notes/Loop A.md": "Loop A text.\n\n![[Loop B]]\n",
    "Notes/Loop B.md": "Loop B text.\n\n![[Loop A]]\n",
}


@pytest.fixture
def more(notebook: Path) -> Path:
    for relative, text in MORE.items():
        (notebook / relative).write_text(text, encoding="utf-8")
    return notebook


async def test_embeds_missing_links_and_writing_them(
    static_site: str, app: Any, more: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FWeek.md")
        await page.wait_for_selector(".embed")
        embed = page.locator(".embed")
        title = await embed.locator(".embed-title").text_content()
        assert title == "Shopping \u203a Food"  # (the page writes a single angle quote)
        assert "milk" in await embed.locator(".embed-body").text_content()
        assert "Other" not in await embed.locator(".embed-body").text_content()  # just that part
        missing = page.locator("a.note-link.missing")
        assert await missing.text_content() == "Missing one"
        await missing.click()
        await page.wait_for_selector(".missing-note")
        await page.locator(".missing-note button", has_text="Write it").click()
        await page.wait_for_selector(".title-input")
        assert await page.locator(".title-input").input_value() == "Missing one"
        assert (more / "Notes" / "Missing one.md").is_file()

        await opened(page, f"{static_site}/notes.html?path=Notes%2FLoop%20A.md")
        await page.wait_for_selector(".embed .embed-note")
        assert await page.locator(".embed .embed-title").text_content() == "Loop B"
        assert await page.locator(".embed-note").text_content() == "(shown above: Loop A)"
        assert await page.locator(".embed").count() == 1  # the loop stops
        assert page.errors == []  # type: ignore[attr-defined]


async def test_backlink_cards_and_previews(
    static_site: str, app: Any, more: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FShopping.md")
        await page.wait_for_selector("#backlinks .line")
        cards = await page.locator("#backlinks .back").evaluate_all(
            "bs => bs.map(b => [b.firstChild.textContent, b.querySelector('.line')?.textContent])"
        )
        assert ["Plan", "Back to [[Shopping#Food]]."] in cards
        assert ["Week", "![[Shopping#Food]]"] in cards
        await page.locator("#column a.note-link", has_text="Plan").hover()
        await page.wait_for_selector(".preview")
        assert await page.locator(".preview h4").text_content() == "Plan"
        assert "The word Settings stays." in await page.locator(".preview .md").text_content()
        await page.mouse.move(5, 5)
        await page.wait_for_selector(".preview", state="detached")
        assert page.errors == []  # type: ignore[attr-defined]


async def test_link_suggestions_and_a_rename_that_asks_about_links(
    static_site: str, app: Any, more: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FPlan.md")
        await page.locator("#edit-toggle").click()
        await page.wait_for_selector(".cm-content")
        await page.locator(".cm-content").click()
        await page.keyboard.press("Control+End")
        await page.keyboard.type("\nSee [[Panc")
        await page.wait_for_selector(".cm-tooltip-autocomplete")
        await page.wait_for_timeout(150)  # (Enter picks an item only after 75 ms)
        assert "Pancakes" in await page.locator(".cm-tooltip-autocomplete li").first.text_content()
        await page.keyboard.press("Enter")
        await page.keyboard.type(" too.")
        await page.wait_for_function(SAVED)
        assert (more / "Notes" / "Plan.md").read_text("utf-8").endswith("\nSee [[Pancakes]] too.")
        await page.keyboard.type("\n[[einka")  # an alias
        await page.wait_for_selector(".cm-tooltip-autocomplete")
        await page.wait_for_timeout(150)  # (Enter picks an item only after 75 ms)
        assert "Einkauf" in await page.locator(".cm-tooltip-autocomplete li").first.text_content()
        await page.keyboard.press("Escape")
        await page.keyboard.press("Control+e")
        await page.wait_for_selector("#column .note-body")

        await opened(page, f"{static_site}/notes.html?path=Notes%2FShopping.md")
        await page.locator("#edit-toggle").click()
        await page.wait_for_selector(".title-input")
        await page.locator(".title-input").fill("Groceries")
        await page.keyboard.press("Enter")
        box = page.locator("dialog.ask")
        await box.wait_for()
        assert await box.locator("button").all_inner_texts() == [
            "Leave the links",
            "Change the links",
        ]
        assert "Also change the links to this note in other notes?" in await answer(page)
        await page.wait_for_function("() => location.search.includes('Groceries')")
        assert "[[Groceries#Food]]" in (more / "Notes" / "Plan.md").read_text("utf-8")
        assert "![[Groceries#Food]]" in (more / "Notes" / "Week.md").read_text("utf-8")
        assert page.errors == []  # type: ignore[attr-defined]
