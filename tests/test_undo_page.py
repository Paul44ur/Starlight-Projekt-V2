"""Undo for delete and archive (F2) on the chat page: no question, a toast with Undo for a few
seconds (``window.undoSeconds`` shortens the time in tests), Ctrl+Z, and German."""

from __future__ import annotations

import copy
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from tests.test_chat_app_look import LIST, open_app
from tests.test_chat_page import static_site  # noqa: F401  (a fixture)

# A server in the page: it keeps the list, moves a deleted conversation aside and brings it
# back, records every call that changes something, and answers a new conversation with "c9".
SERVER = """(list) => { window.calls = []; window.waste = {}; window.gone = [];
  window.messages = {};
  const real = window.fetch, rows = list.conversations;
  const type = {'content-type': 'application/json'};
  const json = (body) => new Response(JSON.stringify(body), {headers: type});
  window.fetch = async (url, init) => {
    const u = new URL(String(url), location.href), method = (init && init.method) || 'GET';
    const id = u.pathname.split('/')[3], body = init && init.body ? JSON.parse(init.body) : null;
    if (method !== 'GET') window.calls.push([method, u.pathname, body]);
    if (method === 'DELETE' && u.searchParams.get('empty')) {  // for good, only an empty one
      const at = rows.findIndex(c => c.id === id);
      if (at < 0 || (window.messages[id] || []).length) return new Response('{}', {status: 404});
      rows.splice(at, 1); window.gone.push(id); return json({deleted: id}); }
    if (method === 'DELETE') {
      window.waste[id] = rows.splice(rows.findIndex(c => c.id === id), 1)[0];
      return json({deleted: id}); }
    if (method === 'POST' && u.pathname.endsWith('/restore')) { rows.push(window.waste[id]);
      return json({id}); }
    if (method === 'POST' && u.pathname === '/api/conversations') {
      rows.push({id: 'c9', title: 'New conversation', updated: '', count: 0});
      return json({id: 'c9'}); }
    if (method === 'PATCH') {
      Object.assign(rows.find(c => c.id === id), body);
      return json({id, ...body}); }
    if (u.pathname === '/api/conversations') {
      const archived = u.searchParams.get('archived') === 'true';
      return json({current: 'c1', conversations: rows.filter(c => !!c.archived === archived)}); }
    if (/^\\/api\\/conversations\\/\\w+$/.test(u.pathname)) {
      const c = rows.find(c => c.id === id) || {title: 'New conversation'};
      return json({private: false, messages: [], title: c.title, pinned: !!c.pinned}); }
    return real(url, init); }; }"""


@asynccontextmanager
async def chat(
    site: str,
    monkeypatch: pytest.MonkeyPatch,
    extra: str = "",
    titles: dict[str, str] | None = None,
) -> AsyncIterator[Any]:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    shown = copy.deepcopy(LIST)
    for row in shown["conversations"]:
        row["title"] = (titles or {}).get(row["id"], row["title"])
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, site, extra)
        try:
            await page.evaluate(SERVER, shown)
            await page.evaluate("() => loadList()")
            yield page
        finally:
            await browser.close()


async def pick(page: Any, title: str, action: str) -> None:
    row = page.locator(".conv", has_text=title)
    await row.hover()
    await row.locator(".acts button").click()
    await page.locator(".popmenu button", has_text=action).click()


async def calls(page: Any) -> list[Any]:
    return await page.evaluate("() => window.calls")  # type: ignore[no-any-return]


