"""The chat page in the Night Sky look (UI plan block V2): the sidebar as a card with the gold
New conversation button and a search button for the quick switcher, the tool steps folded into
one chip, the details pane (from 1200 px) with "This answer", the keys shown only where there
is room, nothing sideways on a phone, and the German texts."""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_chat_app_look import open_app
from tests.test_chat_page import SEND, static_site  # noqa: F401  (a fixture)

STATS = {"seconds": 4.1, "tokens": 212, "tokens_per_second": 51.7, "context_tokens": 8800}
NONE = "\u2013"  # (a dash: no answer yet)
WIDE = {"width": 1400, "height": 860}
TOKEN = (
    "(name) => { const probe = document.createElement('i');"
    " probe.style.background = 'var(' + name + ')'; document.body.append(probe);"
    " const colour = getComputedStyle(probe).backgroundColor; probe.remove(); return colour; }"
)


async def answer_with_tools(page: Any) -> None:
    await page.evaluate("() => addMsg('you', 'Is my web server up?')")
    await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
    for tool, ms in (("web.check", 182), ("log.read", 40)):
        await page.evaluate(SEND, {"type": "tool", "tool": tool, "state": "running"})
        await page.evaluate(SEND, {"type": "tool", "tool": tool, "state": "done", "ms": ms})
    done = {"type": "answer", "text": "Yes, it is up.", "conversation": "c1", "stats": STATS}
    await page.evaluate(SEND, done)


async def test_the_sidebar_is_a_card_and_new_conversation_is_gold(
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
            assert box is not None and box["x"] == 12 and box["y"] == 12  # inset on the ground
            assert await side.evaluate("s => getComputedStyle(s).borderRadius") == "16px"
            panel = await page.evaluate(TOKEN, "--panel")
            assert await side.evaluate("s => getComputedStyle(s).backgroundColor") == panel
            gold = await page.evaluate(TOKEN, "--primary")
            new = page.locator("#new")
            assert await new.evaluate("b => getComputedStyle(b).backgroundColor") == gold
            private = page.locator("#newprivate")
            assert await private.evaluate("b => getComputedStyle(b).backgroundColor") != gold
            assert await page.locator(".side-brand .brand-star svg.ico .star").count() == 1
            assert await page.locator(".brand-name").inner_text() == "Starlight"
            # the open conversation: --panel2 behind it and a violet dot, no bar at its edge
            row = page.locator(".conv.active")
            panel2 = await page.evaluate(TOKEN, "--panel2")
            assert await row.evaluate("r => getComputedStyle(r).backgroundColor") == panel2
            assert await row.evaluate("r => getComputedStyle(r).boxShadow") == "none"
            dot = await row.evaluate("r => getComputedStyle(r, '::before').backgroundColor")
            assert dot == await page.evaluate(TOKEN, "--accent")
            assert await row.evaluate("r => r.offsetHeight") >= 44
            # the section labels
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


async def test_the_tool_steps_fold_into_one_chip_that_opens_the_timeline(
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
            timeline = page.locator(".msg.bot .toolrow")
            assert await chip.get_attribute("aria-expanded") == "false"
            assert not await timeline.is_visible()  # (the steps wait behind the chip)
            await chip.click()
            assert await chip.get_attribute("aria-expanded") == "true"
            assert await timeline.is_visible()
            assert await page.locator(".toolstep").all_inner_texts() == [
                "✓ web.check 182 ms",
                "✓ log.read 40 ms",
            ]
            await chip.click()
            assert await chip.get_attribute("aria-expanded") == "false"
            assert not await timeline.is_visible()
            # Starlight's answer has no card; a small gold star stands before it
            assert (
                await page.locator(".msg.bot").evaluate("m => getComputedStyle(m).backgroundColor")
                == "rgba(0, 0, 0, 0)"
            )
            assert await page.locator(".msg.bot .who svg.ico .star").count() == 1
            assert await page.locator(".msg.bot .att").evaluate("a => a.classList.contains('num')")
        finally:
            await browser.close()


async def test_the_details_pane_shows_this_answer_from_1200_px(
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
                await answer_with_tools(page)
                assert await page.locator("#side").is_visible() == shown, width
                if not shown:
                    continue
                values = page.locator("#answer-tools, #answer-time, #answer-tokens")
                assert await values.all_inner_texts() == ["2", "4.1 s", "212"]
                assert await page.locator("#side h2").all_inner_texts() == [
                    "THIS PC", "CONTEXT", "THIS ANSWER", "ALLOWED SERVERS", "LOGINS", "SECRETS",
                    "SAVED FILES",
                ]  # fmt: skip
                assert await page.locator("#context-of").inner_text() == "8.8k of 32.8k"
                assert await page.locator("#side h2.section-label").count() == 7
                # the bar's meter: the same percentage, in mono figures
                assert await page.locator("#bar-context-pct").inner_text() == "27%"
                assert (
                    await page.locator("#bar-context-fill").evaluate("i => i.style.width") == "27%"
                )
                # another conversation: nothing of the last answer is left
                await page.locator(".conv", has_text="Server check").click()
                await page.wait_for_function("() => conversation === 'c3'")
                assert await values.all_inner_texts() == [NONE] * 3
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
                assert await page.locator("#send-key").is_visible() == shown, width
                if shown:
                    assert await kill.inner_text() == "Ctrl Alt J"
                    assert await page.locator("#send-key").inner_text() == "Enter"
                    assert await page.locator("#kill").evaluate(
                        "k => getComputedStyle(k).borderTopColor"
                    ) == await page.evaluate(TOKEN, "--bad")
                    send = await page.locator("#send").bounding_box()
                    assert send is not None and send["width"] == send["height"] == 44  # round
                    gold = await page.evaluate(TOKEN, "--primary")
                    assert (
                        await page.locator("#send").evaluate(
                            "b => getComputedStyle(b).backgroundColor"
                        )
                        == gold
                    )
                    form = page.locator("#form")
                    assert await form.evaluate("f => getComputedStyle(f).borderRadius") == "26px"
                bar = await page.locator("#main header.top").bounding_box()
                assert bar is not None and bar["height"] < 70, width  # (still one row)
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
            await page.locator("#menu").click()  # the drawer works as before
            assert await page.locator("#quick-open").is_visible()
            assert not await page.locator("#quick-open kbd").is_visible()  # (no keyboard)
            assert await page.evaluate("() => document.documentElement.scrollWidth") <= 360
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
            playwright, static_site, "localStorage.setItem('starlight-lang', 'de');\n",
            viewport=WIDE,
        )  # fmt: skip
        try:
            await page.wait_for_function("() => contextWindow === 32768")
            await answer_with_tools(page)
            await page.wait_for_function(
                "() => document.querySelector('.toolsum .ts-text').textContent !== 'Used 2 tools'"
            )
            assert await page.locator(".toolsum .ts-text").inner_text() == "2 Werkzeuge benutzt"
            assert "Suchen oder springen" in await page.locator("#quick-open").inner_text()
            assert await page.locator("#quick-open kbd").inner_text() == "Strg K"
            assert await page.locator("#kill kbd").inner_text() == "Strg Alt J"
            labels = await page.locator("#side h2").all_inner_texts()
            assert "DIESE ANTWORT" in labels
            rows = await page.locator("#side .kv > :first-child").all_inner_texts()
            assert rows[1:] == ["Werkzeuge", "Zeit", "Token"]
            assert await page.locator("#bar-context").get_attribute("title") == (
                "Wie voll der Kontext des Modells ist"
            )
        finally:
            await browser.close()
