"""Block C3 of the notes plan (docs/plan-notes-app.md): the assistant uses the structure. A
memory's [[links]] bring the memory or note they lead to into the context (one step, at
most 5, a size budget); notes.read gives a note's properties and links; "Ask Starlight" on
the Notes page opens the chat with the request written out."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.memory import service
from tests.test_chat_page import open_chat
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_home_devices import call, fails
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened


@pytest.fixture
def notes(paths: Paths, use_module: Callable[[str], Path], documents: Path) -> Path:
    use_module("notes")
    folder = documents / "Starlight" / "Notes"
    folder.mkdir(parents=True)
    (folder / "Car service.md").write_text(
        "# Car service\nGarage Müller, phone 0123.\n## Last time\nOil changed in May.\n"
        "## Next\nTyres in winter. See [[Tyres]].\n",
        encoding="utf-8",
    )
    (folder / "Tyres.md").write_text("Winter tyres are in the cellar.\n", encoding="utf-8")
    return folder


async def test_a_memorys_links_bring_their_memories_and_notes(paths: Paths, notes: Path) -> None:
    async with Kernel(paths) as k:
        memory = k.memory
        await memory.remember("A blue Golf from 2019.", "Golf")
        await memory.remember(
            "My car is a [[Golf]]. Service: [[Car service#Last time]]. Garage: [[Car service]].",
            "Car",
        )
        context = await memory.enrich("What car do I have?")
    assert context is not None and "What those memories link to" in context
    linked = context.split("What those memories link to", 1)[1]
    assert "A blue Golf from 2019." in context  # (a hit itself, or linked)
    assert "- Car service > Last time: ## Last time Oil changed in May." in linked
    assert "Garage Müller" not in linked  # the same note comes once, not twice
    assert "Winter tyres" not in context  # one step only: the note's own links stay


async def test_the_links_stay_within_the_limits(
    paths: Paths, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for number in range(8):
        (notes / f"Short {number}.md").write_text(f"Short note {number}.", encoding="utf-8")
        (notes / f"Long {number}.md").write_text(f"N{number} " + "word " * 400, encoding="utf-8")
    async with Kernel(paths) as k:
        short = " ".join(f"[[Short {number}]]" for number in range(8))
        long = " ".join(f"[[Long {number}]]" for number in range(8))
        await k.memory.remember(f"Small things: {short}", "Small")
        await k.memory.remember(f"Reading list: {long}", "Reading")
        entries = {e.topic: e for e in k.memory.store.all()}
        found = k.memory.linked([entries["Small"]])
        assert [name for name, _ in found] == [f"Short {n}" for n in range(5)]  # at most 5
        found = k.memory.linked([entries["Reading"]])
        assert sum(len(body) for _, body in found) <= service.LINKED_CHARS  # the size budget
        assert all(len(body) <= service.LINKED_ONE for _, body in found)
        assert len(found) == 4  # 600 + 600 + 600 + the 200 left
        monkeypatch.setattr(service, "LINKED_CHARS", 700)
        assert sum(len(body) for _, body in k.memory.linked([entries["Reading"]])) <= 700


async def test_notes_read_gives_properties_and_takes_a_path(paths: Paths, notes: Path) -> None:
    journal = notes.parent / "Journal"
    journal.mkdir()
    (journal / "2026-10-30.md").write_text("---\nmood: good\n---\nA quiet day.\n", "utf-8")
    async with Kernel(paths) as k:
        read: Any = await call(k, "notes.read", title="Journal/2026-10-30")
        assert read["properties"] == {"mood": "good"} and read["path"] == "Journal/2026-10-30.md"
        assert "A quiet day." in read["text"]
        car: Any = await call(k, "notes.read", title="Car service")
        assert car["properties"] == {} and car["links"] == ["Tyres"]
        for bad in ("../outside", "code/x", "Journal/../../x", ".hidden/x"):
            assert "Starlight folder" in await fails(k, "notes.read", title=bad)
        renamed: Any = await call(
            k, "notes.rename", title="Journal/2026-10-30", new_title="Quiet day"
        )
        assert renamed["to"] == "Quiet day" and (journal / "Quiet day.md").is_file()  # same folder


async def test_ask_starlight_opens_the_chat_with_the_request(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        reached: list[str] = []

        async def chat(route: Any, request: Any) -> None:
            reached.append(request.url)
            await route.fulfill(body="<p>the chat</p>", content_type="text/html")

        await page.route("**/?ask=**", chat)
        await opened(page, f"{static_site}/notes.html?path=Notes%2FShopping.md")
        await page.locator("button", has_text="✨ Ask").click()
        items = page.locator(".ask-menu [role=menuitem]")
        assert await items.all_text_contents() == [
            "Summarise", "Find the tasks", "Suggest links and tags", "Translate",
        ]  # fmt: skip
        await page.locator('.ask-menu [data-ask="tasks"]').click()
        await page.wait_for_selector("text=the chat")
        from urllib.parse import parse_qs, urlsplit

        asked = parse_qs(urlsplit(reached[-1]).query)["ask"][0]
        assert asked == (
            'List the tasks in my note "Shopping" (Notes/Shopping): what is still open, and when '
            "it is due."
        )
    async with notes_page(static_site, app, locale="de-DE") as page:
        await page.route("**/?ask=**", chat)
        await opened(page, f"{static_site}/notes.html?path=Notes%2FPlan.md")
        await page.locator("button", has_text="✨ Fragen").click()
        await page.locator('.ask-menu [data-ask="summary"]').click()
        await page.wait_for_selector("text=the chat")
        asked = parse_qs(urlsplit(reached[-1]).query)["ask"][0]
        assert asked == "Fasse meine Notiz „Plan“ (Notes/Plan) in ein paar Sätzen zusammen."


async def test_the_chat_takes_the_request_without_sending_it(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            await page.add_init_script(  # (after the stand-in server: record what is sent)
                "window.WebSocket.prototype.send = function (d) { (window.sent ||= []).push(d); };"
            )
            await page.goto(f"{static_site}/chat.html?ask=" + "Summarise%20my%20note%20%22Plan%22")
            await page.wait_for_function("() => document.querySelector('textarea')?.value")
            assert await page.locator("textarea").input_value() == 'Summarise my note "Plan"'
            assert "Check the request" in await page.locator(".toast").first.text_content()
            assert await page.evaluate("() => location.search") == ""  # (not asked again on reload)
            await page.wait_for_timeout(300)
            sent = await page.evaluate("() => (window.sent || []).map(d => JSON.parse(d).type)")
            assert "ask" not in sent  # nothing goes to Starlight before Send
        finally:
            await browser.close()
