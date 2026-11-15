"""Text size and density (plan UI 3, block F3) in a real browser: the "Display" section of the
Settings page (after Language, in English and German), the choice kept per device in the browser
only, followed at once by every page (also by another open one), set before the first paint, a
mouse gets tighter rows when Compact and a touch screen never goes below 44 px, nothing breaks
when the browser's storage throws, a paired phone can use it, and nothing goes to the server."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_app_look import APP, open_app
from tests.test_chat_page import CHROMIUM, SEND
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_settings_paper import open_page, stub

ANSWER = {
    "type": "answer",
    "text": "Done.",
    "conversation": "c1",
    "stats": {"seconds": 1.0, "tokens": 5, "context_tokens": 100},
}
BODY = "() => getComputedStyle(document.body).fontSize"
# the user's own message: its text size comes from the tokens (the answer's is 17px in chat.css)
BOT = "() => getComputedStyle(document.querySelector('.msg.you .body')).fontSize"
ANSWERED = "() => getComputedStyle(document.querySelector('.msg.bot .body')).fontSize"
ROW = "() => document.querySelector('.conv').getBoundingClientRect().height"
DATASET = (
    "() => [document.documentElement.dataset.textSize, document.documentElement.dataset.density]"
)
THROWING = (
    "Storage.prototype.getItem = () => { throw new Error('blocked'); };"
    "Storage.prototype.setItem = () => { throw new Error('blocked'); };"
)


async def talk(chat: Any) -> None:
    """One message of the user and one answer in the open chat."""
    await chat.locator("#input").fill("Hello")
    await chat.locator("#input").press("Enter")
    await chat.wait_for_selector(".msg.you")
    await chat.evaluate(SEND, {"type": "start", "conversation": "c1"})
    await chat.evaluate(SEND, ANSWER)
    await chat.wait_for_selector(".msg.bot")


async def two_pages(playwright: Any, site: str) -> tuple[Any, Any, Any]:
    """One browser context (so one stored choice) with the Settings page and the chat open."""
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    settings = await context.new_page()
    await settings.add_init_script(stub(True))
    await settings.goto(f"{site}/settings.html")
    await settings.wait_for_selector("#safety .srow")
    chat = await context.new_page()
    await chat.add_init_script(APP)
    await chat.goto(f"{site}/chat.html")
    await chat.wait_for_selector(".conv")
    await talk(chat)
    return browser, settings, chat


async def choose(page: Any, group: str, value: str) -> None:
    await page.locator(f"#{group} button[data-value={value}]").click()


async def test_the_section_follows_language_in_english_and_german(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_page(playwright, static_site)
        try:
            heads = await page.locator(".card:visible h2").all_inner_texts()
            assert heads.index("Display") == heads.index("Language") + 1
            texts = await page.locator("#display-card").inner_text()
            for word in ("Text size", "Small", "Normal", "Large", "Lists", "Compact", "Roomy"):
                assert word in texts
            assert "Applies to this device." in texts
            await page.evaluate("localStorage.setItem('starlight-lang', 'de')")
            await page.reload()
            await page.wait_for_selector("#safety .srow")
            heads = await page.locator(".card:visible h2").all_inner_texts()
            assert heads.index("Darstellung") == heads.index("Sprache") + 1
            texts = await page.locator("#display-card").inner_text()
            for word in ("Textgröße", "Klein", "Normal", "Groß", "Listen", "Kompakt", "Luftig"):
                assert word in texts
            assert "Gilt für dieses Gerät." in texts
        finally:
            await browser.close()


async def test_every_page_follows_the_size_and_normal_is_the_old_size(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page, chat = await two_pages(playwright, static_site)
        try:
            assert await page.evaluate(DATASET) == ["normal", "roomy"]
            assert (
                await page.locator("#text-size button[aria-pressed=true]").inner_text() == "Normal"
            )
            assert await page.locator("#density button[aria-pressed=true]").inner_text() == "Roomy"
            assert await page.evaluate(BODY) == "15px"
            for value, body, bot, said in (
                ("large", "17px", "17px", "19.55px"),
                ("small", "14px", "14px", "15.3px"),
                ("normal", "15px", "15px", "17px"),
            ):
                await choose(page, "text-size", value)
                assert await page.evaluate(BODY) == body  # at once, on this page
                pressed = page.locator("#text-size button[aria-pressed=true]")
                assert await pressed.get_attribute("data-value") == value
                await chat.reload()  # a page opened after choosing
                await chat.wait_for_selector(".conv")
                await talk(chat)
                assert await chat.evaluate(BODY) == body
                assert await chat.evaluate(BOT) == bot  # the chat message text too
                assert await chat.evaluate(ANSWERED) == said  # and the answers (the serif)
        finally:
            await browser.close()


async def test_compact_rows_are_tighter_with_a_mouse_and_never_below_44_px_on_touch(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    store = "try { localStorage.setItem('starlight-density', 'compact'); } catch (e) {}"
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(
            playwright, static_site, "", viewport={"width": 1280, "height": 800}
        )
        try:
            roomy = await page.evaluate(ROW)
            await page.evaluate(store)
            await page.reload()
            await page.wait_for_selector(".conv")
            compact = await page.evaluate(ROW)
            assert compact < roomy == 36 and compact == 32
        finally:
            await browser.close()
        browser, page = await open_app(
            playwright,
            static_site,
            store,
            viewport={"width": 390, "height": 800},
            has_touch=True,
            is_mobile=True,
        )
        try:
            assert await page.evaluate("matchMedia('(pointer: coarse)').matches")
            assert await page.evaluate(DATASET) == ["normal", "compact"]
            assert await page.evaluate(ROW) >= 44
        finally:
            await browser.close()


async def test_the_choice_survives_a_reload_and_is_set_before_the_first_paint(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_page(playwright, static_site)
        try:
            await choose(page, "text-size", "large")
            await choose(page, "density", "compact")
            assert await page.evaluate(
                "['starlight-text-size', 'starlight-density'].map(k => localStorage.getItem(k))"
            ) == ["large", "compact"]
            # the page's own early look: read as soon as the document is parsed, and never changed
            await page.add_init_script(
                "window.__early = []; window.__changes = 0;"
                "document.addEventListener('DOMContentLoaded', () => { window.__early = ["
                "document.documentElement.dataset.textSize,"
                "document.documentElement.dataset.density,"
                "getComputedStyle(document.body).fontSize]; });"
                "new MutationObserver(() => { window.__changes++; }).observe("
                "document.documentElement, {attributes: true, attributeFilter:"
                " ['data-text-size', 'data-density']});"
            )
            await page.reload()
            await page.wait_for_selector("#safety .srow")
            assert await page.evaluate("window.__early") == ["large", "compact", "17px"]
            assert await page.evaluate("window.__changes") <= 2  # set once, not corrected later
            pressed = page.locator("#text-size button[aria-pressed=true]")
            assert await pressed.get_attribute("data-value") == "large"
        finally:
            await browser.close()


async def test_another_open_page_picks_it_up_without_a_reload(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page, chat = await two_pages(playwright, static_site)
        try:
            assert await chat.evaluate(BOT) == "15px"
            await choose(page, "density", "compact")
            await chat.wait_for_function(
                "() => document.querySelector('.conv').offsetHeight === 32"
            )
            await choose(page, "text-size", "large")
            await chat.wait_for_function(
                "() => getComputedStyle(document.body).fontSize === '17px'"
            )
            assert await chat.evaluate(BOT) == "17px"
        finally:
            await browser.close()


async def test_pages_still_load_at_normal_and_roomy_when_storage_throws(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, THROWING)
        try:
            assert await page.evaluate(DATASET) == ["normal", "roomy"]
            assert await page.evaluate(BODY) == "15px"
            assert await page.evaluate(ROW) == 36
        finally:
            await browser.close()
        browser, page = await open_page(playwright, static_site)
        try:
            await page.add_init_script(THROWING)
            await page.reload()
            await page.wait_for_selector("#safety .srow")
            assert await page.evaluate(DATASET) == ["normal", "roomy"]
            await choose(page, "text-size", "large")  # cannot be kept, but shows on this page
            assert await page.evaluate(BODY) == "17px"
        finally:
            await browser.close()


async def test_a_paired_phone_can_use_it_while_pc_only_controls_stay_locked(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_page(playwright, static_site, width=360, local=False)
        try:
            await page.wait_for_function("() => document.body.classList.contains('remote')")
            assert await page.locator(".locked").is_visible()
            events = await page.locator("#pair").evaluate("n => getComputedStyle(n).pointerEvents")
            assert events == "none"  # a PC-only control stays locked
            assert await page.locator("#display-card").is_visible()
            for button in await page.locator("#display-card button").all():
                assert await button.evaluate("n => getComputedStyle(n).pointerEvents") != "none"
                assert await button.is_enabled()
            await choose(page, "text-size", "large")
            await choose(page, "density", "compact")
            assert await page.evaluate(DATASET) == ["large", "compact"]
            assert await page.evaluate("document.documentElement.scrollWidth") <= 360
        finally:
            await browser.close()


async def test_choosing_sends_nothing_to_the_server(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_page(playwright, static_site)
        try:
            await page.evaluate(
                "() => { window.__calls = []; const real = window.fetch;"
                " window.fetch = (...a) => { window.__calls.push(String(a[0]));"
                " return real(...a); };"
                " const was = XMLHttpRequest.prototype.open;"
                " XMLHttpRequest.prototype.open = function (...a) {"
                " window.__calls.push(String(a[1])); return was.apply(this, a); }; }"
            )
            for group, value in (
                ("text-size", "small"),
                ("text-size", "large"),
                ("density", "compact"),
            ):
                await choose(page, group, value)
            assert await page.evaluate("window.__calls") == []
            assert await page.evaluate("window.posted") == []
        finally:
            await browser.close()
