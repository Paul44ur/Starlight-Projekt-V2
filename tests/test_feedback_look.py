"""Small feedback and keyboard details (UI block U8), in a real browser: one toast at a time,
above the message box; faint bars while a list loads; a window of keyboard shortcuts (Ctrl+/,
or ? when not typing); and the focus going where it should in menus, lists and the drawer."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import CHROMIUM, static_site  # noqa: F401  (a fixture)
from tests.test_settings_look import DATA as SETTINGS_DATA

CONVERSATIONS = {
    "current": "c1",
    "conversations": [
        {"id": "c1", "title": "First", "updated": "2026-11-03T10:00:00", "folder": ""},
        {"id": "c2", "title": "Second", "updated": "2026-11-02T10:00:00", "folder": ""},
    ],
}
CHAT = (
    "window.sockets = []; window.asked = [];\n"
    "window.WebSocket = class { constructor() { this.readyState = 1; window.sockets.push(this);"
    " setTimeout(() => this.onopen && this.onopen(), 20); } send() {} close() {} };\n"
    "window.WebSocket.OPEN = 1;\n"
    f"const LIST = {json.dumps(CONVERSATIONS)};\n"
    "window.fetch = async (url) => { const u = String(url); window.asked.push(u);"
    " const body = u.includes('/api/conversations/') ? {private: false, messages: []}"
    " : u.includes('/api/conversations') ? LIST : {local: true, kill: {engaged: false},"
    " access: {}};"
    " return new Response(JSON.stringify(body),"
    " {headers: {'content-type': 'application/json'}}); };"
)


async def browser_page(
    playwright: Any, site: str, url: str, script: str, **context: Any
) -> tuple[Any, Any]:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    page = await browser.new_page(**({"viewport": {"width": 1200, "height": 800}} | context))
    await page.add_init_script(script)
    await page.goto(f"{site}/{url}")
    return browser, page


async def test_one_toast_at_a_time_above_the_message_box_and_long_ones_stay_longer(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await browser_page(playwright, static_site, "chat.html", CHAT)
        try:
            await page.wait_for_selector(".conv")
            await page.evaluate("() => { toast('One'); toast('Two'); }")
            assert await page.locator(".toast").all_text_contents() == ["Two"]  # not stacked
            above = await page.evaluate(
                "() => document.querySelector('.toast').getBoundingClientRect().bottom"
                " <= document.querySelector('footer').getBoundingClientRect().top"
            )
            assert above  # (it used to cover the message box)
            await page.evaluate("() => toast('Short')")
            await page.wait_for_timeout(3000)
            assert await page.locator(".toast").count() == 0
            await page.evaluate("t => toast(t)", "A much longer message. " * 6)
            await page.wait_for_timeout(3000)
            assert await page.locator(".toast").count() == 1  # still there to be read
        finally:
            await browser.close()


async def test_a_list_shows_faint_bars_while_it_loads(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    slow = (
        f"const DATA = {json.dumps(SETTINGS_DATA)};\n"
        "window.release = null; const later = new Promise(r => window.release = r);\n"
        "window.fetch = async (url) => { await later;"
        " const u = new URL(String(url), location.href).pathname;"
        " return new Response(JSON.stringify(DATA[u] || {}),"
        " {headers: {'content-type': 'application/json'}}); };"
    )
    async with async_api.async_playwright() as playwright:
        browser, page = await browser_page(playwright, static_site, "settings.html", slow)
        try:
            assert await page.locator("#mods .skeleton").count() == 1  # while it loads
            await page.evaluate("() => window.release()")
            await page.wait_for_selector("details.mod")
            assert await page.locator(".skeleton").count() == 0  # the list took its place
        finally:
            await browser.close()


async def test_the_keyboard_shortcuts_window(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await browser_page(playwright, static_site, "chat.html", CHAT)
        try:
            await page.wait_for_selector(".conv")
            box, window = page.locator("#input"), page.locator("dialog#shortcuts")
            await box.focus()
            await page.keyboard.type("?")  # in the message box, ? is a question mark
            assert await box.input_value() == "?"
            assert await window.count() == 0
            await page.keyboard.press("Control+/")  # Ctrl+/ opens it, also while typing
            assert await window.evaluate("d => d.open")
            keys = [
                k.replace("\n", "").replace(" ", "")
                for k in await window.locator("dt").all_inner_texts()
            ]
            assert "Shift+Enter" in keys and "Ctrl+/" in keys
            await page.keyboard.press("Escape")
            assert not await window.evaluate("d => d.open")
            await page.evaluate("() => document.activeElement.blur()")
            await page.keyboard.press("?")  # ? alone, when no box has the focus
            assert await window.evaluate("d => d.open")
        finally:
            await browser.close()
        browser, page = await browser_page(
            playwright, static_site, "chat.html",
            "localStorage.setItem('starlight-lang', 'de');\n" + CHAT,
        )  # fmt: skip
        try:
            await page.wait_for_selector(".conv")
            await page.locator("#input").focus()
            await page.keyboard.press("Control+/")
            await page.wait_for_function(
                "() => document.querySelector('#shortcuts h2').textContent === 'Tastenkürzel'"
            )
            assert "Strg" in await page.locator("dialog#shortcuts").inner_text()
        finally:
            await browser.close()


async def test_the_focus_goes_where_it_should(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    active = "() => document.activeElement"
    async with async_api.async_playwright() as playwright:
        browser, page = await browser_page(playwright, static_site, "chat.html", CHAT)
        try:
            await page.wait_for_selector(".conv")
            # a conversation is reached by Tab and opened with Enter
            second = page.locator(".conv", has_text="Second")
            assert await second.get_attribute("tabindex") == "0"
            await second.focus()
            await page.keyboard.press("Enter")
            await page.wait_for_function("() => window.asked.some(u => u.endsWith('/c2'))")
            await page.wait_for_selector(".conv.active >> text=Second")
            # (the list is drawn anew when a conversation opens: the focus stays on its row)
            assert await second.evaluate(f"(r) => r === ({active})()")
            # a row's ⋯ shows when Tab reaches the row; its menu takes the focus, ↓ goes on,
            # and Esc goes back to ⋯
            first = page.locator(".conv", has_text="First")
            await first.focus()
            await page.keyboard.press("Tab")
            more = first.locator(".acts button")
            assert await more.evaluate(f"(b) => b === ({active})()")
            await page.keyboard.press("Enter")
            items = page.locator(".popmenu [role=menuitem]")
            assert await items.first.evaluate(f"(b) => b === ({active})()")
            await page.keyboard.press("ArrowDown")
            assert await items.nth(1).evaluate(f"(b) => b === ({active})()")
            await page.keyboard.press("Escape")
            assert await page.locator(".popmenu").count() == 0
            assert await more.evaluate(f"(b) => b === ({active})()")
        finally:
            await browser.close()
        # the phone's drawer: says whether it is open, takes the focus, Esc closes it
        browser, page = await browser_page(
            playwright, static_site, "chat.html", CHAT, viewport={"width": 390, "height": 800}
        )
        try:
            await page.wait_for_selector(".conv", state="attached")
            menu = page.locator("#menu")
            assert await menu.get_attribute("aria-expanded") == "false"
            await menu.click()
            assert await menu.get_attribute("aria-expanded") == "true"
            assert await page.evaluate("() => document.activeElement.id") == "convs"
            await page.keyboard.press("Escape")
            assert await menu.get_attribute("aria-expanded") == "false"
            assert await page.evaluate("() => document.activeElement.id") == "menu"
        finally:
            await browser.close()
