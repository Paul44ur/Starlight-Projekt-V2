"""Find in the open conversation (F1): Ctrl+F opens a bar under the top bar, every hit in the
messages' text is marked, Enter and Shift+Enter move through them, Esc closes the bar and the
text is exactly as it was. Nothing goes to the server."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from tests.test_chat_app_look import APP
from tests.test_chat_page import SEND, static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import browser_page

HTML_TEXT = "What is in the Sunday plan? <img src=x onerror=window.pwned=1> and sunday again"
TABLE_ANSWER = (
    "Here is the Sunday plan:\n\n| Day | Task |\n|---|---|\n| Sunday | Cook |\n"
    "| Monday | Rest |\n\n```python\nprint('sunday')\n```\n"
)
FILLER = "\n\n".join(
    f"Paragraph {i}: nothing to see here, " + "just words " * 30 for i in range(30)
)
MESSAGES = [
    {"role": "user", "content": HTML_TEXT},
    {"role": "assistant", "content": FILLER},
    {"role": "assistant", "content": TABLE_ANSWER},
]
HITS = 5  # "sunday" in the messages above: your message 2, the answer 1, its table 1, its code 1
# After APP: conversation c2 has the messages above; every other one is empty. Calls are counted.
FAKE = (
    "const plain = window.fetch; window.calls = [];"
    " window.fetch = async (url, o) => { const u = String(url); window.calls.push(u);"
    " const type = {'content-type': 'application/json'};"
    f" const MESSAGES = {json.dumps(MESSAGES)};"
    " if (u.endsWith('/api/conversations/c2')) return new Response(JSON.stringify("
    "{id: 'c2', title: 'Recipe for Sunday', private: false, messages: MESSAGES}), {headers: type});"
    " return plain(url, o); };"
)
MARKS = "#log mark.find"
LOG = "document.getElementById('log')"


@asynccontextmanager
async def conversation(
    site: str, monkeypatch: pytest.MonkeyPatch, **context: Any
) -> AsyncIterator[tuple[Any, Any]]:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await browser_page(playwright, site, "chat.html", APP + FAKE, **context)
        try:
            await page.wait_for_selector(".conv")
            await page.evaluate("openConversation('c2')")
            await page.wait_for_selector(".msg.bot table")
            yield browser, page
        finally:
            await browser.close()


async def counter(page: Any) -> str:
    return str(await page.inner_text("#find-count"))


async def current_in_view(page: Any) -> bool:
    return bool(
        await page.evaluate(
            "() => { const m = document.querySelector('mark.find.current');"
            " const r = m.getBoundingClientRect(), l = document.getElementById('log')"
            ".getBoundingClientRect(); return r.top >= l.top && r.bottom <= l.bottom; }"
        )
    )


async def type_find(page: Any, text: str) -> None:
    await page.keyboard.press("Control+f")
    await page.fill("#find-input", text)
    await page.wait_for_function("document.querySelector('#find-count').textContent !== ''")


async def test_ctrl_f_opens_the_bar_from_anywhere_and_the_browsers_find_is_prevented(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        assert await page.is_hidden("#find")
        prevented = await page.evaluate(
            "() => { const e = new KeyboardEvent('keydown', {key: 'f', ctrlKey: true,"
            " bubbles: true, cancelable: true}); document.getElementById('input')"
            ".dispatchEvent(e); return e.defaultPrevented; }"
        )
        assert prevented  # (the browser's own find does not open)
        assert await page.is_visible("#find")
        assert await page.evaluate("document.activeElement.id") == "find-input"
        await page.evaluate("document.getElementById('find-input').blur()")
        await page.focus("#input")  # (from the message box too)
        await page.keyboard.press("Control+f")
        assert await page.evaluate("document.activeElement.id") == "find-input"
        await page.fill("#find-input", "abc")
        await page.keyboard.press("Control+f")  # (again: its text is selected)
        selected = await page.evaluate(
            "() => { const f = document.getElementById('find-input');"
            " return [f.selectionStart, f.selectionEnd]; }"
        )
        assert selected == [0, 3]
        assert await page.is_visible("#find")


async def test_the_top_bar_has_a_find_control(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        button = page.locator("header.top #find-open")
        assert await button.get_attribute("title") == "Find in this conversation (Ctrl F)"
        assert await button.locator("svg").count() == 1  # (a line icon)
        await button.click()
        assert await page.is_visible("#find")
        assert await page.evaluate("document.activeElement.id") == "find-input"
        assert await button.get_attribute("aria-expanded") == "true"
        await page.click("#find-close")
        assert await page.is_hidden("#find")
        assert await page.evaluate("document.activeElement.id") == "input"


async def test_typing_marks_every_hit_in_the_messages_only(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        side = await page.evaluate("document.getElementById('convs').innerHTML")
        pane = await page.evaluate("document.getElementById('side').innerHTML")
        await type_find(page, "SUNDAY")  # (case does not matter)
        assert await page.locator(MARKS).count() == HITS
        assert await counter(page) == f"1 of {HITS}"
        assert await page.locator("mark.find.current").count() == 1
        inside = await page.evaluate(
            "[...document.querySelectorAll('mark.find')]"
            ".every(m => m.closest('.msg .body') !== null)"
        )
        assert inside
        for place in ("table td", "pre code"):  # (tables and code blocks too)
            assert await page.locator(f".msg.bot {place} mark.find").count() == 1
        assert await page.evaluate("document.getElementById('convs').innerHTML") == side
        assert await page.evaluate("document.getElementById('side').innerHTML") == pane
        assert (
            await page.locator("#convs mark, #side mark, footer mark, .msghead mark").count() == 0
        )
        text = await page.locator("mark.find").all_inner_texts()
        assert sorted(t.lower() for t in text) == ["sunday"] * HITS


async def test_enter_shift_enter_and_the_arrows_move_and_wrap(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        await type_find(page, "sunday")
        assert await counter(page) == f"1 of {HITS}"
        await page.keyboard.press("Enter")
        assert await counter(page) == f"2 of {HITS}"
        await page.keyboard.press("Shift+Enter")
        await page.keyboard.press("Shift+Enter")  # (before the first: the last)
        assert await counter(page) == f"{HITS} of {HITS}"
        assert await current_in_view(page)
        await page.keyboard.press("Enter")  # (after the last: the first)
        assert await counter(page) == f"1 of {HITS}"
        assert await current_in_view(page)
        await page.click("#find-next")
        await page.click("#find-next")
        assert await counter(page) == f"3 of {HITS}"
        assert await current_in_view(page)
        await page.click("#find-prev")
        assert await counter(page) == f"2 of {HITS}"
        current = await page.locator("mark.find.current").all_inner_texts()
        assert len(current) == 1
        assert await page.locator(MARKS).count() == HITS


async def test_no_hits_says_so_and_an_empty_field_marks_nothing(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        await type_find(page, "zebra")
        assert await counter(page) == "No matches"
        assert await page.locator(MARKS).count() == 0
        await page.keyboard.press("Enter")  # (nothing to go to: no error, no change)
        assert await counter(page) == "No matches"
        await page.fill("#find-input", "sunday")
        await page.wait_for_selector(MARKS)
        await page.fill("#find-input", "")
        await page.wait_for_function("document.querySelectorAll('mark.find').length === 0")
        assert await counter(page) == ""


async def test_esc_closes_the_bar_and_the_text_is_exactly_as_before(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        before = await page.evaluate(f"[{LOG}.textContent, {LOG}.innerHTML]")
        await type_find(page, "sunday")
        assert await page.locator(MARKS).count() == HITS
        await page.keyboard.press("Escape")
        assert await page.is_hidden("#find")
        assert await page.locator(MARKS).count() == 0
        after = await page.evaluate(f"[{LOG}.textContent, {LOG}.innerHTML]")
        assert after == before  # (copying an answer gives the same text as before)
        # a text node is whole again, not left in pieces
        pieces = await page.evaluate(
            "[...document.querySelectorAll('.msg .body')].some(b => [...b.childNodes]"
            ".some((n, i, all) => n.nodeType === 3 && all[i + 1] && all[i + 1].nodeType === 3))"
        )
        assert not pieces
        assert await page.evaluate("document.activeElement.id") == "input"


async def test_esc_with_the_bar_closed_still_stops_an_answer(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        await page.evaluate(
            "() => { window.sent = []; ws.send = m => window.sent.push(JSON.parse(m)); }"
        )
        await page.evaluate(SEND, {"type": "start", "conversation": "c2"})
        await page.evaluate(SEND, {"type": "token", "text": "Sunday is "})
        await page.keyboard.press("Control+f")
        await page.keyboard.press("Escape")  # (closes the bar; the answer goes on)
        assert await page.is_hidden("#find")
        assert await page.evaluate("window.sent") == []
        await page.keyboard.press("Escape")  # (the bar is closed: the old behaviour)
        await page.wait_for_function("window.sent.length === 1")
        assert await page.evaluate("window.sent") == [{"type": "stop"}]


async def test_a_message_that_looks_like_html_is_found_and_stays_text(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        await type_find(page, "<img src=x onerror")
        assert await counter(page) == "1 of 1"
        hit = page.locator("mark.find")
        assert await hit.inner_text() == "<img src=x onerror"
        assert await hit.evaluate("m => m.children.length") == 0
        assert await page.locator(".msg.you img").count() == 0
        assert await page.evaluate("window.pwned") is None
        await page.fill("#find-input", ".*+?(")  # (a pattern is no pattern: just text)
        await page.wait_for_function(
            "document.querySelector('#find-count').textContent === 'No matches'"
        )
        await page.keyboard.press("Escape")
        assert await page.locator(".msg.you img").count() == 0
        body = await page.locator(".msg.you .body").inner_text()
        assert body == HTML_TEXT


async def test_opening_another_conversation_closes_the_bar(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        await type_find(page, "sunday")
        await page.evaluate("openConversation('c3')")
        await page.wait_for_function("document.getElementById('find').hidden")
        assert await page.locator(MARKS).count() == 0


async def test_a_streaming_answer_is_marked_once_it_is_done(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        await type_find(page, "sunday")
        assert await counter(page) == f"1 of {HITS}"
        await page.evaluate(SEND, {"type": "start", "conversation": "c2"})
        for word in ("The ", "Sunday ", "trip ", "is ", "sunday "):
            await page.evaluate(SEND, {"type": "token", "text": word})
        await page.wait_for_function("document.querySelector('.msg.streaming .body').raw")
        await page.wait_for_timeout(100)
        # while it streams, nothing is marked in it and the count stays
        assert await page.locator(".msg.streaming mark.find").count() == 0
        assert await counter(page) == f"1 of {HITS}"
        await page.evaluate(
            SEND, {"type": "answer", "text": "The Sunday trip is sunday", "stats": {}}
        )
        await page.wait_for_function(
            "document.querySelectorAll('.msg.bot:last-of-type mark.find').length === 2"
        )
        assert await counter(page) == f"1 of {HITS + 2}"  # (the place is kept)
        assert await page.locator(MARKS).count() == HITS + 2


async def test_the_labels_are_german(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        script = "localStorage.setItem('starlight-lang', 'de');\n" + APP + FAKE
        browser, page = await browser_page(playwright, static_site, "chat.html", script)
        try:
            await page.wait_for_selector(".conv")
            await page.evaluate("openConversation('c2')")
            await page.wait_for_selector(".msg.bot table")
            title = await page.get_attribute("#find-open", "title")
            assert title == "In diesem Gespräch suchen (Strg F)"
            await page.keyboard.press("Control+f")
            assert await page.get_attribute("#find-input", "placeholder") == (
                "In diesem Gespräch suchen"
            )
            assert await page.get_attribute("#find-prev", "title") == (
                "Voriger Treffer (Umschalt+Enter)"
            )
            assert await page.get_attribute("#find-next", "aria-label") == "Nächster Treffer"
            assert await page.get_attribute("#find-close", "aria-label") == "Suchleiste schließen"
            await page.fill("#find-input", "sunday")
            await page.wait_for_function("document.querySelector('#find-count').textContent")
            assert await counter(page) == f"1 von {HITS}"
            await page.fill("#find-input", "zebra")
            await page.wait_for_function(
                "document.querySelector('#find-count').textContent === 'Keine Treffer'"
            )
            keys = await page.evaluate("window.SHORTCUTS.map(s => s[0] + ' ' + s[1])")
            assert "Ctrl+F Find in this conversation" in keys
        finally:
            await browser.close()


async def test_a_phone_fits_the_bar_with_big_targets(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch, viewport={"width": 360, "height": 700}) as (
        _,
        page,
    ):
        assert await page.evaluate("document.documentElement.scrollWidth") <= 360
        await page.click("#find-open")  # (the control is there for touch)
        await page.fill("#find-input", "sunday")
        await page.wait_for_selector("mark.find.current")
        await page.fill("#find-input", "s")  # (a long counter, many hits)
        await page.wait_for_function("document.querySelector('#find-count').textContent")
        assert await page.evaluate("document.documentElement.scrollWidth") <= 360
        for name in ("#find-prev", "#find-next", "#find-close", "#find-open"):
            box = await page.locator(name).bounding_box()
            assert box is not None
            assert box["width"] >= 44 and box["height"] >= 44, name
            assert box["x"] >= 0 and box["x"] + box["width"] <= 360, name
        field = await page.locator("#find-input").bounding_box()
        assert field is not None and field["width"] >= 80
        assert field["height"] >= 44


async def test_nothing_goes_to_the_server_while_finding(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with conversation(static_site, monkeypatch) as (_, page):
        await page.evaluate("window.calls = []; window.sent = []")
        await page.evaluate("() => { ws.send = m => window.sent.push(m); }")
        await type_find(page, "sunday")
        await page.keyboard.press("Enter")
        await page.click("#find-prev")
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(300)
        assert await page.evaluate("window.calls") == []
        assert await page.evaluate("window.sent") == []


async def test_the_current_hit_is_readable_in_both_looks(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = """(look) => {
      document.documentElement.dataset.theme = look;
      const rgb = (c) => c.match(/[\\d.]+/g).map(Number);
      const lum = ([r, g, b]) => { const f = (v) => { v /= 255;
        return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
      const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
        return (x + 0.05) / (y + 0.05); };
      const mark = document.querySelector('mark.find.current');
      const s = getComputedStyle(mark);
      const other = document.querySelector('mark.find:not(.current)');
      return [ratio(rgb(s.color), rgb(s.backgroundColor)), getComputedStyle(other).backgroundColor,
              s.backgroundColor];
    }"""
    async with conversation(static_site, monkeypatch) as (_, page):
        await type_find(page, "sunday")
        for look in ("light", "dark"):
            ratio, others, current = await page.evaluate(script, look)
            assert ratio >= 4.5, look
            assert others != current  # (the current hit stands out from the rest)
