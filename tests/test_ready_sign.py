"""A sign when an answer is ready: the title gets a dot, Starlight's own window lights its
taskbar button, a browser shows a notification (when allowed); the switch is in Settings."""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_chat_app_look import open_app
from tests.test_chat_page import SEND, SETTINGS_API, static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import browser_page

BACKGROUND = "document.hasFocus = () => false;\n"
OWN_WINDOW = (
    "window.attn = [];"
    " window.pywebview = {api: {attention: t => { window.attn.push(t); return true; }}};\n"
)
NOTIFICATIONS = """
window.notes = []; window.asked = 0;
window.Notification = class {
  constructor(title, options) { this.title = title; this.options = options; this.closed = false;
                                window.notes.push(this); }
  close() { this.closed = true; }
  static requestPermission() { window.asked++; Notification.permission = "granted";
                               return Promise.resolve("granted"); }
};
Notification.permission = %s;
"""
GRANTED = NOTIFICATIONS % '"granted"'
ANSWER = {"type": "answer", "text": "Secret words", "conversation": "c1"}
START = {"type": "start", "conversation": "c1"}
TITLE = "() => document.title"


async def answered(page: Any) -> None:
    await page.evaluate(SEND, START)
    await page.evaluate(SEND, ANSWER)


async def test_in_the_background_the_title_gets_a_dot_and_the_window_is_told(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, BACKGROUND + OWN_WINDOW)
        try:
            plain = await page.evaluate(TITLE)
            assert not plain.startswith("●")
            await answered(page)
            assert await page.evaluate(TITLE) == "● " + plain
            assert await page.evaluate("window.attn") == ["Your answer is ready"]
            await answered(page)  # a second one: no second dot
            assert await page.evaluate(TITLE) == "● " + plain
            assert len(await page.evaluate("window.attn")) == 2
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            assert await page.evaluate(TITLE) == plain
        finally:
            await browser.close()


async def test_a_browser_shows_a_notification_without_the_answers_words(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    show = "() => window.notes.map(n => [n.title, n.options])"
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, BACKGROUND + GRANTED)
        try:
            await answered(page)
            assert await page.evaluate(show) == [
                [
                    "Starlight",
                    {
                        "body": "Your answer is ready",
                        "icon": "/icons/icon-192.png",
                        "tag": "starlight-ready",
                    },
                ]
            ]
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            assert await page.evaluate("window.notes[0].closed")
        finally:
            await browser.close()
        german = "localStorage.setItem('starlight-lang', 'de');\n"
        browser, page = await open_app(playwright, static_site, german + BACKGROUND + GRANTED)
        try:
            await answered(page)
            assert await page.evaluate("window.notes[0].options.body") == "Deine Antwort ist fertig"
        finally:
            await browser.close()


async def test_nothing_happens_in_front_or_when_it_is_switched_off(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    front = "document.hasFocus = () => true;\n"
    off = "localStorage.setItem('starlight-ready-sign', 'off');\n"
    async with async_api.async_playwright() as playwright:
        for script in (front, off + BACKGROUND):
            browser, page = await open_app(playwright, static_site, script + OWN_WINDOW + GRANTED)
            try:
                plain = await page.evaluate(TITLE)
                await answered(page)
                await page.evaluate(SEND, {"type": "confirm", "question": "Run it?", "id": "k"})
                assert await page.evaluate(TITLE) == plain
                assert await page.evaluate("[window.attn.length, window.notes.length]") == [0, 0]
            finally:
                await browser.close()


async def test_a_question_in_the_background_says_it_waits_for_your_ok(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, BACKGROUND + OWN_WINDOW)
        try:
            await page.evaluate(SEND, {"type": "confirm", "question": "Run files.save?", "id": "k"})
            await page.evaluate(SEND, {"type": "prompt", "question": "Which one?", "id": "p"})
            assert await page.evaluate("window.attn") == ["Starlight is waiting for your OK"] * 2
            assert (await page.evaluate(TITLE)).startswith("● ")
        finally:
            await browser.close()


async def test_the_settings_switch_is_kept_on_this_device_and_asks_for_notifications(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    key = "() => localStorage.getItem('starlight-ready-sign')"
    async with async_api.async_playwright() as playwright:
        script = SETTINGS_API + NOTIFICATIONS % '"default"'
        browser, page = await browser_page(playwright, static_site, "settings.html", script)
        try:
            card = page.locator("#ready-card")
            assert "local-only" not in (await card.get_attribute("class") or "")
            switch = page.locator("#ready-sign")
            assert await switch.is_checked() and await page.evaluate(key) is None
            await card.locator(".switch").click()
            assert not await switch.is_checked() and await page.evaluate(key) == "off"
            await card.locator(".switch").click()
            assert await switch.is_checked() and await page.evaluate(key) is None
            allow = page.locator("#ready-allow")
            assert await allow.inner_text() == "Allow notifications"
            await allow.click()
            await page.wait_for_function("window.asked === 1")
            note = page.locator("#ready-note")
            assert await note.inner_text() == "Notifications are allowed in this browser."
            assert not await allow.is_visible()
        finally:
            await browser.close()


async def test_settings_says_nothing_of_notifications_in_starlights_own_window(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for state in ('"default"', '"denied"'):
            script = SETTINGS_API + NOTIFICATIONS % state + "window.pywebview = {api: {}};\n"
            browser, page = await browser_page(playwright, static_site, "settings.html", script)
            try:
                assert await page.locator("#ready-sign").count() == 1
                assert not await page.locator("#ready-allow").is_visible()
                assert not await page.locator("#ready-note").is_visible()
            finally:
                await browser.close()
        script = SETTINGS_API + NOTIFICATIONS % '"denied"'
        browser, page = await browser_page(playwright, static_site, "settings.html", script)
        try:
            text = "Notifications are blocked in this browser's settings."
            assert await page.locator("#ready-note").inner_text() == text
        finally:
            await browser.close()
