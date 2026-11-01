"""Block B5 of the notes plan (docs/plan-notes-app.md): the Notes page. It runs in Chromium
with its API calls answered by the real web app, so page and server are tested together."""

from __future__ import annotations

import os
import shutil
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_chat_page import CHROMIUM
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_remote import LOCAL, client, home, pair, phone

NOTES = {
    "Notes/Shopping.md": "---\naliases: [Einkauf]\ntags: [home]\nshop: Rewe\n---\n# Shopping\n\n"
    "## Food\n- [ ] milk\n- [x] bread\n\n## Other\nSee [[Plan]] and #errands.\n",
    "Notes/Plan.md": "# The plan\nBack to [[Shopping#Food]].\n\nThe word Settings stays.\n",
    "Notes/Old ideas.md": "Nothing much. #home\n",
    "Journal/2026-10-30.md": "A quiet day.\n",
    "Recipes/Pancakes.md": "Flour, milk, eggs.\n",
}


@pytest.fixture
def notebook(documents: Path) -> Path:
    root = documents / "Starlight"
    for relative, text in NOTES.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def app(tmp_path: Path, notebook: Path) -> Iterator[Any]:
    root = home(tmp_path / "h")
    shutil.copytree(REPO / "modules" / "notes", root / "modules" / "notes")
    with client(root) as c:
        yield c


@asynccontextmanager
async def notes_page(
    static_site: str, app: Any, locale: str = "en-US", scheme: str = "light", width: int = 1400
) -> AsyncIterator[Any]:
    """The Notes page in Chromium; /api/... goes to the real app (``app``)."""
    async_api = pytest.importorskip("playwright.async_api")
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")

    async def backend(route: Any, request: Any) -> None:
        path = "/" + request.url.split("/", 3)[3]
        headers = {**LOCAL}
        if request.headers.get("content-type"):
            headers["content-type"] = request.headers["content-type"]
        answer = app.request(
            request.method, path, headers=headers, content=request.post_data_buffer
        )
        await route.fulfill(
            status=answer.status_code,
            body=answer.content,
            content_type=answer.headers.get("content-type", "application/json"),
        )

    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable)
        try:
            context = await browser.new_context(
                locale=locale, color_scheme=scheme, viewport={"width": width, "height": 860}
            )
            page = await context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.route("**/api/**", backend)
            page.errors = errors  # type: ignore[attr-defined]
            yield page
        finally:
            await browser.close()


async def opened(page: Any, url: str) -> None:
    await page.goto(url)
    await page.wait_for_selector("#folders details")


async def test_the_first_visit_shows_the_welcome_note_once(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html")
        await page.wait_for_selector("h1.title")
        assert await page.locator("h1.title").text_content() == "Welcome"
        body = page.locator("#column .note-body")
        assert await body.locator(".callout").count() == 2
        assert await body.locator("li.task").count() == 2
        assert await body.locator("a.note-link").first.text_content() == "Shopping list"
        assert (notebook / "Notes" / "Welcome.md").is_file()
        (notebook / "Notes" / "Welcome.md").unlink()  # deleted: it does not come back
        await opened(page, f"{static_site}/notes.html")
        assert not (notebook / "Notes" / "Welcome.md").exists()
        assert await page.locator(".hello h1").text_content() == "Your notes"
        assert page.errors == []  # type: ignore[attr-defined]


async def test_reading_a_note_its_outline_backlinks_and_links(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FShopping.md")
        await page.wait_for_selector("h1.title")
        assert await page.locator("h1.title").text_content() == "Shopping"
        chips = await page.locator(".chips").text_content()
        assert "#home" in chips and "shop: Rewe" in chips and "Einkauf" not in chips
        assert "#errands" not in chips  # (a tag in the text shows where it is written)
        assert await page.locator("#outline button").all_text_contents() == [
            "Shopping",
            "Food",
            "Other",
        ]
        backlinks = await page.locator("#backlinks button").all_text_contents()
        assert backlinks == ["PlanNotesBack to [[Shopping#Food]]."]  # (with the line: C1)
        props = await page.locator("#props dd.prop input").evaluate_all(
            "is => is.map(i => [i.getAttribute('aria-label'), i.value])"
        )  # (editable since C2)
        assert ["shop", "Rewe"] in props and ["aliases", "Einkauf"] in props
        current = page.locator('.side .item[aria-current="page"]')
        assert await current.text_content() == "Shopping"
        folders = await page.locator("#folders > details > summary .name").all_text_contents()
        assert folders[:5] == ["Notes", "Journal", "Recipes", "Clippings", "Inbox"]
        assert await page.locator("#folders > details").first.evaluate("d => d.open")

        await page.locator("#column a.note-link", has_text="Plan").click()  # [[Plan]]
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Plan'"
        )
        assert "path=Notes%2FPlan.md" in page.url
        assert await page.locator("#column .note-body").text_content() is not None
        assert "The word Settings stays." in await page.locator("#column .note-body").text_content()
        await page.locator("#column a.note-link").click()  # [[Shopping#Food]]
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Shopping'"
        )
        assert "heading=Food" in page.url
        await page.go_back()
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Plan'"
        )

        await opened(page, f"{static_site}/notes.html?open=einkauf")  # a link by its alias
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Shopping'"
        )
        await opened(page, f"{static_site}/notes.html?open=Nowhere")
        await page.wait_for_selector(".missing-note")  # not written yet: offered (C1)
        assert await page.locator(".missing-note h1").text_content() == "Nowhere"
        assert page.errors == []  # type: ignore[attr-defined]


