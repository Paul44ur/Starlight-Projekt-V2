"""The pages in German (the conversation itself untouched), the launcher window in German,
and every button and field on the pages having a name a screen reader can say."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests import test_chat_page
from tests.conftest import REPO
from tests.test_chat_page import FAKE_SERVER, SEND

static_site = test_chat_page.static_site  # (a fixture: a server for the page's files)
PAGES = ["chat.html", "settings.html", "activity.html", "automations.html", "memory.html",
         "kill.html", "pair.html", "today.html"]  # fmt: skip


async def browser(playwright: Any) -> Any:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        test_chat_page.CHROMIUM if Path(test_chat_page.CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    return await playwright.chromium.launch(executable_path=executable)


async def test_the_chat_page_in_german(static_site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        chromium = await browser(playwright)
        try:
            page = await chromium.new_page()
            await page.add_init_script(
                FAKE_SERVER + "localStorage.setItem('starlight-lang', 'de');"
            )
            await page.goto(f"{static_site}/chat.html")
            await page.evaluate("() => { if (!window.sockets.length) connect(); }")
            await page.wait_for_function("() => window.sockets.length > 0")
            assert await page.evaluate("() => document.documentElement.lang") == "de"
            assert (await page.locator("#send").text_content()).strip() == "Senden"
            assert await page.locator("#input").get_attribute("aria-label") == "Deine Nachricht"
            assert "Einstellungen" in await page.locator(".side-nav").text_content()
            # an answer that happens to be an interface word stays as the model wrote it
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            await page.evaluate(SEND, {"type": "answer", "text": "Send", "conversation": "c1"})
            assert (await page.locator(".msg.bot .body").last.text_content()).strip() == "Send"
            # what appears later is translated too
            await page.evaluate("() => toast('Copied')")
            assert await page.locator(".toast").last.text_content() == "Kopiert"
            wide = "document.querySelector('#waiting .wide')"
            await page.evaluate(f"() => {{ {wide}.textContent = '3 waiting for your OK'; }}")
            await page.wait_for_function(f"() => {wide}.textContent === '3 warten auf dein OK'")
        finally:
            await chromium.close()


async def test_english_stays_english(static_site: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        chromium = await browser(playwright)
        try:
            page = await chromium.new_page(locale="en-US")
            await page.add_init_script(FAKE_SERVER)
            await page.goto(f"{static_site}/chat.html")
            assert (await page.locator("#send").text_content()).strip() == "Send"
        finally:
            await chromium.close()


NAMELESS = """() => {
  const named = (e) => (e.getAttribute('aria-label') || e.getAttribute('title') || '').trim()
    || (e.labels && [...e.labels].some(l => l.textContent.trim()))
    || /[A-Za-z\\u00c0-\\u024f]{2}/.test(e.textContent || '');
  const fields = 'button, input:not([type=hidden]):not([hidden]), select, textarea';
  return [...document.querySelectorAll(fields)]
    .filter(e => !e.hidden && e.type !== 'file' && !named(e))
    .map(e => e.outerHTML.slice(0, 120));
}"""


async def test_every_button_and_field_has_a_name(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        chromium = await browser(playwright)
        try:
            page = await chromium.new_page()
            await page.add_init_script(FAKE_SERVER)
            nameless: dict[str, list[str]] = {}
            for name in PAGES:
                await page.goto(f"{static_site}/{name}")
                await page.wait_for_timeout(150)
                found = await page.evaluate(NAMELESS)
                if found:
                    nameless[name] = found
            assert nameless == {}
            await page.goto(f"{static_site}/chat.html")
            assert await page.locator("#log").get_attribute("role") == "log"
            assert await page.locator("a.skip").get_attribute("href") == "#input"
        finally:
            await chromium.close()


def test_the_launcher_window_in_german(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("tkinter")
    sys.path.insert(0, str(REPO / "launcher"))
    try:
        import launcher_gui as gui
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(gui, "LANGUAGE", "de")
    assert gui.tr("Open chat") == "Chat öffnen"
    assert gui.tr("What's new in Starlight V2 1.01.00") == "Neu in Starlight V2 1.01.00"
    assert gui.tr("something new") == "something new"  # not in the table: stays English
    monkeypatch.setattr(gui, "LANGUAGE", "en")
    assert gui.tr("Open chat") == "Open chat"
    monkeypatch.setenv("STARLIGHT_LANG", "de")
    assert gui.window_language() == "de"
    monkeypatch.setenv("STARLIGHT_LANG", "en")
    assert gui.window_language() == "en"
    # every step the launcher reports has its German line
    steps = (REPO / "launcher" / "starlight_launcher.py").read_text(encoding="utf-8")
    import re

    for step in re.findall(r'ui\.step\(\s*"([^"]+)"', steps):
        assert step in gui.GERMAN, step
