"""The chat page in the Paper look (UI 3 plan block P2, docs/plan-ui-3.md): the sidebar on the
ground with a thin line (no card), the open row's line at its left, messages as a document (a
line above each with who, when and how long; your words plain; answers in the serif; the tool
steps as one italic line), a plain bordered message box with "Send" under it, a plain start
(no logo; recent conversations and things to ask as links), a calmer details pane that shows
only what has a value, the keys shown only where there is room, nothing sideways on a phone,
no radius above 2 px (circles excepted), no colour of its own in chat.css, and the German
texts."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_chat_app_look import open_app
from tests.test_chat_page import SEND, static_site  # noqa: F401  (a fixture)

STATS = {"seconds": 4.1, "tokens": 212, "tokens_per_second": 51.7, "context_tokens": 8800}
NONE = "\N{EN DASH}"  # (a dash: no answer yet)
WIDE = {"width": 1400, "height": 860}
TOKEN = (
    "(name) => { const probe = document.createElement('i');"
    " probe.style.background = 'var(' + name + ')'; document.body.append(probe);"
    " const colour = getComputedStyle(probe).backgroundColor; probe.remove(); return colour; }"
)
FONT = (
    "(name) => { const probe = document.createElement('i');"
    " probe.style.fontFamily = 'var(' + name + ')'; document.body.append(probe);"
    " const font = getComputedStyle(probe).fontFamily; probe.remove(); return font; }"
)
# the corners of every element: a radius above 2 px (or a percentage other than a circle)
ROUND = """() => {
  const found = [];
  for (const e of document.querySelectorAll('body *')) {
    const s = getComputedStyle(e);
    for (const side of ['TopLeft', 'TopRight', 'BottomLeft', 'BottomRight']) {
      for (const v of s['border' + side + 'Radius'].split(' ')) {
        const big = v.endsWith('%') ? v !== '50%' : parseFloat(v) > 2;
        if (big) found.push(e.tagName + '#' + e.id + '.' + e.className + ' ' + v);
      }
    }
  }
  return found;
}"""
ASK = [
    {"text": "How does my day look?", "module": "briefing"},
    {"text": "What is due today?", "module": "todo"},
]
SUGGEST = (
    "() => { const real = window.fetch; window.fetch = async (u, o) => String(u).includes("
    f"'/api/suggestions') ? new Response(JSON.stringify({{suggestions: {json.dumps(ASK)}}}),"
    " {headers: {'content-type': 'application/json'}}) : real(u, o);"
    " log.innerHTML = ''; showEmpty(false); }"
)
# the status the page gets from now on (then it asks again)
STATUS_NOW = (
    "async (status) => { const real = window.fetch; window.fetch = async (u, o) =>"
    " String(u).includes('/api/status') ? new Response(JSON.stringify(status),"
    " {headers: {'content-type': 'application/json'}}) : real(u, o); await refresh(); }"
)
BASE_STATUS = {
    "local": True,
    "kill": {"engaged": False},
    "access": {},
    "model": "lmstudio",
    "model_name": "qwen3-14b",
    "context_window": 32768,
}


async def answer_with_tools(page: Any) -> None:
    await page.evaluate("() => addMsg('you', 'Is my web server up?')")
    await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
    for tool, ms in (("web.check", 182), ("log.read", 40)):
        await page.evaluate(SEND, {"type": "tool", "tool": tool, "state": "running"})
        await page.evaluate(SEND, {"type": "tool", "tool": tool, "state": "done", "ms": ms})
    done = {"type": "answer", "text": "Yes, it is up.", "conversation": "c1", "stats": STATS}
    await page.evaluate(SEND, done)


def colour_of(prop: str) -> str:
    return f"e => getComputedStyle(e).{prop}"


async def test_the_sidebar_is_not_a_card_and_new_conversation_is_the_filled_button(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, viewport=WIDE)
        try:
            side = page.locator("#convs")
            box = await side.bounding_box()
            assert box is not None and box["x"] == 0 and box["y"] == 0  # (no inset: not a card)
            assert box["height"] == WIDE["height"]
            assert await side.evaluate(colour_of("borderTopLeftRadius")) in ("0px", "2px")
            assert await side.evaluate(colour_of("backgroundColor")) == await page.evaluate(
                TOKEN, "--bg"
            )
            assert await side.evaluate(colour_of("borderRightWidth")) == "1px"  # (the thin line)
            assert await side.evaluate(colour_of("boxShadow")) == "none"
            primary = await page.evaluate(TOKEN, "--primary")
            new = page.locator("#new")
            assert await new.evaluate(colour_of("backgroundColor")) == primary
            assert await new.evaluate(colour_of("borderTopLeftRadius")) == "2px"
            for other in ("#newprivate", "#quick-open"):  # (quiet: no fill)
                button = page.locator(other)
                assert await button.evaluate(colour_of("backgroundColor")) != primary
                assert await button.evaluate(colour_of("borderTopColor")) == "rgba(0, 0, 0, 0)"
            # the brand is the word, in the display font
            assert await page.locator(".side-brand svg.ico").count() == 0
            assert await page.locator(".brand-name").inner_text() == "Starlight"
            assert await page.locator(".brand-name").evaluate(
                colour_of("fontFamily")
            ) == await page.evaluate(FONT, "--font-display")
            # the open conversation: --panel2 behind it and a 2 px accent line at its left
            row = page.locator(".conv.active")
            assert await row.evaluate(colour_of("backgroundColor")) == await page.evaluate(
                TOKEN, "--panel2"
            )
            assert await row.evaluate(colour_of("borderLeftWidth")) == "2px"
            accent = await page.evaluate(TOKEN, "--accent")
            assert await row.evaluate(colour_of("borderLeftColor")) == accent
            other = page.locator(".conv:not(.active)").first
            assert await other.evaluate(colour_of("borderLeftColor")) == "rgba(0, 0, 0, 0)"
            dot = await row.evaluate("r => getComputedStyle(r, '::before').content")
            assert dot in ("none", "normal")  # (no dot before the name)
            assert await row.evaluate("r => r.offsetHeight") == 36  # (44 on touch)
            assert await page.locator(".side-nav a").first.evaluate("a => a.offsetHeight") == 36
            assert await page.locator("#list .folder.section-label").count() == 2
        finally:
            await browser.close()


async def test_the_search_button_opens_the_quick_switcher(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, viewport=WIDE)
        try:
            button = page.locator("#quick-open")
            assert "Search or jump to" in await button.inner_text()
            assert await button.locator("kbd").inner_text() == "Ctrl K"
            assert not await page.locator("dialog#quick[open]").count()
            await button.click()
            await page.wait_for_selector("dialog#quick[open]")
            await page.keyboard.press("Escape")
            await page.wait_for_function("() => !document.querySelector('dialog#quick[open]')")
        finally:
            await browser.close()


async def test_the_tool_steps_are_one_italic_line_that_opens_the_timeline(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, viewport=WIDE)
        try:
            await answer_with_tools(page)
            chip = page.locator(".msg.bot .toolsum")
            assert await chip.count() == 1 and await chip.evaluate("b => b.tagName") == "BUTTON"
            assert await chip.locator(".ts-text").inner_text() == "Used 2 tools"
            assert await chip.locator(".ts-mark").inner_text() == "✓"
            # a plain line: italic, dim, no box and no fill
            assert await chip.evaluate(colour_of("fontStyle")) == "italic"
            assert await chip.evaluate(colour_of("color")) == await page.evaluate(TOKEN, "--dim")
            assert await chip.evaluate(colour_of("backgroundColor")) == "rgba(0, 0, 0, 0)"
            assert await chip.evaluate(colour_of("borderTopColor")) == "rgba(0, 0, 0, 0)"
            timeline = page.locator(".msg.bot .toolrow")
            assert await chip.get_attribute("aria-expanded") == "false"
            assert not await timeline.is_visible()  # (the steps wait behind the line)
            await chip.click()
            assert await chip.get_attribute("aria-expanded") == "true"
            assert await timeline.is_visible()
            assert await page.locator(".toolstep").all_inner_texts() == [
                "✓ web.check 182 ms",
                "✓ log.read 40 ms",
            ]
            await chip.click()
            assert not await timeline.is_visible()
        finally:
            await browser.close()


async def test_messages_are_a_document(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, viewport=WIDE)
        try:
            await answer_with_tools(page)
            you, bot = page.locator(".msg.you"), page.locator(".msg.bot")
            # who, and when: a small upper-case line above each message
            for msg in (you, bot):
                who, body = msg.locator(".who"), msg.locator(".body")
                assert await who.evaluate(colour_of("textTransform")) == "uppercase"
                assert await who.evaluate(colour_of("color")) == await page.evaluate(TOKEN, "--dim")
                above = await who.bounding_box()
                text = await body.bounding_box()
                assert above is not None and text is not None
                assert above["y"] + above["height"] <= text["y"]
                assert re.fullmatch(r"\d\d?[:.]\d\d.*", await msg.locator(".when").inner_text())
            assert await you.locator(".who").text_content() == "you"  # (the text; CSS capitalises)
            assert await bot.locator(".who").text_content() == "starlight"
            assert (await bot.locator(".when").inner_text()).endswith(" · 4.1 s")  # (how long)
            # yours: plain text, no bubble (no fill, no line, no corner)
            for node in (you, you.locator(".body")):
                assert await node.evaluate(colour_of("backgroundColor")) == "rgba(0, 0, 0, 0)"
                assert await node.evaluate(colour_of("borderTopWidth")) == "0px"
                assert await node.evaluate(colour_of("boxShadow")) == "none"
            assert await bot.evaluate(colour_of("backgroundColor")) == "rgba(0, 0, 0, 0)"
            # answers in the serif, a little larger than yours; yours in the plain face
            serif = await page.evaluate(FONT, "--font-display")
            assert await bot.locator(".body").evaluate(colour_of("fontFamily")) == serif
            assert await you.locator(".body").evaluate(colour_of("fontFamily")) != serif
            size = "e => parseFloat(getComputedStyle(e).fontSize)"
            assert await bot.locator(".body").evaluate(size) > await you.locator(".body").evaluate(
                size
            )
            # a column of about 680 px
            width = (await bot.bounding_box() or {})["width"]
            assert 640 <= width <= 700, width
            # Copy, Regenerate, Branch, Edit: plain text links in --dim (still buttons)
            for link in (bot.locator(".tools button").first, you.locator(".tools .edit")):
                assert await link.evaluate("b => b.tagName") == "BUTTON"
                assert await link.evaluate(colour_of("borderTopWidth")) == "0px"
                assert await link.evaluate(colour_of("backgroundColor")) == "rgba(0, 0, 0, 0)"
                assert await link.evaluate(colour_of("color")) == await page.evaluate(
                    TOKEN, "--dim"
                )
            assert await bot.locator(".tools button").all_inner_texts() == [
                "Copy",
                "Regenerate",
                "Branch",
            ]
            # the bar says how many messages there are
            assert await page.locator("#bar-meta").inner_text() == "2 messages"
            assert await page.locator("#bar-context .bc-bar").evaluate("b => b.offsetHeight") <= 3
            assert await page.evaluate(ROUND) == []  # (no radius above 2 px, but circles)
        finally:
            await browser.close()


async def test_the_message_box_is_a_plain_box_with_send_under_it(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, viewport=WIDE)
        try:
            box, send = page.locator("#input"), page.locator("#send")
            assert await box.evaluate(colour_of("borderTopLeftRadius")) == "2px"
            assert await box.evaluate(colour_of("borderTopWidth")) == "1px"
            assert await box.evaluate(colour_of("backgroundColor")) == await page.evaluate(
                TOKEN, "--panel"
            )
            assert await box.evaluate(colour_of("boxShadow")) == "none"
            assert await page.locator("#form").evaluate(colour_of("boxShadow")) == "none"
            assert await page.locator("#form").evaluate(colour_of("borderTopWidth")) == "0px"
            await box.focus()
            accent = await page.evaluate(TOKEN, "--accent")
            await page.wait_for_function(  # (after the short colour change)
                "a => getComputedStyle(document.getElementById('input')).borderTopColor === a",
                arg=accent,
            )
            # Send: a filled rectangle with its word (no round button, no arrow)
            assert await send.evaluate(colour_of("backgroundColor")) == await page.evaluate(
                TOKEN, "--primary"
            )
            assert await send.evaluate(colour_of("borderTopLeftRadius")) == "2px"
            assert await send.inner_text() == "Send"
            assert not await send.locator("svg.ico").is_visible()
            size = await send.bounding_box()
            assert size is not None and size["width"] > size["height"]
            # the small controls and the keys are in a row under the box
            field = await box.bounding_box()
            assert field is not None
            for control in ("#attach", "#camera", "#mic", "#talk", "#send-key"):
                spot = await page.locator(control).bounding_box()
                assert spot is not None and spot["y"] >= field["y"] + field["height"], control
            assert await page.locator("#send-key").inner_text() == (
                "Enter to send · Shift+Enter for a new line"
            )
            assert await page.locator("#send-key").is_visible()
            # Stop is the same button
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            assert await send.get_attribute("class") == "stop"
            assert await send.get_attribute("id") == "send"
        finally:
            await browser.close()


async def test_a_new_conversation_starts_plain_at_the_top(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, viewport=WIDE)
        try:
            await page.evaluate("() => openConversation('c1')")
            empty = page.locator("#log .empty")
            await empty.wait_for()
            assert await empty.locator("img, svg").count() == 0  # (no logo)
            title = empty.locator(".empty-title")
            assert await title.inner_text() == "New conversation"
            assert await title.evaluate(colour_of("fontFamily")) == await page.evaluate(
                FONT, "--font-display"
            )
            assert "runs on this PC" in await empty.inner_text()
            # at the top of the column, at its left: nothing in the middle of the screen
            spot, log = await empty.bounding_box(), await page.locator("#log").bounding_box()
            assert spot is not None and log is not None
            assert spot["y"] - log["y"] < 60
            assert await empty.evaluate(colour_of("textAlign")) in ("left", "start")
            # "Recently": the newest other conversations, each a link that opens it
            await page.wait_for_selector(".empty .recent-link")
            assert await empty.locator(".recent .section-label").inner_text() == "Recently"
            links = empty.locator(".recent-link")
            assert await links.all_inner_texts() == ["Server check", "Recipe for Sunday"]
            assert await links.first.evaluate("a => a.tagName") == "A"
            await links.nth(1).click()
            await page.wait_for_function("() => conversation === 'c2'")
            # the hints, and the first message clears the start
            await page.evaluate("() => openConversation('c1')")
            assert "Type / for commands" in await empty.inner_text()
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            assert await page.locator("#log .empty").count() == 0
            assert await page.evaluate(ROUND) == []
        finally:
            await browser.close()


async def test_the_things_to_ask_are_links_that_fill_the_box_and_send_nothing(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site, viewport=WIDE)
        try:
            await page.evaluate(SUGGEST)
            await page.wait_for_selector(".empty .suggest")
            assert await page.locator(".empty .suggestions .section-label").inner_text() == (
                "You can ask:"
            )
            links = page.locator(".empty .suggest")
            assert await links.all_inner_texts() == [item["text"] for item in ASK]
            accent = await page.evaluate(TOKEN, "--accent")
            first = links.first
            assert await first.evaluate(colour_of("color")) == accent
            assert await first.evaluate(colour_of("backgroundColor")) == "rgba(0, 0, 0, 0)"
            assert await first.evaluate(colour_of("borderTopWidth")) == "0px"
            # (the hints come after the things to ask)
            ask_at = await page.locator(".empty .suggestions").bounding_box()
            hint_at = await page.locator(".empty .empty-hint").bounding_box()
            assert ask_at is not None and hint_at is not None and ask_at["y"] < hint_at["y"]
            await page.evaluate(
                "() => { window.sockets.forEach(s => { s.sent = []; s.send = "
                "(m) => s.sent.push(m); }); }"
            )
            await links.nth(1).click()
            assert await page.locator("#input").input_value() == ASK[1]["text"]
            assert await page.locator(".empty").count() == 1  # (nothing was sent)
            assert await page.evaluate("() => window.sockets.every(s => !(s.sent || []).length)")
        finally:
            await browser.close()


async def test_the_details_pane_shows_only_what_has_a_value(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for width, shown in ((1400, True), (1200, True), (1199, False), (1000, False)):
            view = {"width": width, "height": 800}
            browser, page = await open_app(playwright, static_site, viewport=view)
            try:
                await page.wait_for_function("() => contextWindow === 32768")
                assert await page.locator("#side").is_visible() == shown, width
                if not shown:
                    continue
                visible = page.locator("#side section:not([hidden]) h2")
                # nothing known yet: only this PC, and the way to the settings (all four empty)
                assert await visible.all_inner_texts() == ["This PC"]
                link = page.locator("#side-access")
                assert await link.is_visible()
                assert await link.get_attribute("href") == "/settings"
                assert await link.inner_text() == "Access in the settings"
                assert await page.locator("#side").evaluate(colour_of("borderLeftWidth")) == "1px"
                await answer_with_tools(page)
                assert await visible.all_inner_texts() == ["This answer", "Context", "This PC"]
                values = page.locator("#answer-tools, #answer-time, #answer-tokens")
                assert await values.all_inner_texts() == ["2", "4.1 s", "212"]
                assert await page.locator("#context-of").inner_text() == "8.8k of 32.8k"
                assert await page.locator("#side h2.section-label").count() == 7
                # the bar's meter: the same percentage, in mono figures
                assert await page.locator("#bar-context-pct").inner_text() == "27%"
                fill = page.locator("#bar-context-fill")
                assert await fill.evaluate("i => i.style.width") == "27%"
                # another conversation: nothing of the last answer is left
                await page.locator(".conv", has_text="Server check").click()
                await page.wait_for_function("() => conversation === 'c3'")
                assert await values.all_inner_texts() == [NONE] * 3
                assert await visible.all_inner_texts() == ["This PC"]
                # each of the four comes back when it has something to say; then no link
                one = {"access": {"servers": ["files.example"]}}
                await page.evaluate(STATUS_NOW, BASE_STATUS | one)
                assert await visible.all_inner_texts() == ["This PC", "Allowed servers"]
                assert not await link.is_visible()
                many = {"access": {"login_sites": ["a.example"], "secrets": ["KEY"]}}
                await page.evaluate(STATUS_NOW, BASE_STATUS | many | {"saves_files": "notes.md"})
                assert await visible.all_inner_texts() == [
                    "This PC",
                    "Logins",
                    "Secrets",
                    "Saved files",
                ]
                # not at the PC (a phone paired to it): no link to the settings
                await page.evaluate(STATUS_NOW, BASE_STATUS | {"local": False})
                assert await visible.all_inner_texts() == ["This PC"]
                assert not await link.is_visible()
            finally:
                await browser.close()


async def test_the_keys_show_only_where_there_is_room(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for width, shown in ((1400, True), (1200, True), (1199, False), (820, False)):
            view = {"width": width, "height": 800}
            browser, page = await open_app(playwright, static_site, viewport=view)
            try:
                kill = page.locator("#kill kbd")
                assert await kill.count() == 1
                assert await kill.is_visible() == shown, width
                if shown:
                    assert await kill.inner_text() == "Ctrl Alt J"
                    # the Kill switch: an outlined --bad rectangle
                    button = page.locator("#kill")
                    bad = await page.evaluate(TOKEN, "--bad")
                    assert await button.evaluate(colour_of("borderTopColor")) == bad
                    assert await button.evaluate(colour_of("backgroundColor")) == "rgba(0, 0, 0, 0)"
                    assert await button.evaluate(colour_of("borderTopLeftRadius")) == "2px"
                bar = await page.locator("#main header.top").bounding_box()
                assert bar is not None and bar["height"] < 80, width  # (still one row)
            finally:
                await browser.close()


async def test_nothing_scrolls_sideways_on_a_phone(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(
            playwright, static_site, viewport={"width": 360, "height": 740}
        )
        try:
            await answer_with_tools(page)
            await page.locator(".msg.bot .toolsum").click()
            assert await page.evaluate("() => document.documentElement.scrollWidth") <= 360
            kill = await page.locator("#kill").bounding_box()
            assert kill is not None and kill["x"] + kill["width"] <= 360  # (on screen)
            assert not await page.locator("#side").is_visible()
            send = await page.locator("#send").bounding_box()
            assert send is not None and send["x"] + send["width"] <= 360
            await page.locator("#menu").click()  # the drawer works as before
            assert await page.locator("#quick-open").is_visible()
            assert not await page.locator("#quick-open kbd").is_visible()  # (no keyboard)
            assert await page.evaluate("() => document.documentElement.scrollWidth") <= 360
            # the drawer's rows are 44 px tall
            for row in (".conv", ".side-nav a", "#new", "#newprivate"):
                high = await page.locator(row).first.evaluate("r => r.offsetHeight")
                assert high >= 44, row
            assert await page.evaluate("() => document.getElementById('convs').offsetWidth") <= 300
        finally:
            await browser.close()


async def test_the_new_chat_texts_are_in_german(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(
            playwright,
            static_site,
            "localStorage.setItem('starlight-lang', 'de');\n",
            viewport=WIDE,
        )
        try:
            await page.wait_for_function("() => contextWindow === 32768")
            await page.evaluate("() => openConversation('c1')")
            await page.evaluate(SUGGEST)
            await page.wait_for_selector(".empty .suggest")
            await page.wait_for_function(
                "() => document.querySelector('.empty .recent .section-label')"
                ".textContent === 'Zuletzt'"
            )
            assert await page.locator(".empty .suggestions .section-label").inner_text() == (
                "Du kannst fragen:"
            )
            assert await page.locator("#side-access").inner_text() == (
                "Zugänge in den Einstellungen"
            )
            assert await page.locator("#send-key").inner_text() == (
                "Enter senden · Umschalt+Enter neue Zeile"
            )
            assert await page.locator("#send").inner_text() == "Senden"
            # (the names of conversations are the user's: never translated)
            assert await page.locator(".recent-link").first.get_attribute("translate") == "no"
            await answer_with_tools(page)
            await page.wait_for_function(
                "() => document.querySelector('.toolsum .ts-text').textContent !== 'Used 2 tools'"
            )
            assert await page.locator(".toolsum .ts-text").inner_text() == "2 Werkzeuge benutzt"
            assert await page.locator("#bar-meta").inner_text() == "2 Nachrichten"
            assert await page.locator(".msg.you .who").text_content() == "du"  # (shown upper case)
            assert (await page.locator(".msg.bot .when").inner_text()).endswith(" · 4,1 s")
            # the time as German writes it: 24 hours, never "PM" (the browser here is en-US)
            when = await page.locator(".msg.bot .when").inner_text()
            assert re.fullmatch(r"\d\d:\d\d · 4,1 s", when), when
            assert "Suchen oder springen" in await page.locator("#quick-open").inner_text()
            assert await page.locator("#quick-open kbd").inner_text() == "Strg K"
            assert await page.locator("#kill kbd").inner_text() == "Strg Alt J"
            shown = await page.locator("#side section:not([hidden]) h2").all_inner_texts()
            assert shown == ["Diese Antwort", "Kontext", "Dieser PC"]
            rows = await page.locator("#sec-answer .kv > :first-child").all_inner_texts()
            assert rows == ["Werkzeuge", "Zeit", "Token"]
            assert await page.locator("#bar-context").get_attribute("title") == (
                "Wie voll der Kontext des Modells ist"
            )
        finally:
            await browser.close()


def test_chat_css_has_no_radius_above_two_pixels_and_no_colour_of_its_own() -> None:
    css = (REPO / "starlight/web/static/chat.css").read_text(encoding="utf-8")
    radii = re.findall(r"border-radius:\s*([^;}]+)", css)
    assert radii  # (the rule is read at all)
    for value in radii:
        # (50%: a circle; the tokens are 2 px)
        assert value.strip() in {
            "var(--r-1)",
            "var(--r-2)",
            "var(--r-3)",
            "var(--r-4)",
            "var(--r-pill)",
            "0",
            "50%",
            "2px",
        }, value
    # no literal colour: tokens only (the tests of style.css cover the tokens themselves)
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b(?!-)|rgba?\(|hsla?\(", css) == []
    # no shadow of its own at rest (a menu and the drawer take the token)
    for value in re.findall(r"box-shadow:\s*([^;}]+)", css):
        assert value.strip() in {"none", "var(--shadow)", "var(--shadow-1)", "var(--shadow-2)"}, (
            value
        )
