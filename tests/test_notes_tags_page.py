"""Block C2 of the notes plan (docs/plan-notes-app.md) on the Notes page: the tags in the
sidebar (nested, with counts) filter the list, the properties panel changes, adds and
removes properties (only their lines change), and the editor suggests tags and property
names."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened

TITLE_IS = "() => document.querySelector('h1.title')?.textContent === '{}'"


async def test_tags_in_the_sidebar_filter_the_notes(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    (notebook / "Notes" / "Work.md").write_text("Some #work/projects and #work\n", encoding="utf-8")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FPlan.md")
        await page.wait_for_selector("#tags-box .tag-item")
        tags = await page.locator("#tags-box .tag-item").evaluate_all(
            "bs => bs.map(b => [b.dataset.tag, b.querySelector('.name').textContent,"
            " b.querySelector('.count').textContent])"
        )
        assert ["errands", "#errands", "1"] in tags and ["home", "#home", "2"] in tags
        assert ["work/projects", "#projects", "2"] in tags  # nested (Work and the welcome note)
        assert [t[0] for t in tags].index("work") < [t[0] for t in tags].index("work/projects")
        await page.locator('#tags-box .tag-item[data-tag="home"]').click()
        await page.wait_for_selector(".card-note")
        assert await page.locator(".card-note .t").all_text_contents() == ["Old ideas", "Shopping"]
        assert page.errors == []  # type: ignore[attr-defined]


async def test_the_properties_panel_changes_only_those_lines(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    file = notebook / "Notes" / "Shopping.md"
    body = file.read_text("utf-8").split("---\n", 2)[2]
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FShopping.md")
        await page.wait_for_selector('#props input[aria-label="shop"]')
        await page.locator('#props input[aria-label="shop"]').fill("Aldi")
        await page.locator('#props input[aria-label="shop"]').press("Enter")
        await page.wait_for_function(
            "() => document.querySelector('.chips')?.textContent.includes('shop: Aldi')"
        )  # (saved, and the note drawn again)
        assert "shop: Aldi\n" in file.read_text("utf-8")
        assert "shop: Aldi" in await page.locator(".chips").text_content()

        await page.locator(".add-prop").click()
        await page.locator(".prop-form .prop-name").fill("done")
        await page.locator(".prop-form select").select_option("yesno")
        await page.locator(".prop-form input[type=checkbox]").check()
        await page.locator(".prop-form button", has_text="Add").click()
        await page.wait_for_selector('#props input[aria-label="done"]')
        assert "done: true\n" in file.read_text("utf-8")

        await page.locator('#props button[aria-label="Remove this property: shop"]').click()
        await page.wait_for_function(
            "() => !document.querySelector('#props input[aria-label=\"shop\"]')"
        )
        text = file.read_text("utf-8")
        assert "shop" not in text and "aliases: [Einkauf]\ntags: [home]\n" in text
        assert text.split("---\n", 2)[2] == body  # the note itself did not change

        file.write_text(file.read_text("utf-8") + "Changed elsewhere.\n", encoding="utf-8")
        await page.locator('#props input[aria-label="done"]').uncheck()  # an old stamp now
        await page.wait_for_selector(".toast")
        assert "changed meanwhile" in await page.locator(".toast").first.text_content()
        assert "done: true\n" in file.read_text("utf-8")  # not overwritten
        assert page.errors == []  # type: ignore[attr-defined]


async def test_the_editor_suggests_tags_and_property_names(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FShopping.md")
        await page.locator("#edit-toggle").click()
        await page.wait_for_selector(".cm-content")
        await page.locator(".cm-content").click()
        await page.keyboard.press("Control+End")
        await page.keyboard.type(" #err")
        await page.wait_for_selector(".cm-tooltip-autocomplete")
        await page.wait_for_timeout(150)  # (Enter picks an item only after 75 ms)
        assert "errands" in await page.locator(".cm-tooltip-autocomplete li").first.text_content()
        await page.keyboard.press("Enter")
        assert (await page.locator(".cm-activeLine").text_content() or "").endswith("#errands")
        await page.keyboard.press("Control+Home")
        await page.keyboard.press("ArrowDown")  # into the properties block
        await page.keyboard.press("End")
        await page.keyboard.type("\nsh")
        await page.wait_for_selector(".cm-tooltip-autocomplete")
        await page.wait_for_timeout(150)  # (Enter picks an item only after 75 ms)
        assert "shop" in await page.locator(".cm-tooltip-autocomplete li").first.text_content()
        await page.keyboard.press("Enter")
        assert await page.locator(".cm-activeLine").text_content() == "shop: "
        assert page.errors == []  # type: ignore[attr-defined]
