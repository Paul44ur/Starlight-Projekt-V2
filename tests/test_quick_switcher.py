"""The quick switcher (quick.js, Ctrl+K): one box to jump to a conversation, a note, a page or a
command, on every page. The chat page here runs with stand-ins for the server; the Notes page
runs against the real app."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from starlight.web.app import STATIC
from tests.test_chat_app_look import APP, LIST, STATUS
from tests.test_chat_page import SEND, static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import browser_page
from tests.test_notes_page import app, notebook, notes_page, opened  # noqa: F401  (fixtures)
from tests.test_remote import LOCAL, client, home

CONVERSATIONS = [  # (the server lists the pinned one first, whatever its date)
    {"id": "k9", "title": "Old pinned", "updated": "2026-11-01T10:00:00", "pinned": True},
    {"id": "k1", "title": "Plan the week", "updated": "2026-11-09T10:00:00"},
    {"id": "k2", "title": "Server check", "updated": "2026-11-08T10:00:00"},
    {"id": "k3", "title": "Recipe for Sunday", "updated": "2026-11-07T10:00:00"},
    {"id": "k4", "title": "Memory", "updated": "2026-11-06T10:00:00"},
    {"id": "k5", "title": "Secret plan", "updated": "2026-11-05T10:00:00", "private": True},
    {"id": "k6", "title": "Holiday", "updated": "2026-11-04T10:00:00"},
    {"id": "k7", "title": "Budget", "updated": "2026-11-03T10:00:00"},
    {"id": "k8", "title": "Garden", "updated": "2026-11-02T10:00:00"},
]
# On top of the chat page's stand-ins: a recording fetch. A conversation search answers with one
# row that names the text it was asked for (after window.delays[text] milliseconds), the notes
# search likewise; /api/status says whether this is the PC (window.PC_LOCAL).
WRAP = (
    "(() => {\n"
    f"const CONVERSATIONS = {json.dumps(CONVERSATIONS)};\n"
    "window.calls = []; window.delays = {}; window.failNotes = false;\n"
    "const stub = window.fetch;\n"
    "const json = (b, status = 200) => new Response(JSON.stringify(b),"
    " {status, headers: {'content-type': 'application/json'}});\n"
    "window.fetch = async (url, init = {}) => {\n"
    "  const u = String(url), q = new URL(u, location.href).searchParams;\n"
    "  window.calls.push([init.method || 'GET', u, init.body || '']);\n"
    "  const text = q.get('search') || q.get('q');\n"
    "  if (text) await new Promise(r => setTimeout(r, window.delays[text] || 0));\n"
    "  const title = text && text.startsWith('<') ? text : 'hit for ' + text;\n"
    "  if (u.includes('/api/notes/search')) return window.failNotes ? json({}, 500)\n"
    "    : json({hits: [{path: 'Projects/' + text + '.md', title: 'Note ' + text}]});\n"
    "  if (u.includes('/api/conversations?') && text) return json({current: 'k1',"
    " conversations: [{id: 's-' + text, title, updated: '', folder: 'Work'}]});\n"
    "  if (u.endsWith('/api/conversations') || u.endsWith('/api/conversations?search='))"
    " { if ((init.method || 'GET') === 'GET')"
    " return json({current: 'k1', conversations: CONVERSATIONS}); }\n"
    "  if (u.endsWith('/api/conversations/nope')) return json({detail: 'no such'}, 404);\n"
    "  if (u.includes('/api/status')) return json({...JSON.parse(JSON.stringify("
    "await (await stub(url, init)).json())), local: window.PC_LOCAL !== false});\n"
    "  return stub(url, init);\n"
    "};\n"
    "})();\n"
)
ROWS = "() => [...document.querySelectorAll('#quick .quick-row')].map(r => r.textContent)"
HEADS = "() => [...document.querySelectorAll('#quick .quick-head')].map(r => r.textContent)"
MARKED = "() => document.querySelector('#quick .quick-row[aria-selected=true]')?.textContent"


async def chat(
    playwright: Any, site: str, extra: str = "", url: str = "chat.html", **ctx: Any
) -> Any:
    browser, page = await browser_page(playwright, site, url, extra + APP + WRAP, **ctx)
    await page.wait_for_selector(".conv")
    return browser, page


async def ready(page: Any, text: str = "") -> None:
    """Opens the switcher with Ctrl+K, optionally types, and waits for the answers."""
    await page.keyboard.press("Control+k")
    await page.wait_for_selector("#quick[open]")
    if text:
        await page.keyboard.type(text)


def api() -> Any:
    return pytest.importorskip("playwright.async_api")


async def test_ctrl_k_opens_it_with_the_focus_in_the_box_and_lists_the_groups(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with api().async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            assert await page.locator("#quick").count() == 0  # (made when first needed)
            await ready(page)
            assert await page.evaluate("() => document.activeElement.closest('#quick') !== null")
            assert await page.evaluate("() => document.activeElement.tagName") == "INPUT"
            await page.wait_for_function(
                "() => document.querySelectorAll('#quick .quick-row').length > 10"
            )
            assert await page.evaluate(HEADS) == [
                "Conversations",
                "Pages",
                "Commands",
            ]  # (no notes)
            rows = await page.evaluate(ROWS)
            # at most six conversations, the newest first (the pinned old one is not first)
            assert rows[:6] == ["Plan the week", "Server check", "Recipe for Sunday", "Memory",
                                "Secret plan", "Holiday"]  # fmt: skip
            assert rows[6:] == ["Chat", "Notes", "Memory", "Activity", "Automations", "Settings",
                                "New conversation", "Private conversation", "Change the theme",
                                "Keyboard shortcuts"]  # fmt: skip
            lock = page.locator("#quick .quick-row", has_text="Secret plan").locator("svg")
            assert 'y="11"' in await lock.inner_html()  # (the lock icon)
            assert await page.locator("#quick .quick-row svg").count() == len(rows)
            assert await page.evaluate(MARKED) == "Plan the week"
            await page.keyboard.press("Escape")
            assert await page.locator("#quick[open]").count() == 0
            await page.keyboard.press("Control+k")  # (and again)
            assert await page.locator("#quick[open]").count() == 1
            await page.keyboard.press("Escape")
            await page.evaluate("() => { askYesNo('Sure?'); }")
            await page.keyboard.press("Control+k")  # not while a question is open
            assert await page.locator("#quick[open]").count() == 0
            await page.keyboard.press("Escape")
            await page.evaluate("() => showShortcuts()")
            await page.keyboard.press("Control+k")  # nor while the shortcuts are shown
            assert await page.locator("#quick[open]").count() == 0
            await page.keyboard.press("Escape")
            await page.keyboard.press("Meta+k")  # (a Mac)
            assert await page.locator("#quick[open]").count() == 1
        finally:
            await browser.close()


async def test_typing_filters_pages_and_commands_and_asks_the_server(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with api().async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            await ready(page)
            await page.wait_for_function(
                "() => document.querySelectorAll('#quick .quick-row').length > 10"
            )
            await page.evaluate("() => { window.calls.length = 0; }")
            await page.keyboard.type("set")
            await page.wait_for_function(
                "() => document.querySelector('#quick')?.textContent.includes('hit for set')"
            )
            assert await page.evaluate(HEADS) == ["Conversations", "Notes", "Pages"]
            assert await page.evaluate(ROWS) == ["hit for setWork", "Note setProjects", "Settings"]
            asked = await page.evaluate("() => window.calls.map(c => c[1])")
            # typed quickly: one request for each group, for the whole text
            assert [a for a in asked if "search=" in a] == ["/api/conversations?search=set"]
            assert [a for a in asked if "notes/search" in a] == ["/api/notes/search?q=set"]
            for _ in range(3):
                await page.keyboard.press("Backspace")
            await page.keyboard.type("keyb")  # a command
            await page.wait_for_function(
                "() => document.querySelector('#quick').textContent.includes('Keyboard shortcuts')"
            )
            assert "Keyboard shortcuts" in await page.evaluate(ROWS)
            assert "Settings" not in await page.evaluate(ROWS)
        finally:
            await browser.close()


async def test_arrows_enter_and_clicks_open_the_marked_row(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with api().async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            # (the page's own function is replaced once it is declared)
            await page.evaluate("() => { window.openConversation = (id) => { window.o = id; }; }")
            await ready(page)
            await page.wait_for_function(
                "() => document.querySelectorAll('#quick .quick-row').length > 10"
            )
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("ArrowDown")
            assert await page.evaluate(MARKED) == "Recipe for Sunday"
            await page.keyboard.press("ArrowUp")
            await page.keyboard.press("ArrowUp")
            await page.keyboard.press("ArrowUp")  # (wraps to the last row)
            assert await page.evaluate(MARKED) == "Keyboard shortcuts"
            await page.keyboard.press("ArrowDown")  # (and to the first again)
            assert await page.evaluate(MARKED) == "Plan the week"
            await page.keyboard.press("ArrowDown")
            await page.keyboard.press("Enter")
            assert await page.evaluate("() => window.o") == "k2"
            assert await page.locator("#quick[open]").count() == 0
            # a click opens a row; the theme command works in place
            await ready(page)
            await page.wait_for_function(
                "() => document.querySelectorAll('#quick .quick-row').length > 10"
            )
            await page.locator("#quick .quick-row", has_text="Holiday").click()
            assert await page.evaluate("() => window.o") == "k6"
            await ready(page)
            await page.locator("#quick .quick-row", has_text="Change the theme").click()
            assert await page.evaluate("() => document.documentElement.dataset.theme") == "light"
            assert await page.locator("#quick[open]").count() == 0
            # the commands that start a conversation press the chat's own buttons
            await page.evaluate("() => { window.calls.length = 0; }")
            await ready(page, "private conv")
            await page.locator("#quick .quick-row", has_text="Private conversation").click()
            await page.wait_for_function("() => window.calls.some(c => c[0] === 'POST')")
            assert await page.evaluate("() => window.calls.filter(c => c[0] === 'POST')[0][2]") == (
                '{"private":true}'
            )
            # a click beside the window closes it
            await ready(page)
            await page.mouse.click(5, 5)
            assert await page.locator("#quick[open]").count() == 0
            # a page: the address changes
            await ready(page, "mem")
            await page.wait_for_function(
                "() => [...document.querySelectorAll('#quick .quick-row .q-t')]"
                ".some(n => n.textContent === 'Memory')"
            )
            await page.locator("#quick .quick-row", has_text="Memory").last.click()
            await page.wait_for_url("**/memory")
        finally:
            await browser.close()


async def test_a_conversation_from_another_page_opens_the_chat_on_it(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with api().async_playwright() as playwright:
        browser, page = await browser_page(playwright, static_site, "memory.html", WRAP_ONLY)
        try:
            await ready(page, "week")
            await page.wait_for_function(
                "() => document.querySelector('#quick').textContent.includes('hit for week')"
            )
            await page.keyboard.press("Enter")
            await page.wait_for_url("**/?c=s-week")  # (the chat page then reads it)
        finally:
            await browser.close()


# (a page that has no chat in it: the same stand-ins, without the chat's WebSocket)
WRAP_ONLY = (
    f"const LIST = {json.dumps(LIST)}, STATUS = {json.dumps(STATUS)};\n"
    "window.fetch = async (url) => { const u = String(url);"
    " const body = u.includes('/api/conversations') ? LIST : STATUS;"
    " return new Response(JSON.stringify(body),"
    " {headers: {'content-type': 'application/json'}}); };\n"
) + WRAP


async def test_the_chat_opens_the_conversation_named_in_its_address_once(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with api().async_playwright() as playwright:
        browser, page = await chat(playwright, static_site, url="chat.html?c=k3")
        try:
            await page.wait_for_function("() => conversation === 'k3'")
            assert await page.evaluate("() => location.search") == ""  # (taken out of the address)
        finally:
            await browser.close()
        # an id that does not exist: the chat as usual (the current conversation)
        browser, page = await chat(playwright, static_site, url="chat.html?c=nope")
        try:
            await page.wait_for_function("() => conversation === 'k1'")
            assert await page.evaluate("() => location.search") == ""
        finally:
            await browser.close()
        # a command from another page: a new conversation, private when asked
        browser, page = await chat(playwright, static_site, url="chat.html?new=private")
        try:
            await page.wait_for_function("() => window.calls.some(c => c[0] === 'POST')")
            post = await page.evaluate("() => window.calls.find(c => c[0] === 'POST')")
            assert post[1] == "/api/conversations" and post[2] == '{"private":true}'
            assert await page.evaluate("() => location.search") == ""
        finally:
            await browser.close()


async def test_esc_closes_it_and_does_not_stop_an_answer(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with api().async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            await page.evaluate(SEND, {"type": "start", "conversation": "k1"})
            await ready(page)
            await page.keyboard.press("Escape")
            assert await page.locator("#quick[open]").count() == 0
            await page.wait_for_timeout(200)
            assert await page.locator(".toast").count() == 0  # no "Stopping"
            await page.keyboard.press("Escape")  # (the page's own Esc still stops it)
            await page.wait_for_selector(".toast")
            assert "Stopping" in await page.locator(".toast").inner_text()
        finally:
            await browser.close()


async def test_a_late_answer_for_an_older_text_does_not_replace_a_newer_one(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with api().async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            await page.evaluate("() => { window.delays = {pl: 900, ple: 0, pla: 0}; }")
            await ready(page, "pl")
            await page.wait_for_function("() => window.calls.some(c => c[1].includes('search=pl'))")
            await page.keyboard.type("a")
            await page.wait_for_function(
                "() => document.querySelector('#quick').textContent.includes('hit for pla')"
            )
            await page.wait_for_timeout(1200)  # (the answer for "pl" has arrived by now)
            rows = await page.evaluate(ROWS)
            assert "hit for plaWork" in rows and "hit for plWork" not in rows
            assert "Note plaProjects" in rows and "Note plProjects" not in rows
            # a failing group shows nothing, without a message
            await page.evaluate("() => { window.failNotes = true; }")
            await page.keyboard.type("x")
            await page.wait_for_function(
                "() => document.querySelector('#quick').textContent.includes('hit for plax')"
            )
            assert await page.evaluate(HEADS) == ["Conversations"]
            assert await page.locator(".toast").count() == 0
        finally:
            await browser.close()


async def test_notes_are_not_offered_on_a_phone(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with api().async_playwright() as playwright:
        browser, page = await chat(playwright, static_site, "window.PC_LOCAL = false;")
        try:
            await ready(page)
            await page.wait_for_function(
                "() => document.querySelectorAll('#quick .quick-row').length > 8"
            )
            assert "Notes" not in await page.evaluate(ROWS)  # (the page)
            await page.keyboard.type("no")
            await page.wait_for_function(
                "() => document.querySelector('#quick').textContent.includes('hit for no')"
            )
            assert "Notes" not in await page.evaluate(HEADS)
            calls = await page.evaluate("() => window.calls.map(c => c[1])")
            assert not [c for c in calls if "/api/notes" in c]  # (not even asked)
        finally:
            await browser.close()


async def test_titles_are_shown_as_text_and_marked_as_not_to_translate(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with api().async_playwright() as playwright:
        browser, page = await chat(playwright, static_site)
        try:
            bad = '<img src=x onerror="window.pwned=1">'
            await ready(page, bad)
            await page.wait_for_function(
                "(bad) => [...document.querySelectorAll('#quick .q-t')]"
                ".some(n => n.textContent === bad)",
                arg=bad,
            )
            await page.wait_for_timeout(300)
            assert await page.locator("#quick img").count() == 0
            assert await page.evaluate("() => window.pwned") is None
            titles = page.locator("#quick .quick-row .q-t[translate=no]")
            assert await titles.count() >= 2  # (the conversation and the note)
        finally:
            await browser.close()


async def test_in_german_headings_and_rows_speak_german_but_titles_stay(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with api().async_playwright() as playwright:
        browser, page = await chat(playwright, static_site, locale="de-DE")
        try:
            await ready(page)
            await page.wait_for_function(
                "() => document.querySelectorAll('#quick .quick-row').length > 10"
            )
            await page.wait_for_function(
                "() => document.querySelector('#quick .quick-head').textContent === 'Gespräche'"
            )
            assert await page.evaluate(HEADS) == ["Gespräche", "Seiten", "Befehle"]
            rows = await page.evaluate(ROWS)
            assert "Memory" in rows[:6]  # a conversation called Memory stays as it is
            assert rows[6:] == ["Chat", "Notizen", "Gedächtnis", "Aktivität", "Automationen",
                                "Einstellungen", "Neues Gespräch", "Privates Gespräch",
                                "Farben wechseln", "Tastenkürzel"]  # fmt: skip
            placeholder = await page.locator("#quick input").get_attribute("placeholder")
            assert placeholder == "Suchen oder springen zu ..."
            await page.keyboard.type("gedä")  # filtered on the text as shown
            await page.wait_for_function(
                "() => document.querySelector('#quick').textContent.includes('hit for gedä')"
            )
            assert await page.evaluate(HEADS) == ["Gespräche", "Notizen", "Seiten"]
            assert (await page.evaluate(ROWS))[-1] == "Gedächtnis"
        finally:
            await browser.close()


async def test_on_the_notes_page_ctrl_k_stays_the_notes_finder(
    static_site: str,  # noqa: F811
    app: Any,  # noqa: F811
    notebook: Path,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html")
        await page.keyboard.press("Control+k")
        await page.wait_for_selector("#switcher:not([hidden])")
        assert await page.locator("#quick[open]").count() == 0
        await page.keyboard.press("Escape")
        await page.keyboard.press("Control+Shift+k")
        await page.wait_for_selector("#quick[open]")
        assert await page.locator("#switcher").is_hidden()  # (the notes finder stays shut)
        await page.keyboard.type("shop")
        await page.wait_for_function(
            "() => [...document.querySelectorAll('#quick .quick-head')]"
            ".some(h => h.textContent === 'Notes')"
        )
        notes = await page.evaluate(
            "() => [...document.querySelectorAll('#quick .quick-row')].map(r => r.textContent)"
        )
        assert any(row.startswith("Shopping") for row in notes)
        await page.keyboard.press("Escape")
        assert await page.locator("#quick[open]").count() == 0
        assert await page.locator("#switcher").is_hidden()
        assert ["Ctrl+Shift+K", "Jump to a conversation, a note or a page"] in (
            await page.evaluate("() => window.SHORTCUTS")
        )
        assert page.errors == []  # type: ignore[attr-defined]


def test_every_page_loads_the_switcher_and_the_app_serves_it(tmp_path: Path) -> None:
    for name in ("chat", "notes", "memory", "activity", "automations", "settings"):
        html = (STATIC / f"{name}.html").read_text("utf-8")
        scripts = re.findall(r'<script src="/([^"]+)"></script>', html)
        assert scripts.index("quick.js") == scripts.index("common.js") + 1, name
    source = (STATIC / "quick.js").read_text("utf-8")
    assert "innerHTML" not in source  # (titles are never put in as HTML)
    with client(home(tmp_path / "h", remote=False)) as c:
        served = c.get("/quick.js", headers=LOCAL)
        assert served.status_code == 200 and served.text == source
        assert served.headers["content-type"].startswith("text/javascript")
        assert '"/quick.js"' in c.get("/sw.js", headers=LOCAL).text  # (kept for the phone)
        for name in ("chat-core.js", "notes-find.js"):  # (each page lists the key for ?)
            assert "Jump to a conversation, a note or a page" in (STATIC / name).read_text("utf-8")
