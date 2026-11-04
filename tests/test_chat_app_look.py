"""The chat page as a desktop app (after the Claude app's layout, in Starlight's own look): a
sidebar with the actions, the pages and the conversations (Pinned, Recent) and the model at
its foot; the open conversation's name in the bar with its menu; a sidebar that can be hidden;
and a meter of how full the model's context is."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from starlight.web.app import answer_stats
from tests.test_chat_page import SEND, static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import browser_page
from tests.test_remote import LOCAL, client, home

LIST = {
    "current": "c1",
    "conversations": [
        {"id": "c1", "title": "Plan the week", "updated": "2026-11-04T10:00:00", "folder": "",
         "pinned": True},
        {"id": "c3", "title": "Server check", "updated": "2026-11-03T10:00:00", "folder": "",
         "pinned": True},
        {"id": "c2", "title": "Recipe for Sunday", "updated": "2026-11-02T10:00:00", "folder": ""},
    ],
}  # fmt: skip
STATUS = {
    "local": True, "kill": {"engaged": False}, "access": {}, "model": "lmstudio",
    "model_name": "qwen3-14b", "context_window": 32768,
}  # fmt: skip
NONE = "\u2013"  # (the meter before an answer: a dash)
APP = (
    "window.sockets = []; window.WebSocket = class { constructor() { this.readyState = 1;"
    " window.sockets.push(this); setTimeout(() => this.onopen && this.onopen(), 20); }"
    " send() {} close() {} }; window.WebSocket.OPEN = 1;\n"
    f"const LIST = {json.dumps(LIST)}, STATUS = {json.dumps(STATUS)};\n"
    "window.fetch = async (url) => { const u = String(url);"
    " const body = u.includes('/api/conversations/') ? {private: false, messages: []}"
    " : u.includes('/api/conversations') ? LIST : STATUS;"
    " const type = {'content-type': 'application/json'};"
    " return new Response(JSON.stringify(body), {headers: type}); };"
)


def test_an_answer_says_how_full_the_context_is(tmp_path: Path) -> None:
    usage = [
        {"prompt_tokens": 9000, "completion_tokens": 20, "duration_s": 1.0},
        {"prompt_tokens": 9400, "completion_tokens": 600, "duration_s": 2.0},
    ]
    assert answer_stats(usage)["context_tokens"] == 10000  # the last step: read + written
    assert "context_tokens" not in answer_stats([{"completion_tokens": 5}])  # (not reported)
    with client(home(tmp_path / "h", remote=False)) as c:
        status = c.get("/api/status", headers=LOCAL).json()
    assert status["model_name"] == "echo" and status["context_window"] == 32768


async def open_app(playwright: Any, site: str, extra: str = "", **context: Any) -> tuple[Any, Any]:
    browser, page = await browser_page(playwright, site, "chat.html", extra + APP, **context)
    await page.wait_for_selector(".conv")
    return browser, page


async def test_the_sidebar_holds_the_actions_the_pages_and_the_conversations(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site)
        try:
            side = page.locator("#convs")
            assert await page.locator("#new").inner_text() == "New conversation"
            assert await page.locator("#newprivate").inner_text() == "Private conversation"
            pages = side.locator(".side-nav a")
            assert await pages.all_inner_texts() == [
                "Notes",
                "Memory",
                "Activity",
                "Automations",
                "Settings",
            ]
            assert await side.locator(".side-nav a > svg.ico").count() == 5
            assert await page.locator("header.top a[href='/notes']").count() == 0  # (moved)
            labels = await page.locator("#list .folder, #list .conv .t").all_inner_texts()
            assert labels[:5] == ["Pinned", "Plan the week", "Server check", "Recent",
                                  "Recipe for Sunday"]  # fmt: skip
            await page.wait_for_function(
                "() => document.getElementById('model-name').textContent === 'qwen3-14b'"
            )
            # the open conversation's name in the bar; its menu from there
            title = page.locator("#title")
            assert await title.is_visible() and await title.inner_text() == "Plan the week"
            await title.click()
            assert "Rename" in " ".join(await page.locator(".popmenu").all_inner_texts())
            await page.keyboard.press("Escape")
            # a wide screen can hide the sidebar, and it stays hidden
            menu = page.locator("#menu")
            assert await menu.is_visible() and await menu.get_attribute("aria-expanded") == "true"
            await menu.click()
            assert not await side.is_visible()
            assert await menu.get_attribute("aria-expanded") == "false"
            await page.reload()
            await page.wait_for_selector(".conv", state="attached")
            assert not await side.is_visible()  # (remembered)
            await menu.click()
            assert await side.is_visible()
        finally:
            await browser.close()


async def test_the_context_meter(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    text = "() => ['context-used', 'context-of', 'context-note', 'meta-context']" \
        ".map(id => document.getElementById(id).textContent)"  # fmt: skip
    answer = {"type": "answer", "text": "Done.", "conversation": "c1",
              "stats": {"seconds": 1.0, "tokens": 5, "context_tokens": 21400}}  # fmt: skip
    async with async_api.async_playwright() as playwright:
        wide = {"width": 1400, "height": 800}
        browser, page = await open_app(playwright, static_site, viewport=wide)
        try:
            await page.wait_for_function("() => contextWindow === 32768")
            assert await page.evaluate(text) == [NONE, "", "Shows after the next answer.", ""]
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            await page.evaluate(SEND, answer)
            assert await page.evaluate(text) == [
                "65% used", "21.4k of 32.8k", "Older turns are summarized when it is full.",
                "Context 65%",
            ]  # fmt: skip
            bar = page.locator("#context-bar")
            assert await bar.get_attribute("class") == "context-bar warn"
            assert await bar.locator("i").evaluate("i => i.style.width") == "65%"
            answer["stats"]["context_tokens"] = 30000  # type: ignore[index]
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            await page.evaluate(SEND, answer)
            assert await bar.get_attribute("class") == "context-bar full"
            assert (await page.evaluate(text))[2].startswith("Nearly full")
            await page.locator(".conv", has_text="Recipe for Sunday").click()  # another one
            await page.wait_for_function("() => window.sockets.length > 0")
            assert (await page.evaluate(text))[0] == NONE  # known after its next answer
        finally:
            await browser.close()
        browser, page = await open_app(
            playwright, static_site, "localStorage.setItem('starlight-lang', 'de');\n"
        )
        try:
            await page.wait_for_function("() => contextWindow === 32768")
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            await page.evaluate(SEND, answer)
            await page.wait_for_function(
                "() => document.getElementById('meta-context').textContent === 'Kontext 92 %'"
            )
            words = await page.evaluate(text)
            assert words[:2] == ["92 % belegt", "30k von 32.8k"]
            assert await page.locator("#side h2").first.inner_text() == "Dieser PC"
        finally:
            await browser.close()
