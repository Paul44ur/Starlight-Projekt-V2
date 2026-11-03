"""The chat page's look (UI block U3) in a real browser: a calm start for a new conversation,
a caret while an answer streams in, a button back to the newest message, quieter tools on
older answers, and a message box that is one line at rest."""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_chat_page import SEND, open_chat, static_site  # noqa: F401  (a fixture)

# The server's conversations, stood in for: "c1" is new and empty, "secret" is private.
CONVERSATIONS = """() => { window.fetch = async (url) => {
  const u = String(url);
  const body = u.includes("/api/conversations/") ? {private: u.includes("secret"), messages: []}
    : u.includes("/api/conversations") ? {current: "c1", conversations: []} : {};
  return new Response(JSON.stringify(body), {headers: {"content-type": "application/json"}});
}; }"""


async def chat(playwright: Any, site: str) -> tuple[Any, Any]:
    browser, page = await open_chat(playwright, site)
    await page.emulate_media(reduced_motion="reduce")  # (no smooth scroll or fades to wait for)
    return browser, page


async def answer(page: Any, text: str) -> None:
    await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
    await page.evaluate(SEND, {"type": "answer", "text": text, "conversation": "c1"})


async def test_a_new_conversation_starts_calm_and_the_first_answer_clears_it(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            await page.evaluate(CONVERSATIONS)
            await page.evaluate("() => open('c1')")
            empty = page.locator("#log .empty")
            await empty.wait_for()
            assert await empty.locator(".empty-title").text_content() == (
                "New conversation. Ask anything."
            )
            assert "nothing you say leaves it" in (await empty.text_content() or "")
            assert "Type / for commands" in (await empty.text_content() or "")
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            assert await page.locator("#log .empty").count() == 0  # gone with the first answer
            await page.evaluate("() => open('secret')")
            await page.locator("#log .empty").wait_for()
            assert await page.locator(".empty-title").text_content() == "Private conversation"
            assert "never saved" in (await page.locator(".empty").text_content() or "")
        finally:
            await browser.close()


async def test_a_caret_shows_while_the_answer_streams_in(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    caret = """() => {
      const p = [...document.querySelectorAll('.msg.bot')].pop().querySelector('.body p');
      return p ? getComputedStyle(p, '::after').content : null;
    }"""
    async with async_api.async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            for end in ({"type": "answer", "text": "Kelvin is Celsius plus 273.15."},
                        {"type": "stopped"}):  # fmt: skip
                await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
                await page.evaluate(SEND, {"type": "token", "text": "Kelvin is Celsius plus"})
                await page.wait_for_function("() => document.querySelector('.msg.streaming p')")
                assert await page.evaluate(caret) == '""'  # a caret after the words
                await page.evaluate(SEND, {**end, "conversation": "c1"})
                assert await page.locator(".msg.streaming").count() == 0
                assert await page.evaluate(caret) == "none"
        finally:
            await browser.close()


async def test_a_button_brings_you_back_to_the_newest_message(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            await page.set_viewport_size({"width": 1200, "height": 600})
            for n in range(8):
                await answer(page, f"Answer {n}\n\n" + "A line of text.\n\n" * 6)
            button = page.locator("#tolatest")
            # the page stays at the newest message as answers come (also one that did not
            # stream, or came out longer than it streamed), so there is nothing to jump to
            assert await page.evaluate("() => atBottom()")
            assert await button.is_hidden()
            await page.evaluate("() => { document.getElementById('log').scrollTop = 0; }")
            await button.wait_for(state="visible")
            assert not await button.evaluate("b => b.classList.contains('new')")
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})  # new, below
            await page.wait_for_function(
                "() => document.getElementById('tolatest').classList.contains('new')"
            )
            await button.click()
            await page.wait_for_function("() => document.getElementById('tolatest').hidden")
            assert await page.evaluate("() => atBottom()")
            assert not await button.evaluate("b => b.classList.contains('new')")
        finally:
            await browser.close()


async def test_older_answers_keep_their_tools_out_of_the_way(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    look = "ts => ts.map(t => [getComputedStyle(t).opacity, getComputedStyle(t).position])"
    async with async_api.async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            await answer(page, "The first answer.")
            await answer(page, "The second answer.")
            tools = page.locator(".msg.bot .tools")

            async def settled(want: list[list[str]]) -> None:  # (after the fade)
                await page.wait_for_function(
                    f"want => JSON.stringify(({look})([...document.querySelectorAll("
                    "'.msg.bot .tools')])) === JSON.stringify(want)",
                    arg=want,
                )

            # the older one's bar takes no room and shows when pointed at; the latest's stays
            await settled([["0", "absolute"], ["1", "static"]])
            await page.locator(".msg.bot").first.hover()
            await settled([["1", "absolute"], ["1", "static"]])
            # and Tab reaches it too
            await page.mouse.move(0, 0)
            await settled([["0", "absolute"], ["1", "static"]])
            await page.locator(".msg.bot").first.locator(".tools button").first.focus()
            await settled([["1", "absolute"], ["1", "static"]])
            assert len(await tools.all()) == 2
        finally:
            await browser.close()


async def test_the_message_box_is_one_line_and_send_is_the_main_button(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            box, send = page.locator("#input"), page.locator("#send")
            assert await box.evaluate("i => i.offsetHeight") == 44  # one line at rest
            await box.fill("one\ntwo\nthree\nfour")
            await box.dispatch_event("input")
            assert await box.evaluate("i => i.offsetHeight") > 44  # it grows with the text
            assert await send.get_attribute("class") == "primary"
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            assert await send.get_attribute("class") == "stop"
            await page.evaluate(SEND, {"type": "answer", "text": "Done.", "conversation": "c1"})
            assert await send.get_attribute("class") == "primary"
        finally:
            await browser.close()


async def test_the_new_chat_texts_are_in_german(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            await page.evaluate("() => localStorage.setItem('starlight-lang', 'de')")
            await page.reload()
            await page.evaluate("() => { if (!window.sockets.length) connect(); }")
            await page.wait_for_function("() => window.sockets.length > 0")
            await page.evaluate(CONVERSATIONS)
            await page.evaluate("() => open('c1')")
            await page.locator("#log .empty").wait_for()
            await page.wait_for_function(
                "() => document.querySelector('.empty-title').textContent.startsWith('Neues')"
            )
            assert await page.locator(".empty-title").text_content() == (
                "Neues Gespräch. Frag einfach."
            )
            text = await page.locator(".empty").text_content() or ""
            assert "Nichts, was du sagst" in text and "Tippe / für Befehle" in text
            label = await page.locator("#tolatest").get_attribute("aria-label")
            assert label == "Zur neuesten Nachricht springen"
            await page.evaluate("() => addMsg('you', 'Hallo')")
            await page.wait_for_function(
                "() => document.querySelector('.msg.you .who').textContent === 'du'"
            )
        finally:
            await browser.close()


async def test_the_newest_message_stays_in_view_while_the_message_box_grows(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            await page.set_viewport_size({"width": 1200, "height": 600})
            await page.wait_for_function("() => innerHeight === 600")
            for n in range(6):
                await answer(page, f"Answer {n}\n\n" + "A line of text.\n\n" * 6)
            box = page.locator("#input")
            # a long message makes the box taller and the conversation's area shorter: the
            # newest message stays in view, and there is nothing to jump to
            await box.fill("\n".join(["a line"] * 7))
            await box.dispatch_event("input")
            assert await box.evaluate("i => i.offsetHeight") > 120
            await page.wait_for_function("() => nearBottom()")
            assert await page.locator("#tolatest").is_hidden()
            # scrolled up to read: the box changing size leaves you where you are
            await page.evaluate("() => { document.getElementById('log').scrollTop = 0; }")
            await page.wait_for_function("() => !pinned")
            await box.fill("")
            await box.dispatch_event("input")
            await page.wait_for_timeout(150)
            assert await page.evaluate("() => document.getElementById('log').scrollTop") == 0
        finally:
            await browser.close()
