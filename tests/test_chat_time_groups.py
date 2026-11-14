"""The chat's conversation list grouped by time: Today, Yesterday, Last 7 days and Older, in
place of "Recent". The clock of the page is fixed (Saturday 14 November 2026, 12:00, Berlin),
so the tests do not depend on the day they run."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.test_chat_app_look import APP, CLOCK, LIST
from tests.test_chat_page import static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import browser_page

ZONE = {"timezone_id": "Europe/Berlin"}


def made(
    cid: str, title: str, updated: str | None, folder: str = "", pinned: bool = False
) -> dict[str, Any]:
    row: dict[str, Any] = {"id": cid, "title": title, "folder": folder, "pinned": pinned}
    if updated is not None:
        row["updated"] = updated
    return row


# the server writes local time without a zone, to the microsecond (conversations.py _now)
MINE = {
    "current": "a",
    "conversations": [
        made("pin", "Pinned one", "2026-11-14T09:00:00.000001", pinned=True),
        made("a", "Today late", "2026-11-14T11:59:59.999999"),
        made("b", "Today early", "2026-11-14T00:01:00.000000"),
        made("c", "Yesterday late", "2026-11-13T23:59:00.000000"),
        made("d", "Three days ago", "2026-11-11T08:00:00.000000"),
        made("e", "Eight days ago", "2026-11-06T08:00:00.000000"),
        made("f", "Broken date", "not a date"),
        made("g", "No date", None),
        made("h", "In a folder", "2026-11-14T10:00:00.000000", folder="Work"),
    ],
}


def script(listing: dict[str, Any] = MINE) -> str:
    own = APP.replace(f"const LIST = {json.dumps(LIST)}", f"const LIST = {json.dumps(listing)}")
    assert own != APP
    return CLOCK + own


async def open_list(playwright: Any, site: str, listing: dict[str, Any] = MINE, **kw: Any) -> Any:
    kw = ZONE | kw
    extra = kw.pop("init", "")
    browser, page = await browser_page(playwright, site, "chat.html", extra + script(listing), **kw)
    await page.wait_for_selector(".conv")
    return browser, page


async def shape(page: Any) -> list[str]:
    """Labels (upper case: .section-label) and titles of the list, in order."""
    return await page.locator("#list .folder, #list .conv .t").all_inner_texts()


async def test_the_list_is_grouped_by_the_day_a_conversation_last_changed(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_list(playwright, static_site)
        try:
            assert await shape(page) == [
                "PINNED",
                "Pinned one",
                "TODAY",
                "Today late",
                "Today early",  # (00:01 today)
                "YESTERDAY",
                "Yesterday late",  # (23:59 yesterday)
                "LAST 7 DAYS",
                "Three days ago",
                "OLDER",
                "Eight days ago",
                "Broken date",
                "No date",
                "Work",  # (a folder keeps its place and its name)
                "In a folder",
            ]  # fmt: skip
            assert "RECENT" not in await shape(page)
            # a label is no row: it cannot be focused, and Tab and the arrows pass it by
            assert await page.locator("#list .section-label[tabindex]").count() == 0
            rows = await page.locator("#list .conv").count()
            await page.locator("#list .conv").first.focus()
            seen = []
            for key in ["ArrowDown", "Tab", "Tab", "ArrowDown", "Tab"]:
                await page.keyboard.press(key)
                seen.append(
                    await page.evaluate(
                        "() => document.activeElement.classList.contains('folder')"
                        " || document.activeElement.classList.contains('section-label')"
                    )
                )
            assert rows == 9 and seen == [False] * 5
        finally:
            await browser.close()


async def test_a_group_without_conversations_has_no_label(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    only = {"current": "d", "conversations": [made("d", "Three days ago", "2026-11-11T08:00:00")]}
    async with async_api.async_playwright() as playwright:
        browser, page = await open_list(playwright, static_site, only)
        try:
            assert await shape(page) == ["LAST 7 DAYS", "Three days ago"]  # fmt: skip
        finally:
            await browser.close()


async def test_the_groups_are_worked_out_again_at_every_drawing(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    one = {"current": "c", "conversations": [made("c", "Late", "2026-11-13T23:59:00")]}
    async with async_api.async_playwright() as playwright:
        browser, page = await open_list(playwright, static_site, one)
        try:
            assert (await shape(page))[0] == "YESTERDAY"
            # two minutes later, the next day: the same conversation is in the next group
            await page.evaluate("() => { window.__now += 24 * 3600e3; return loadList(); }")
            await page.wait_for_function(
                "() => document.querySelector('#list .section-label').textContent === 'Last 7 days'"
            )
        finally:
            await browser.close()


async def test_pinned_ones_and_folders_stay_as_they_were(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    listing = {
        "current": "p",
        "conversations": [
            made("p", "Pinned old", "2026-01-01T10:00:00", pinned=True),
            made("p2", "Pinned in folder", "2026-11-14T10:00:00", folder="Zed", pinned=True),
            made("t", "Today", "2026-11-14T10:00:00"),
            made("f1", "Folder A", "2026-11-14T09:00:00", folder="Alpha"),
            made("f2", "Folder Z", "2026-11-01T09:00:00", folder="Zed"),
        ],
    }
    async with async_api.async_playwright() as playwright:
        browser, page = await open_list(playwright, static_site, listing)
        try:
            # pinned: no time label inside; folders sorted by name, no time labels in them
            assert await shape(page) == [
                "PINNED", "Pinned old", "Pinned in folder",
                "TODAY", "Today",
                "Alpha", "Folder A",
                "Zed", "Folder Z",
            ]  # fmt: skip
        finally:
            await browser.close()


async def test_the_archive_and_a_search_show_no_time_labels(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    hit = {"text": "a line with straße in it", "mark": [12, 18]}
    found = {"current": "a", "conversations": [MINE["conversations"][1] | {"hit": hit}]}
    async with async_api.async_playwright() as playwright:
        browser, page = await open_list(playwright, static_site)
        try:
            labels = "#list .section-label"
            await page.click("#archived")  # the archive
            await page.wait_for_selector("#list .folder:has-text('Archived')")
            assert await page.locator(labels).count() == 0
            assert (await shape(page))[0] == "Archived conversations"
            await page.click("#archived")  # back
            await page.wait_for_selector(".section-label:has-text('Today')")
            # a search: the list keeps its look from before, the "Recent" rule as it was
            await page.evaluate(
                "(l) => { const f = window.fetch; window.fetch = (u, ...r) =>"
                " String(u).includes('search=') && !String(u).endsWith('search=') ?"
                " Promise.resolve(new Response(JSON.stringify(l),"
                " {headers: {'content-type': 'application/json'}})) : f(u, ...r); }",
                found,
            )
            await page.fill("#search", "straße")
            await page.wait_for_selector("#list .hit")
            assert await page.locator("#list .folder").count() == 0
            assert await page.locator("#list .conv .hit mark").inner_text() == "straße"
            assert await page.locator("#list .conv .t").inner_text() == "Today late"
            # (the hit stays a line under the title)
        finally:
            await browser.close()


async def test_a_search_keeps_the_recent_label_when_something_is_pinned(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_list(playwright, static_site)
        try:
            await page.fill("#search", "a")
            await page.wait_for_function("() => document.getElementById('search').value === 'a'")
            await page.wait_for_selector("#list .section-label:has-text('Recent')")
            assert "TODAY" not in await shape(page)
            assert (await shape(page))[:3] == ["PINNED", "Pinned one", "RECENT"]
        finally:
            await browser.close()


async def test_the_time_groups_are_in_german(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_list(
            playwright, static_site, init="localStorage.setItem('starlight-lang', 'de');\n"
        )
        try:
            labels = await page.locator("#list .section-label").all_inner_texts()
            assert (
                labels[:1] + labels[1:]
                == [
                    "ANGEHEFTET",
                    "HEUTE",
                    "GESTERN",
                    "LETZTE 7 TAGE",
                    "ÄLTER",
                    "ORDNER",
                ][:5]
                + labels[5:]
            )
            assert await page.locator("#list .section-label").count() == 5
        finally:
            await browser.close()
