"""Starter suggestions on a new conversation: the pure pick, the route, and the chat page."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from starlight.web.suggestions import FALLBACK, MAX_SUGGESTIONS, TABLE, pick_suggestions
from tests.test_chat_app_look import APP
from tests.test_chat_page import SEND, static_site  # noqa: F401  (a fixture)
from tests.test_remote import LOCAL, PHONE, client, home

REPO = Path(__file__).resolve().parent.parent
ALL = [module for module, _ in TABLE]
DAY = date(2026, 11, 14)


def texts(found: list[dict[str, str]]) -> list[str]:
    return [item["text"] for item in found]


def test_every_line_belongs_to_a_module_that_exists() -> None:
    for module in ALL:
        assert (REPO / "modules" / module / "module.yaml").is_file(), module


def test_at_most_four_and_only_from_modules_that_are_on() -> None:
    found = pick_suggestions(ALL, DAY)
    assert len(found) == MAX_SUGGESTIONS
    assert all(item["module"] in ALL for item in found)
    few = pick_suggestions(["weather", "mail"], DAY)
    assert {item["module"] for item in few} <= {"weather", "mail", ""}
    assert "weather" in {item["module"] for item in few}
    assert pick_suggestions(["weather"], DAY)[0] == {
        "text": "What's the weather this week?",
        "module": "weather",
    }


def test_what_can_you_do_fills_up_and_stands_alone_when_nothing_is_on() -> None:
    assert pick_suggestions([], DAY) == [{"text": FALLBACK[1], "module": ""}]
    three = pick_suggestions(["weather", "mail", "todo"], DAY)
    assert len(three) == 4 and three[-1]["text"] == "What can you do?"
    assert "What can you do?" not in texts(pick_suggestions(ALL, DAY))  # (four modules: none)


def test_an_unknown_module_is_ignored() -> None:
    assert pick_suggestions(["no-such-module", "weather"], DAY) == pick_suggestions(
        ["weather"], DAY
    )
    assert pick_suggestions(["no-such-module"], DAY) == pick_suggestions([], DAY)


def test_the_same_day_gives_the_same_four_and_other_days_give_others() -> None:
    assert pick_suggestions(ALL, DAY) == pick_suggestions(list(reversed(ALL)), DAY)
    assert pick_suggestions(ALL, DAY) == pick_suggestions(ALL, DAY)
    week = {tuple(texts(pick_suggestions(ALL, DAY + timedelta(days=n)))) for n in range(7)}
    assert len(week) > 1
    seen = {t for day in week for t in day}
    assert len(seen) > MAX_SUGGESTIONS  # (it turns through the table)


def test_the_route_answers_from_the_modules_that_are_on(tmp_path: Path) -> None:
    with client(home(tmp_path / "h", remote=True)) as c:
        found = c.get("/api/suggestions", headers=LOCAL).json()["suggestions"]
        assert found == [{"text": "What can you do?", "module": ""}]  # (hello has no line)
        c.post("/api/modules/hello", headers=LOCAL, json={"enabled": False})
        assert c.get("/api/suggestions", headers=LOCAL).json()["suggestions"] == found
        assert c.get("/api/suggestions", headers=PHONE).status_code == 403  # (not paired)


def test_the_route_lists_a_module_only_while_it_is_on(tmp_path: Path) -> None:
    root = home(tmp_path / "h", remote=False)
    import shutil

    shutil.copytree(REPO / "modules" / "weather", root / "modules" / "weather")
    with client(root) as c:
        on = c.get("/api/suggestions", headers=LOCAL).json()["suggestions"]
        assert {"text": "What's the weather this week?", "module": "weather"} in on
        c.post("/api/modules/weather", headers=LOCAL, json={"enabled": False})
        off = c.get("/api/suggestions", headers=LOCAL).json()["suggestions"]
        assert all(item["module"] != "weather" for item in off)


# -- the page ------------------------------------------------------------------------------------

FOUR = [
    {"text": "How does my day look?", "module": "briefing"},
    {"text": "What's the weather this week?", "module": "weather"},
    {"text": "Remind me in 2 hours to …", "module": "reminders"},
    {"text": "What is due today?", "module": "todo"},
]
FAKE = (
    "const realFetch = window.fetch; window.suggestionCalls = 0;\n"
    f"window.suggestions = {json.dumps(FOUR)};\n"
    "window.fetch = async (url, ...rest) => {"
    " if (window.c1 && String(url).endsWith('/api/conversations/c1'))"
    " return new Response(JSON.stringify(window.c1),"
    " {headers: {'content-type': 'application/json'}});"
    " if (!String(url).includes('/api/suggestions')) return realFetch(url, ...rest);"
    " window.suggestionCalls++;"
    " if (window.suggestionsFail) return new Response('{}', {status: 500});"
    " return new Response(JSON.stringify({suggestions: window.suggestions}),"
    " {headers: {'content-type': 'application/json'}}); };\n"
)
INIT = APP + FAKE
BUTTONS = "#log .empty .suggestions button.suggest"


async def start(playwright: Any, site: str, before: str = "", **context: Any) -> tuple[Any, Any]:
    from tests.test_feedback_look import browser_page

    browser, page = await browser_page(playwright, site, "chat.html", before + INIT, **context)
    await page.wait_for_selector(".conv")
    return browser, page


async def test_the_suggestions_show_in_a_new_conversation(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await start(playwright, static_site)
        try:
            await page.wait_for_selector(BUTTONS)
            assert await page.locator(BUTTONS).all_inner_texts() == [x["text"] for x in FOUR]
            group = page.locator("#log .empty .suggestions")
            assert await group.get_attribute("role") == "group"
            assert await group.get_attribute("aria-label") == "Suggestions"
            assert await page.locator(BUTTONS).first.get_attribute("type") == "button"
            # none is gold: that is Send's colour
            colours = await page.evaluate(
                "() => [...document.querySelectorAll('.suggest')].map("
                "b => getComputedStyle(b).backgroundColor)"
                ".concat(getComputedStyle(document.documentElement)"
                ".getPropertyValue('--primary'))"
            )
            await page.evaluate(
                "() => { const s = document.createElement('span');"
                " s.id = 'probe'; s.style.background = 'var(--primary)';"
                " document.body.append(s); }"
            )
            gold = await page.evaluate("() => getComputedStyle(probe).backgroundColor")
            assert colours[:-1] and all(c != gold for c in colours[:-1])
            # a conversation with messages shows none
            await page.evaluate("() => addMsg('you', 'Hello')")
            assert await page.locator(".suggest").count() == 0
        finally:
            await browser.close()


async def test_a_conversation_with_messages_shows_none(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    messages = [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]
    before = f"window.c1 = {json.dumps({'private': False, 'messages': messages})};\n"
    async with async_api.async_playwright() as playwright:
        browser, page = await start(playwright, static_site, before)
        try:
            await page.wait_for_selector("#log .msg")
            await page.wait_for_timeout(300)
            assert await page.evaluate("() => window.suggestionCalls") == 0  # (not even asked)
            assert await page.locator(".suggest").count() == 0
        finally:
            await browser.close()


async def test_pressing_one_fills_the_box_selects_the_gap_and_does_not_send(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await start(playwright, static_site)
        try:
            await page.wait_for_selector(BUTTONS)
            await page.evaluate(
                "() => { window.sent = []; window.sockets[0].send = m => sent.push(m); }"
            )
            await page.locator(BUTTONS).nth(2).click()
            box = page.locator("#input")
            assert await box.input_value() == "Remind me in 2 hours to …"
            assert await page.evaluate("() => document.activeElement.id") == "input"
            start_end = await page.evaluate(
                "() => [input.selectionStart, input.selectionEnd, input.value.indexOf('…')]"
            )
            assert start_end[0] == start_end[2] and start_end[1] == start_end[0] + 1
            await page.keyboard.type("buy milk")  # typing replaces the gap
            assert await box.input_value() == "Remind me in 2 hours to buy milk"
            # a text without a gap: the cursor is at the end
            await page.locator(BUTTONS).nth(0).click()
            end = await page.evaluate("() => [input.selectionStart, input.selectionEnd]")
            assert end == [len("How does my day look?")] * 2
            # as typed: the draft is kept and the box is sized
            await page.wait_for_function(
                "() => Object.keys(localStorage).some(k => k.startsWith('starlight-draft:'))"
                " && Object.entries(localStorage).some(([k, v]) => v === 'How does my day look?')"
            )
            assert await page.evaluate("() => window.sent.length") == 0
            assert await page.locator("#log .msg.you").count() == 0  # (nothing was sent)
        finally:
            await browser.close()


async def test_the_suggestions_go_with_the_first_message(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await start(playwright, static_site)
        try:
            await page.wait_for_selector(BUTTONS)
            await page.locator(BUTTONS).nth(3).click()
            await page.keyboard.press("Enter")
            await page.wait_for_selector("#log .msg.you")
            assert await page.locator(".empty").count() == 0
            assert await page.locator(".suggest").count() == 0
        finally:
            await browser.close()


async def test_a_failing_request_shows_none_and_no_message(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await start(playwright, static_site, "window.suggestionsFail = true;\n")
        try:
            await page.wait_for_selector(".empty")
            await page.wait_for_function("() => window.suggestionCalls > 0")
            await page.wait_for_timeout(200)
            assert await page.locator(".suggest").count() == 0
            assert await page.locator(".suggestions").count() == 0
            assert await page.locator(".toast").count() == 0
            assert await page.locator(".empty-title").count() == 1  # (the start itself stays)
        finally:
            await browser.close()


async def test_a_private_conversation_shows_them_too(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    before = "window.c1 = {private: true, messages: []};\n"
    async with async_api.async_playwright() as playwright:
        browser, page = await start(playwright, static_site, before)
        try:
            await page.wait_for_selector(BUTTONS)
            assert await page.locator(".empty-title").inner_text() == "Private conversation"
            assert len(await page.locator(BUTTONS).all_inner_texts()) == 4
        finally:
            await browser.close()


async def test_german_shows_german_texts_and_puts_the_german_text_in_the_box(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await start(
            playwright, static_site, "localStorage.setItem('starlight-lang', 'de');\n"
        )
        try:
            await page.wait_for_selector(BUTTONS)
            await page.wait_for_function(
                "() => document.querySelector('.suggest').textContent === 'Wie sieht mein Tag aus?'"
            )
            assert await page.locator(BUTTONS).all_inner_texts() == [
                "Wie sieht mein Tag aus?",
                "Wie wird das Wetter diese Woche?",
                "Erinnere mich in 2 Stunden an …",
                "Was ist heute fällig?",
            ]
            group = page.locator(".suggestions")
            assert await group.get_attribute("aria-label") == "Vorschläge"
            await page.locator(BUTTONS).nth(2).click()
            assert await page.locator("#input").input_value() == "Erinnere mich in 2 Stunden an …"
            sel = await page.evaluate(
                "() => input.value.slice(input.selectionStart, input.selectionEnd)"
            )
            assert sel == "…"
        finally:
            await browser.close()


async def test_at_360_px_nothing_sticks_out(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    long = [{"text": "What is on my calendar this week? " * 3, "module": "calendar"}, *FOUR]
    before = f"window.suggestionsLong = {json.dumps(long)};\n"
    async with async_api.async_playwright() as playwright:
        browser, page = await start(
            playwright,
            static_site,
            before + "localStorage.setItem('starlight-lang', 'de');\n",
            viewport={"width": 360, "height": 740},
        )
        try:
            await page.wait_for_selector(BUTTONS)
            await page.evaluate(
                "() => { window.suggestions = window.suggestionsLong; showEmpty(false); }"
            )
            await page.wait_for_function("() => document.querySelectorAll('.suggest').length > 4")
            assert await page.evaluate("() => document.documentElement.scrollWidth") <= 360
            fits = await page.evaluate(
                "() => [...document.querySelectorAll('.suggest')].every("
                "b => b.getBoundingClientRect().right <= 360"
                " && b.getBoundingClientRect().left >= 0)"
            )
            assert fits
        finally:
            await browser.close()


def test_no_suggestion_text_lacks_a_german_entry() -> None:
    source = (REPO / "starlight/web/static/i18n.js").read_text(encoding="utf-8")
    block = source.split("// starter suggestions on a new conversation", 1)[1]
    block = block.split("// the chat in the Night Sky look", 1)[0]
    keys = set(re.findall(r'^\s*"(.+?)":', block, re.M))
    for _, text in (*TABLE, FALLBACK):
        assert text in keys, text
    assert "Suggestions" in keys