async def test_delete_asks_nothing_and_undo_brings_the_conversation_back(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        dialogs: list[str] = []
        page.on("dialog", lambda dialog: dialogs.append(dialog.message))
        await pick(page, "Recipe for Sunday", "Delete")
        toast = page.locator(".toast")
        await toast.wait_for()
        assert await page.locator("dialog.ask").count() == 0 and dialogs == []
        assert await calls(page) == [["DELETE", "/api/conversations/c2", None]]
        assert await toast.locator("span").inner_text() == 'Deleted "Recipe for Sunday".'
        assert await toast.get_attribute("role") == "status"
        assert await toast.locator("button").inner_text() == "Undo"
        assert await page.locator(".conv", has_text="Recipe for Sunday").count() == 0
        assert await page.evaluate("() => document.activeElement === document.body")  # no jump
        await toast.locator("button").click()
        await page.wait_for_selector(".conv:has-text('Recipe for Sunday')")
        assert (await calls(page))[1] == ["POST", "/api/conversations/c2/restore", None]
        await page.wait_for_selector(".toast", state="detached")


async def test_undo_reopens_the_open_conversation_and_keeps_its_draft(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await page.fill("#input", "half a sentence")
        await pick(page, "Plan the week", "Delete")
        await page.wait_for_selector(".toast")
        assert [c[0] for c in await calls(page)] == ["DELETE", "POST"]
        assert await page.evaluate("() => localStorage.getItem('starlight-draft:c1')") == (
            "half a sentence"
        )  # kept while Undo is possible
        assert await page.evaluate("() => conversation") == "c9"
        await page.locator(".toast button").click()
        await page.wait_for_selector(".conv.active:has-text('Plan the week')")
        assert await page.input_value("#input") == "half a sentence"
        assert await page.evaluate("() => conversation") == "c1"


async def test_undo_removes_the_empty_conversation_that_opened_in_its_place(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await pick(page, "Plan the week", "Delete")
        await page.wait_for_selector(".toast")
        await page.wait_for_selector(".conv:has-text('New conversation')")
        await page.locator(".toast button").click()
        await page.wait_for_selector(".conv.active:has-text('Plan the week')")
        await page.wait_for_function("() => window.gone.length === 1")
        assert await page.evaluate("() => window.gone") == ["c9"]
        assert await page.locator(".conv", has_text="New conversation").count() == 0
        assert await page.evaluate("() => conversation") == "c1"
        assert await page.locator(".toast").count() == 0  # (quietly)
        got = await calls(page)
        assert got[2] == ["POST", "/api/conversations/c1/restore", None]
        assert got[3] == ["DELETE", "/api/conversations/c9", None]
        assert await page.evaluate("() => Object.keys(window.waste)") == ["c1"]  # c9 not in it


async def test_undo_keeps_the_new_conversation_when_it_has_a_draft(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await pick(page, "Plan the week", "Delete")
        await page.wait_for_selector(".toast")
        await page.wait_for_selector(".conv:has-text('New conversation')")
        await page.fill("#input", "something new")
        await page.locator(".toast button").click()
        await page.wait_for_selector(".conv.active:has-text('Plan the week')")
        await page.wait_for_timeout(300)
        assert await page.evaluate("() => window.gone") == []
        assert await page.locator(".conv", has_text="New conversation").count() == 1


async def test_when_the_time_is_over_the_toast_goes_and_the_draft_is_dropped(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch, "window.undoSeconds = 0.6;\n") as page:
        await page.fill("#input", "half a sentence")
        await pick(page, "Plan the week", "Delete")
        await page.wait_for_selector(".toast")
        await page.wait_for_selector(".toast", state="detached")
        assert await page.evaluate("() => localStorage.getItem('starlight-draft:c1')") is None
        assert [c[0] for c in await calls(page)] == ["DELETE", "POST"]  # (nothing else was sent)


async def test_archive_is_at_once_with_undo_in_the_menu_and_as_a_command(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await pick(page, "Recipe for Sunday", "Archive")
        toast = page.locator(".toast")
        await toast.wait_for()
        assert await calls(page) == [["PATCH", "/api/conversations/c2", {"archived": True}]]
        assert await toast.locator("span").inner_text() == 'Archived "Recipe for Sunday".'
        assert await page.locator(".conv", has_text="Recipe for Sunday").count() == 0
        await toast.locator("button").click()
        await page.wait_for_selector(".conv:has-text('Recipe for Sunday')")
        assert (await calls(page))[1] == ["PATCH", "/api/conversations/c2", {"archived": False}]
        # the /archive command: the open conversation
        await page.fill("#input", "/archive")
        await page.press("#input", "Enter")
        await page.wait_for_selector(".toast:has-text('Archived \"Plan the week\".')")
        assert (await calls(page))[2] == ["PATCH", "/api/conversations/c1", {"archived": True}]
        await page.locator(".toast button").click()
        await page.wait_for_function("() => window.calls.length === 4")
        assert (await calls(page))[3] == ["PATCH", "/api/conversations/c1", {"archived": False}]


async def test_a_second_delete_replaces_the_toast_and_the_first_stays_done(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await pick(page, "Recipe for Sunday", "Delete")
        await page.wait_for_selector(".toast:has-text('Recipe for Sunday')")
        await pick(page, "Server check", "Delete")
        await page.wait_for_selector(".toast:has-text('Server check')")
        assert await page.locator(".toast").count() == 1
        await page.locator(".toast button").click()
        await page.wait_for_selector(".conv:has-text('Server check')")
        assert await page.locator(".conv", has_text="Recipe for Sunday").count() == 0
        restores = [c for c in await calls(page) if c[1].endswith("/restore")]
        assert restores == [["POST", "/api/conversations/c3/restore", None]]


async def test_ctrl_z_undoes_but_not_while_typing(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with chat(static_site, monkeypatch) as page:
        await pick(page, "Recipe for Sunday", "Delete")
        await page.wait_for_selector(".toast")
        await page.focus("#input")
        await page.keyboard.press("Control+z")
        await page.wait_for_timeout(300)
        assert len(await calls(page)) == 1 and await page.locator(".toast").count() == 1
        await page.evaluate("() => document.activeElement.blur()")
        await page.keyboard.press("Control+z")
        await page.wait_for_selector(".conv:has-text('Recipe for Sunday')")
        assert (await calls(page))[1][1] == "/api/conversations/c2/restore"
        await page.keyboard.press("Control+z")  # (no toast any more: nothing happens)
        await page.wait_for_timeout(200)
        assert len(await calls(page)) == 2


async def test_german_texts_with_the_name_left_as_it_is(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # (the names are words the dictionary knows: they must stay)
    titles = {"c2": "Archive", "c3": "Settings"}
    async with chat(
        static_site, monkeypatch, "localStorage.setItem('starlight-lang', 'de');\n", titles
    ) as page:
        await pick(page, "Archive", "Löschen")
        toast = page.locator(".toast")
        await toast.wait_for()
        assert await toast.locator("span").inner_text() == "„Archive“ gelöscht."
        assert await toast.locator("button").inner_text() == "Rückgängig"
        await pick(page, "Settings", "Archivieren")
        await page.wait_for_selector(".toast:has-text('archiviert')")
        assert await toast.locator("span").inner_text() == "„Settings“ archiviert."
