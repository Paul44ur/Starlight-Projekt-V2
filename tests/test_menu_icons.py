"""The chat's menus and list use the drawn icons (`ICONS` in common.js), not emoji."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.test_chat_app_look import LIST, NARROWED, STATUS, open_app
from tests.test_chat_page import static_site  # noqa: F401  (a fixture)

STATIC = Path(__file__).resolve().parent.parent / "starlight" / "web" / "static"
PICTOGRAPH = re.compile("[\U0001f300-\U0001faff☀-➿⤳←-⇿]")
EMOJI_JS = "/[\\u{1F300}-\\u{1FAFF}\\u2600-\\u27BF\\u2933\\u2190-\\u21FF]/u"

# A list with a private conversation and one in a folder (the other tests keep their LIST).
WIDER = {
    "current": "c1",
    "conversations": [
        *LIST["conversations"],
        {"id": "c4", "title": "Secret plans", "updated": "2026-11-01T10:00:00", "folder": "",
         "private": True},
        {"id": "c5", "title": "Taxes", "updated": "2026-10-30T10:00:00", "folder": "Home"},
    ],
}  # fmt: skip


def icon_names() -> set[str]:
    source = (STATIC / "common.js").read_text(encoding="utf-8")
    block = source.split("const ICONS = {", 1)[1].split("\n};", 1)[0]
    return set(re.findall(r"^  (\w+): ", block, re.M))


def test_every_icon_name_the_chat_page_uses_is_drawn() -> None:
    page = (STATIC / "chat.html").read_text(encoding="utf-8")
    used = set(re.findall(r'data-icon="(\w+)"', page))
    used |= set(re.findall(r'setIcon\(\w+, "(\w+)"\)', page))
    used |= set(re.findall(r'icon\("(\w+)"\)', page))
    menu = page.split("function conversationMenu", 1)[1].split("// A small menu", 1)[0]
    used |= set(re.findall(r'\n\s*\["(\w+)", ', menu))  # a menu item's icon
    used |= set(re.findall(r'\["/\w+", "(\w+)"', page))  # a / command's icon
    used |= set(
        re.findall(r'setIcon\(toggle, showArchived \? "(\w+)" : "(\w+)"', page)[0]
    )  # the archive button's
    used |= set(re.findall(r'labelled\("(\w+)"', page))
    missing = {name for name in used if name not in icon_names()}
    assert not missing, missing
    assert {"edit", "pin", "folder", "archive", "forget", "print", "back", "trash"} <= used


async def test_the_conversation_menu_and_the_list_show_drawn_icons(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site)
        try:
            await page.evaluate(NARROWED, [WIDER, STATUS])
            await page.evaluate("() => loadList()")
            await page.wait_for_function("() => document.querySelectorAll('.conv').length === 5")
            # the menu: every row has a drawn icon and no emoji
            row = page.locator(".conv", has_text="Recipe for Sunday")
            await row.hover()
            await row.locator(".acts button").click()
            rows = page.locator(".popmenu button")
            assert await rows.count() == 9
            for i in range(9):
                assert await rows.nth(i).locator("svg.ico").count() == 1, i
                assert not await rows.nth(i).evaluate(f"(b) => {EMOJI_JS}.test(b.textContent)"), i
            await page.keyboard.press("Escape")
            # the list: a folder label, a private mark in its own element, the archive button
            folder = page.locator("#list .folder", has_text="Home")
            assert await folder.locator("svg.ico").count() == 1
            assert not PICTOGRAPH.search(await folder.inner_text())
            secret = page.locator(".conv", has_text="Secret plans")
            assert await secret.locator(":scope > svg.ico").count() == 1
            assert await secret.locator(".t").inner_text() == "Secret plans"
            assert await page.locator(".conv:not(:has(> svg.ico))").count() >= 3
            toggle = page.locator("#archived")
            assert await toggle.inner_text() == "Archived conversations"
            assert await toggle.locator("svg.ico").count() == 1
            await toggle.click()
            await page.wait_for_function(
                "() => document.querySelector('#archived').textContent"
                ".includes('Back to your conversations')"
            )
            assert await page.locator("#archived svg.ico").count() == 1
            assert await page.locator("#list .folder", has_text="Archived").count() == 1
            assert not PICTOGRAPH.search(await page.locator("#list").inner_text())
        finally:
            await browser.close()


async def test_the_bar_marks_a_private_conversation_with_a_lock_of_its_own(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site)
        try:
            lock = page.locator("#title-lock")
            assert not await lock.is_visible()
            await page.evaluate("() => showTitle({id: 'c1', title: 'Secret plans', private: true})")
            assert await lock.is_visible() and await lock.locator("svg.ico").count() == 1
            assert await page.locator("#title-text").inner_text() == "Secret plans"
        finally:
            await browser.close()


async def test_the_slash_menu_still_lists_its_commands_with_drawn_icons(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site)
        try:
            await page.fill("#input", "/")
            rows = page.locator(".popmenu button")
            assert await rows.count() == 12
            text = await page.locator(".popmenu").inner_text()
            for word in ("New conversation", "Practice mode on or off", "A little starfall"):
                assert word in text, word
            # the commands with a drawn icon have it; the ones without (◌ ✦ ?) stay text
            assert await page.locator(".popmenu button svg.ico").count() == 9
            assert await rows.nth(2).locator("svg.ico").count() == 0
            assert await rows.nth(2).locator("span").first.inner_text() == "◌"
        finally:
            await browser.close()


async def test_the_archive_button_is_german_in_german(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(
            playwright, static_site, "localStorage.setItem('starlight-lang', 'de');\n"
        )
        try:
            toggle = page.locator("#archived")
            assert await toggle.inner_text() == "Archivierte Gespräche"
            await page.evaluate(NARROWED, [LIST, STATUS])
            await toggle.click()
            await page.wait_for_function(
                "() => document.querySelector('#archived').textContent.includes('Zurück')"
            )
            assert await page.locator("#archived").inner_text() == "Zurück zu deinen Gesprächen"
            assert "Archivierte Gespräche" in await page.locator("#list .folder").inner_text()
        finally:
            await browser.close()
