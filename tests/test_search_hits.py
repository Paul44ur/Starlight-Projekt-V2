"""Search hits: a conversation found by what was said in it shows that line under its title and
opens at that message. The store says where (``hit``), the chat's list and page show it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from starlight.core.conversations import ConversationStore
from starlight.core.model import Message, ToolCall
from tests.test_chat_app_look import APP, LIST, NARROWED, STATUS, open_app  # noqa: F401
from tests.test_chat_page import SEND, static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import browser_page

# -- the store ---------------------------------------------------------------------------------


def _store(tmp_path: Path) -> ConversationStore:
    return ConversationStore(tmp_path / "conversations")


def _by_id(found: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in found}


def test_found_by_its_title_has_no_hit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    one = store.create("Holiday plans")
    store.save(one, [Message("user", "hello"), Message("assistant", "hi")])
    found = store.list("holiday")
    assert [c["id"] for c in found] == [one] and "hit" not in found[0]


def test_found_by_a_message_says_which_line_and_which_message(tmp_path: Path) -> None:
    store = _store(tmp_path)
    one = store.create("Notes")
    long_before = "word " * 30
    long_after = " tail" * 40
    store.save(
        one,
        [
            Message("user", "first"),
            Message("assistant", "second"),
            Message("user", f"{long_before}the  Blue Lagoon\n\ttrip{long_after}"),
        ],
    )
    (hit,) = [c["hit"] for c in store.list("BLUE lagoon")]
    assert hit["message"] == 2
    text = hit["text"]
    assert "Blue Lagoon trip" in text  # (whitespace collapsed, the case kept)
    assert "\n" not in text and "\t" not in text and "  " not in text
    assert text.startswith("…") and text.endswith("…")
    assert len(text) < 40 + 80 + 20  # about 40 before, 80 after
    assert text[1:].split(" ")[0] == "word"  # cut at a word border
    start, end = hit["mark"]  # (where the words are in the line, the dots counted)
    assert text[start:end] == "Blue Lagoon"


def test_a_word_with_a_sharp_s_is_found_and_marked(tmp_path: Path) -> None:
    """German: "ß" counts as "ss" in a search, so the place is not where a plain
    lower-case comparison finds it."""
    store = _store(tmp_path)
    one = store.create("Weg")
    store.save(one, [Message("user", "Die Straße ist lang, viele Grüße")])
    for words in ("strasse", "STRASSE", "Straße", "straße"):
        hit = store.list(words)[0]["hit"]
        assert hit["text"] == "Die Straße ist lang, viele Grüße", words
        assert hit["mark"] == [4, 10], words
    start, end = store.list("grüsse")[0]["hit"]["mark"]
    assert "Die Straße ist lang, viele Grüße"[start:end] == "Grüße"


def test_a_short_message_is_shown_whole_without_dots(tmp_path: Path) -> None:
    store = _store(tmp_path)
    one = store.create("Notes")
    store.save(one, [Message("user", "Is the  pasta\nready?")])
    assert store.list("pasta")[0]["hit"] == {
        "text": "Is the pasta ready?",
        "message": 0,
        "mark": [7, 12],
    }


def test_the_index_counts_what_the_page_shows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    one = store.create("Notes")
    call = ToolCall("web_search", {"q": "x"}, "id1")
    store.save(
        one,
        [
            Message("user", "look it up"),
            Message("assistant", "", tool_calls=[call]),  # (no words: not shown)
            Message("tool", "result about ferrets", tool_call_id="id1", name="web_search"),
            Message("assistant", "Ferrets are small."),
        ],
    )
    # shown: "look it up" (0), "Ferrets are small." (1)
    assert store.list("ferrets")[0]["hit"]["message"] == 1


def test_words_only_in_a_hidden_message_are_found_without_a_hit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    one = store.create("Notes")
    store.save(
        one,
        [
            Message("user", "look it up"),
            Message("tool", "secret ferrets", tool_call_id="id1", name="web_search"),
            Message("system", "a system note about ferrets"),
            Message("assistant", "Done."),
        ],
    )
    found = store.list("ferrets")
    assert [c["id"] for c in found] == [one] and "hit" not in found[0]


def test_content_with_pictures_is_a_list(tmp_path: Path) -> None:
    store = _store(tmp_path)
    one = store.create("Notes")
    data = json.loads((store.folder / f"{one}.json").read_text("utf-8"))
    data["messages"] = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                {"type": "text", "text": "what is this lighthouse?"},
            ],
        },
    ]
    (store.folder / f"{one}.json").write_text(json.dumps(data), "utf-8")
    assert store.list("lighthouse")[0]["hit"] == {
        "text": "what is this lighthouse?",
        "message": 0,
        "mark": [13, 23],
    }
    assert "AAAA" not in json.dumps(store.list("lighthouse"))
    # the words only in the picture's data are not a hit (the page does not show them)
    assert "hit" not in (store.list("base64") or [{}])[0]


def test_a_private_conversation_has_hits_too(tmp_path: Path) -> None:
    store = _store(tmp_path)
    one = store.create("Secret", private=True)
    store.save(one, [Message("user", "hi"), Message("assistant", "The cake is a lie")])
    (found,) = store.list("cake")
    assert found["private"]
    assert found["hit"] == {"text": "The cake is a lie", "message": 1, "mark": [4, 8]}
    assert not (store.folder / f"{one}.json").exists()


def test_no_search_no_hit_and_unchanged_files_are_not_read_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    one = store.create("Notes")
    store.save(one, [Message("user", "hello world")])
    assert store.list("world")[0]["hit"]["message"] == 0
    assert all("hit" not in c for c in store.list())  # (a search leaves nothing in the cache)
    reads: list[Path] = []
    real = Path.read_text

    def counting(self: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(self)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    assert all("hit" not in c for c in store.list())
    assert reads == []  # (the summary was kept)


def test_the_api_list_carries_the_hit(tmp_path: Path) -> None:
    from tests.test_remote import LOCAL, client, home

    with client(home(tmp_path / "h", remote=False)) as c:
        made = c.post("/api/conversations", headers=LOCAL).json()["id"]
        with c.websocket_connect("/ws", headers=LOCAL) as ws:
            ws.send_json({"type": "ask", "text": "tell me about zebras", "conversation": made})
            while ws.receive_json()["type"] != "answer":
                pass
        c.patch(f"/api/conversations/{made}", json={"title": "Animals"}, headers=LOCAL)
        listed = c.get("/api/conversations?search=zebras", headers=LOCAL).json()
        mine = _by_id(listed["conversations"])[made]
        assert "zebras" in mine["hit"]["text"] and mine["hit"]["message"] == 0
        plain = c.get("/api/conversations", headers=LOCAL).json()
        assert "hit" not in _by_id(plain["conversations"])[made]


# -- the page ----------------------------------------------------------------------------------

LONG = [
    {
        "role": "user" if i % 2 == 0 else "assistant",
        "content": f"Message number {i}\n\n" + "x " * 200,
    }
    for i in range(30)
]
HIT_TEXT = "…the plan for the <img src=x onerror=window.pwned=1> Sunday trip is set…"
HITS = {
    "current": "c1",
    "conversations": [
        {"id": "c2", "title": "Recipe for Sunday", "updated": "2026-11-02T10:00:00", "folder": "",
         "hit": {"text": HIT_TEXT, "message": 12}},
        {"id": "c1", "title": "Sunday plan", "updated": "2026-11-04T10:00:00", "folder": ""},
    ],
}  # fmt: skip
# After APP: a search answers with the hits, and conversation c2 has thirty long messages.
FAKE = (
    "const plain = window.fetch; window.fetch = async (url, o) => { const u = String(url);"
    f" const type = {{'content-type': 'application/json'}}; const HITS = {json.dumps(HITS)};"
    f" const LONG = {json.dumps(LONG)};"
    " if (u.includes('search=Sunday')) return new Response(JSON.stringify(HITS), {headers: type});"
    " if (u.endsWith('/api/conversations/c2')) return new Response(JSON.stringify("
    "{id: 'c2', title: 'Recipe for Sunday', private: false, messages: LONG}), {headers: type});"
    " return plain(url, o); };"
)


async def _searching(playwright: Any, site: str, **context: Any) -> tuple[Any, Any]:
    browser, page = await browser_page(playwright, site, "chat.html", APP + FAKE, **context)
    await page.wait_for_selector(".conv")
    await page.fill("#search", "Sunday")
    await page.wait_for_selector(".conv .hit")
    return browser, page


async def test_a_hit_shows_its_line_with_the_words_marked(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await _searching(playwright, static_site)
        try:
            rows = page.locator("#list .conv")
            assert await rows.count() == 2
            line = rows.nth(0).locator(".hit")
            assert await line.inner_text() == HIT_TEXT  # (text: no element made from it)
            assert await line.locator("img").count() == 0
            assert await page.evaluate("window.pwned") is None
            assert await line.locator("mark").all_inner_texts() == ["Sunday"]
            assert await line.get_attribute("translate") == "no"
            marked = """() => withHit(document.createElement("span"),
                {text: "Die Straße ist lang", mark: [4, 10]}, "strasse").querySelector("mark")
                .textContent"""
            assert await page.evaluate(marked) == "Straße"  # (the server's place is used)
            style = await line.evaluate(
                "n => { const s = getComputedStyle(n); return [s.whiteSpace, s.textOverflow,"
                " s.fontSize, s.color]; }"
            )
            assert style[:3] == ["nowrap", "ellipsis", "12px"]
            assert await rows.nth(1).locator(".hit").count() == 0  # (found by its title)
            assert await rows.nth(1).locator(".t").inner_text() == "Sunday plan"
            assert await page.evaluate("document.documentElement.scrollWidth") <= 1200
            await page.fill("#search", "")  # cleared: the list as before
            await page.wait_for_function("document.querySelectorAll('#list .conv').length === 3")
            assert await page.locator("#list .hit").count() == 0
        finally:
            await browser.close()


async def test_opening_a_hit_scrolls_to_the_message_and_marks_it_for_a_moment(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await _searching(playwright, static_site)
        try:
            await page.locator("#list .conv").nth(0).click()
            await page.wait_for_selector(".msg.found")
            assert await page.locator(".msg.found").get_attribute("data-n") == "12"
            assert "Message number 12" in await page.locator(".msg.found").inner_text()
            centre = await page.evaluate(
                "() => { const l = document.getElementById('log').getBoundingClientRect(),"
                " m = document.querySelector('.msg.found').getBoundingClientRect();"
                " return [m.top >= l.top - 1 && m.bottom <= l.bottom + 1,"
                " Math.abs((m.top + m.bottom) / 2 - (l.top + l.bottom) / 2)]; }"
            )
            assert centre[0] and centre[1] < 40  # in view, in the middle
            assert await page.input_value("#search") == "Sunday"  # (the search stays)
            await page.wait_for_selector(".msg.found", state="detached", timeout=5000)
        finally:
            await browser.close()


async def test_opening_a_row_without_a_hit_goes_to_the_bottom_as_before(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await browser_page(playwright, static_site, "chat.html", APP + FAKE)
        try:
            await page.wait_for_selector(".conv")
            assert await page.locator("#list .hit").count() == 0  # (looks as before)
            await page.locator("#list .conv", has_text="Recipe for Sunday").click()
            await page.wait_for_selector(".msg[data-n='29']")
            assert await page.locator(".msg.found").count() == 0
            await page.wait_for_function(
                "() => { const l = document.getElementById('log');"
                " return l.scrollHeight - l.scrollTop - l.clientHeight < 80; }"
            )
        finally:
            await browser.close()


async def test_a_german_page_adds_nothing_in_english(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await _searching(playwright, static_site)
        try:
            await page.evaluate("localStorage.setItem('starlight-lang', 'de')")
            await page.reload()
            await page.wait_for_selector(".conv")
            await page.fill("#search", "Sunday")
            await page.wait_for_selector(".conv .hit")
            assert await page.locator(".conv .hit").inner_text() == HIT_TEXT  # (not translated)
            assert await page.locator("#list .conv .t").first.inner_text() == "Recipe for Sunday"
        finally:
            await browser.close()
