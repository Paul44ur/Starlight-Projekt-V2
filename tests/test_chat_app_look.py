"""The chat page as a desktop app (after the Claude app's layout, in Starlight's own look): a
sidebar with the actions, the pages and the conversations (Pinned, then by time) and the model at
its foot; the open conversation's name in the bar with its menu; a sidebar that can be hidden;
and a meter of how full the model's context is."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from starlight.web.app import answer_stats
from tests.test_chat_page import SEND, answer, static_site  # noqa: F401  (a fixture)
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
# the page's "now": Date is replaced, `window.__now` moves the clock
CLOCK = (
    "window.__now = new Date(2026, 10, 14, 12, 0, 0).getTime();\n"
    "const RealDate = Date;\n"
    "Date = class extends RealDate { constructor(...a) { if (a.length) super(...a);"
    " else super(window.__now); } static now() { return window.__now; } };\n"
)
NONE = "\u2013"  # (the meter before an answer: a dash)
APP = (
    "window.sockets = []; window.WebSocket = class { constructor() { this.readyState = 1;"
    " window.sockets.push(this); setTimeout(() => this.onopen && this.onopen(), 20); }"
    " send() {} close() {} }; window.WebSocket.OPEN = 1;\n"
    f"const LIST = {json.dumps(LIST)}, STATUS = {json.dumps(STATUS)};\n"
    "window.fetch = async (url) => { const u = String(url);"
    " const stored = (window.storedFor || ['c2']).some("
    "id => u.endsWith('/api/conversations/' + id)) ? {context_tokens: 9000} : {};"
    " const body = u.includes('/api/conversations/') ? {private: false, messages: [], ...stored}"
    " : u.includes('/api/conversations') ? LIST : STATUS;"
    " if (u.includes('/api/status') && window.statusDelay) await new Promise("
    "r => setTimeout(r, window.statusDelay));"
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


def test_the_web_chat_stores_the_context_number_with_the_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlight.core.model import EchoProvider

    real = EchoProvider.chat

    async def reporting(self: Any, messages: Any, tools: Any) -> Any:
        response = await real(self, messages, tools)  # (the echo model reports no usage)
        response.usage = {"prompt_tokens": 800, "completion_tokens": 200}
        return response

    monkeypatch.setattr(EchoProvider, "chat", reporting)
    with client(home(tmp_path / "h", remote=False)) as c:
        made = c.post("/api/conversations", headers=LOCAL).json()["id"]
        assert c.get(f"/api/conversations/{made}", headers=LOCAL).json()["context_tokens"] is None
        with c.websocket_connect("/ws", headers=LOCAL) as ws:
            ws.send_json({"type": "ask", "text": "Hello", "conversation": made})
            while (said := ws.receive_json())["type"] != "answer":
                pass
        assert said["stats"]["context_tokens"] == 1000
        assert c.get(f"/api/conversations/{made}", headers=LOCAL).json()["context_tokens"] == 1000


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
        browser, page = await open_app(playwright, static_site, CLOCK)
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
            assert labels[:5] == [
                "PINNED",
                "Plan the week",
                "Server check",
                "OLDER",
                "Recipe for Sunday",
            ]  # fmt: skip  (.section-label: upper case)
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


async def test_the_stored_number_shows_although_the_status_comes_late(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    # the first conversation opens on page load, before /api/status answered
    late = "window.statusDelay = 600; window.storedFor = ['c1'];\n"  # (c1 is the one opened)
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, late)
        try:
            await page.wait_for_function("() => contextWindow === 32768")
            assert await page.locator("#meta-context").inner_text() == "Context 27%"
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
            await page.locator(".conv", has_text="Server check").click()  # none stored
            await page.wait_for_function("() => conversation === 'c3'")
            assert await page.evaluate(text) == [NONE, "", "Shows after the next answer.", ""]
            await page.locator(".conv", has_text="Recipe for Sunday").click()  # 9000 stored
            await page.wait_for_function("() => conversation === 'c2'")
            assert (await page.evaluate(text))[:2] == ["27% used", "9k of 32.8k"]
            assert (await page.evaluate(text))[3] == "Context 27%"
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
            assert words[:2] == ["92 % belegt", "30k von 32,8k"]  # (a German comma)
            first = await page.locator("#side h2").first.inner_text()
            assert first == "DIESER PC"  # (.section-label: upper case)
            assert await page.locator(".side-nav").get_attribute("aria-label") == "Seiten"
        finally:
            await browser.close()


async def test_the_bar_stays_one_row_in_a_narrower_window(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for width in (820, 1000, 1400):
            browser, page = await open_app(
                playwright, static_site, viewport={"width": width, "height": 700}
            )
            try:
                await page.wait_for_function("() => !document.getElementById('title').hidden")
                bar = await page.locator("#main header.top").bounding_box()
                assert bar is not None and bar["height"] < 70, width  # (the sidebar beside it)
                assert await page.locator("#kill").is_visible()
            finally:
                await browser.close()


# A list that answers a search and the archive, and keeps what is changed (as the server does).
NARROWED = """([list, status]) => { window.served = {list, status};
  const real = window.fetch; window.fetch = async (url, init) => {
  const u = new URL(String(url), location.href), json = (body) => new Response(
    JSON.stringify(body), {headers: {'content-type': 'application/json'}});
  if (u.pathname === '/api/conversations' && (!init || init.method === 'GET')) {
    const search = (u.searchParams.get('search') || '').toLowerCase();
    const archived = !!u.searchParams.get('archived');
    return json({current: 'c1', conversations: list.conversations.filter(c =>
      c.title.toLowerCase().includes(search) && !!c.archived === archived)});
  }
  if (init && init.method === 'PATCH') {
    const change = JSON.parse(init.body), id = u.pathname.split('/').pop();
    Object.assign(list.conversations.find(c => c.id === id), change);
    return json({id, ...change});
  }
  if (u.pathname === '/api/status') return json(status);
  return real(url, init); }; }"""


async def test_the_name_in_the_bar_stays_and_names_are_never_translated(
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
            await page.evaluate(NARROWED, [LIST, STATUS])
            title, slash = page.locator("#title"), page.locator(".bar-sep")
            await page.locator("#search").fill("recipe")  # the open one is not in the list
            await page.wait_for_function("() => document.querySelectorAll('.conv').length === 1")
            assert await title.is_visible() and await slash.is_visible()
            assert await title.inner_text() == "Plan the week"
            # renamed from the bar while the list leaves it out; a name is never translated
            await title.click()
            await page.locator(".popmenu button").first.click()
            await answer(page, text="Memory")
            await page.wait_for_function(
                "() => document.getElementById('title-text').textContent === 'Memory'"
            )
            await page.locator("#search").fill("")
            await page.wait_for_function("() => document.querySelectorAll('.conv').length === 3")
            assert "Memory" in await page.locator("#list .conv .t").all_inner_texts()
            assert await title.inner_text() == "Memory"
            await page.locator("#archived").click()  # the archive: it is not in there either
            await page.wait_for_function("() => !document.querySelector('.conv')")
            assert await title.inner_text() == "Memory"
            await page.evaluate("() => { served.status.model_name = 'Memory'; refresh(); }")
            await page.wait_for_function(
                "() => document.getElementById('model-name').textContent === 'Memory'"
            )
            assert await page.locator("#model").text_content() == "Memory"
            # no conversation open: no name, and no lone slash
            await page.evaluate("() => showTitle(null)")
            assert not await title.is_visible() and not await slash.is_visible()
        finally:
            await browser.close()
