"""Drafts: what you typed in the message box and did not send stays with its conversation (in
the browser's localStorage, a private one only in the page's memory), and comes back."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from tests.test_chat_app_look import APP, LIST, STATUS  # noqa: F401
from tests.test_chat_page import answer, static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import browser_page

KEY = "starlight-draft:"
# On top of the page's stand-ins: a new conversation is "c9", and "p1" is a private one.
EXTRA = """
const standIn = window.fetch;
const type = {'content-type': 'application/json'};
const asJson = (body) => new Response(JSON.stringify(body), {headers: type});
window.fetch = async (url, options) => {
  const u = String(url), method = (options && options.method) || 'GET';
  if (method === 'POST' && u.endsWith('/api/conversations')) return asJson({id: 'c9'});
  if (u.endsWith('/api/conversations/p1')) return asJson({private: true, messages: []});
  return standIn(url, options);
};
"""
THROWS = "Storage.prototype.setItem = function () { throw new Error('full'); };\n"


@asynccontextmanager
async def chat(site: str, monkeypatch: pytest.MonkeyPatch, extra: str = "") -> AsyncIterator[Any]:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await browser_page(playwright, site, "chat.html", APP + EXTRA + extra)
        try:
            await page.wait_for_selector(".conv")
            await page.wait_for_function("() => document.querySelector('.conv.active')")
            yield page
        finally:
            await browser.close()


async def stored(page: Any, id: str) -> str | None:
    return await page.evaluate("id => localStorage.getItem('starlight-draft:' + id)", id)


async def saved(page: Any, id: str, text: str | None) -> None:
    """Waits for the short delay after typing, until the draft is (or is no longer) stored."""
    await page.wait_for_function(
        "([id, text]) => localStorage.getItem('starlight-draft:' + id) === text",
        arg=[id, text],
    )


async def open_row(page: Any, title: str) -> None:
    await page.locator(".conv", has_text=title).click()
    await page.wait_for_selector(f".conv.active:has-text('{title}')")


async def test_each_conversation_keeps_its_draft_also_after_a_reload(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await page.fill("#input", "Dear team,\nlong text")
        await open_row(page, "Recipe for Sunday")  # at once, no wait for the delay
        assert await page.input_value("#input") == ""
        await page.fill("#input", "salt?")
        await open_row(page, "Plan the week")
        assert await page.input_value("#input") == "Dear team,\nlong text"
        assert await stored(page, "c1") == "Dear team,\nlong text"
        assert await stored(page, "c2") == "salt?"
        await page.reload()
        await page.wait_for_selector(".conv.active")
        assert await page.input_value("#input") == "Dear team,\nlong text"
        # a long draft is sized like typed text
        await page.fill("#input", "\n".join(["line"] * 6))
        await open_row(page, "Recipe for Sunday")
        await open_row(page, "Plan the week")
        assert await page.evaluate("document.querySelector('#input').offsetHeight") > 60


async def test_sending_or_emptying_the_box_removes_the_draft(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await page.fill("#input", "to be sent")
        await saved(page, "c1", "to be sent")
        await page.press("#input", "Enter")
        await page.wait_for_selector(".msg.you")
        assert await stored(page, "c1") is None
        await page.reload()
        await page.wait_for_selector(".conv.active")
        assert await page.input_value("#input") == ""
        await page.fill("#input", "typed")
        await saved(page, "c1", "typed")
        await page.fill("#input", "")
        await saved(page, "c1", None)
        await page.fill("#input", "again")
        await saved(page, "c1", "again")
        await page.click("#send")  # the Send button, too
        assert await stored(page, "c1") is None


async def test_deleting_a_conversation_removes_its_draft(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await open_row(page, "Recipe for Sunday")
        await page.fill("#input", "half a sentence")
        await saved(page, "c2", "half a sentence")
        await (
            page.locator(".conv", has_text="Recipe for Sunday")
            .locator(".acts button")
            .click(force=True)
        )
        await page.locator(".popmenu button", has_text="Delete").click()
        await answer(page)
        await page.wait_for_function("() => !document.querySelector('#input').value")
        await page.wait_for_timeout(500)  # (a late save would show now)
        assert await stored(page, "c2") is None


async def test_a_private_conversations_draft_is_never_on_disk(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await page.evaluate("openConversation('p1')")
        await page.wait_for_function("() => !document.getElementById('privacy').hidden")
        await page.fill("#input", "secret plan")
        await page.wait_for_timeout(500)
        await open_row(page, "Recipe for Sunday")
        assert await page.input_value("#input") == ""
        await page.evaluate("openConversation('p1')")
        await page.wait_for_function("() => document.querySelector('#input').value")
        assert await page.input_value("#input") == "secret plan"
        await page.fill("#input", "secret plan, more")
        await page.evaluate("window.dispatchEvent(new Event('pagehide'))")
        disk = await page.evaluate(
            "JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])"
        )
        assert "secret" not in disk


async def test_a_restored_draft_starting_with_a_slash_does_not_open_the_command_menu(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await page.evaluate(f"localStorage.setItem('{KEY}c2', '/pr')")
        await open_row(page, "Recipe for Sunday")
        assert await page.input_value("#input") == "/pr"
        await page.wait_for_timeout(300)
        assert await page.locator(".popmenu").count() == 0
        await page.fill("#input", "/pr")  # typing it still opens the menu
        await page.wait_for_selector(".popmenu")


async def test_drafts_of_conversations_that_are_gone_are_removed_but_nothing_else(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await page.evaluate(
            f"localStorage.setItem('{KEY}gone', 'x'); localStorage.setItem('{KEY}c3', 'y');"
            " localStorage.setItem('starlight-sidebar', 'on');"
            " localStorage.setItem('other:gone', 'z');"
        )
        await page.reload()
        await page.wait_for_selector(".conv.active")
        await page.wait_for_function(f"() => localStorage.getItem('{KEY}gone') === null")
        assert await stored(page, "c3") == "y"
        assert await page.evaluate("localStorage.getItem('other:gone')") == "z"
        assert await page.evaluate("localStorage.getItem('starlight-sidebar')") == "on"


async def test_the_chat_works_when_localstorage_cannot_be_written(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch, THROWS) as page:
        await page.fill("#input", "no room")
        await open_row(page, "Recipe for Sunday")
        await open_row(page, "Plan the week")
        await page.fill("#input", "still sending")
        await page.press("#input", "Enter")
        await page.wait_for_selector(".msg.you")
        assert await page.input_value("#input") == ""