async def test_tags_search_views_and_pins(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?tag=home")
        await page.wait_for_selector(".card-note")
        assert await page.locator(".card-note .t").all_text_contents() == ["Old ideas", "Shopping"]
        await page.locator("#search").fill("flour")
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent.includes('flour')"
        )
        await page.wait_for_selector(".card-note")
        assert await page.locator(".card-note .t").all_text_contents() == ["Pancakes"]
        assert "milk" in await page.locator(".card-note .s").text_content()
        await page.locator("#search").fill("zzzz")
        await page.wait_for_selector("text=Nothing found")

        await page.locator('.quick button[data-view="inbox"]').click()
        await page.wait_for_selector("text=The inbox is empty")
        await page.locator('.quick button[data-view="graph"]').click()
        await page.wait_for_selector("text=coming in a later update")

        await page.locator('#folders details[data-folder="Recipes"] > summary').click()
        await page.locator(".side .item", has_text="Pancakes").click()
        await page.wait_for_selector("text=☆ Pin")
        await page.locator("button", has_text="☆ Pin").click()
        await page.wait_for_selector("#pinned-box:not([hidden])")
        assert await page.locator("#pinned .item").all_text_contents() == ["Pancakes"]
        assert page.errors == []  # type: ignore[attr-defined]


async def test_the_quick_switcher(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html")
        await page.keyboard.press("Control+k")
        await page.wait_for_selector("#switcher:not([hidden])")
        await page.keyboard.type("shp")
        first = page.locator('#switch-list li[aria-selected="true"]')
        assert (await first.text_content() or "").startswith("Shopping")
        await page.keyboard.press("Enter")
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Shopping'"
        )
        assert await page.locator("#switcher").is_hidden()
        await page.keyboard.press("Control+k")
        await page.keyboard.type("einkauf")  # an alias
        assert (
            "Einkauf" in await page.locator('#switch-list li[aria-selected="true"]').text_content()
        )
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Escape")
        assert await page.locator("#switcher").is_hidden()
        await page.locator("#find").click()
        assert await page.locator("#switch-list li").count() >= 5  # recent and all notes
        assert page.errors == []  # type: ignore[attr-defined]


async def test_in_german_the_page_speaks_german_but_notes_stay_as_written(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app, locale="de-DE") as page:
        await opened(page, f"{static_site}/notes.html")
        await page.wait_for_function("() => document.querySelector('h1.title')")
        assert await page.locator("h1.title").text_content() == "Willkommen"
        assert (notebook / "Notes" / "Willkommen.md").is_file()
        headings = await page.locator(".side h3, .info h3").all_text_contents()
        assert "Ordner" in headings and "Gliederung" in headings and "Verlinkt von" in headings
        assert await page.locator("#search").get_attribute("placeholder") == "Notizen durchsuchen"
        await page.locator(".side .item", has_text="Plan").click()
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Plan'"
        )
        assert "The word Settings stays." in await page.locator("#column .note-body").text_content()
        assert page.errors == []  # type: ignore[attr-defined]


def test_the_notes_page_is_on_the_pc_only(tmp_path: Path, notebook: Path) -> None:
    with client(home(tmp_path / "h")) as c:
        assert c.get("/notes", headers=LOCAL).status_code == 200
        token = pair(c)
        assert c.get("/notes", headers=phone(token)).status_code == 403
        for page in ("/", "/memory", "/activity", "/automations", "/settings"):
            assert 'href="/notes"' in c.get(page, headers=LOCAL).text, page


async def test_a_phone_does_not_show_the_notes_link(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async_api = pytest.importorskip("playwright.async_api")
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    async with async_api.async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=executable)
        try:
            for local, shown in ((False, False), (True, True)):
                page = await browser.new_page()

                async def status(route: Any, request: Any, local: bool = local) -> None:
                    body = '{"local": %s}' % ("true" if local else "false")
                    await route.fulfill(body=body, content_type="application/json")

                await page.route("**/api/**", status)
                await page.goto(f"{static_site}/memory.html")
                await page.wait_for_timeout(300)
                assert await page.locator('header nav a[href="/notes"]').is_visible() == shown
                await page.close()
        finally:
            await browser.close()
