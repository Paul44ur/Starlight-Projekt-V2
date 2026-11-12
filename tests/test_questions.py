"""Starlight's own questions (askYesNo, askText in common.js): a small window in the page's
look instead of the browser's grey boxes, on every page."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_app_look import LIST, NARROWED, STATUS, open_app
from tests.test_chat_page import SEND, answer, static_site  # noqa: F401  (a fixture)

STATIC = Path(__file__).resolve().parents[1] / "starlight" / "web" / "static"


def test_no_page_uses_the_browsers_own_boxes() -> None:
    boxes = re.compile(r"(?<![\w.])(?:window\.)?(?:alert|confirm|prompt)\(")
    for page in [*STATIC.glob("*.html"), *STATIC.glob("*.js")]:
        if ".bundle." in page.name or page.name.endswith(".min.js"):
            continue
        assert not boxes.search(page.read_text("utf-8")), page.name


async def test_a_question_is_a_window_in_the_pages_look(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    ask = "(code) => { window.said = 'waiting'; eval(code).then(a => { window.said = a; }); }"

    async def said(page: Any) -> Any:  # (the window closes a moment after the key or click)
        await page.wait_for_function("() => window.said !== 'waiting'")
        return await page.evaluate("() => window.said")

    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(playwright, static_site)
        native: list[str] = []
        page.on("dialog", lambda dialog: native.append(dialog.message))
        try:
            box = page.locator("dialog.ask")
            # a dangerous answer is red, and the keyboard starts on Cancel
            await page.evaluate(
                ask, "askYesNo('Delete \"Plan\"?\\n\\nIt is gone.', {yes: 'Delete', danger: true})"
            )
            assert await box.locator(".ask-q").inner_text() == 'Delete "Plan"?'
            assert await box.locator(".ask-more").inner_text() == "It is gone."
            assert await box.locator("button").all_inner_texts() == ["Cancel", "Delete"]
            assert await box.locator("button.danger").inner_text() == "Delete"
            assert await page.evaluate("() => document.activeElement.textContent") == "Cancel"
            assert await box.get_attribute("role") == "alertdialog"
            panel = await box.evaluate("b => getComputedStyle(b).backgroundColor")
            lifted = """() => {  // --panel2 as computed (a question lifts off the page)
                const probe = document.body.appendChild(document.createElement("i"));
                probe.style.background = "var(--panel2)";
                const colour = getComputedStyle(probe).backgroundColor;
                probe.remove();
                return colour;
            }"""
            assert panel == await page.evaluate(lifted)
            await page.keyboard.press("Enter")  # (on Cancel)
            assert await said(page) is False and await box.count() == 0
            # an everyday one: Enter says yes; Esc and a click beside it say no
            await page.evaluate(ask, "askYesNo('Save it?', {yes: 'Save'})")
            assert await page.evaluate("() => document.activeElement.textContent") == "Save"
            await page.keyboard.press("Enter")
            assert await said(page) is True
            await page.evaluate(ask, "askYesNo('Save it?')")
            await page.keyboard.press("Escape")
            assert await said(page) is False
            await page.evaluate(ask, "askYesNo('Save it?')")
            await page.mouse.click(5, 5)
            assert await said(page) is False and await box.count() == 0
            # a line of text: what is there is marked, so typing replaces it
            await page.evaluate(ask, "askText('New name', 'Plan the week', {yes: 'Rename'})")
            assert await box.locator("input").input_value() == "Plan the week"
            await page.keyboard.type("Week 46")
            await page.keyboard.press("Enter")
            assert await said(page) == "Week 46"
            await page.evaluate(ask, "askText('New name', 'Plan the week')")
            await box.locator("button", has_text="Cancel").click()
            assert await said(page) is None
            # Esc in a question is not the page's Esc (which stops the answer being written)
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            await page.evaluate(ask, "askYesNo('Save it?')")
            await page.keyboard.press("Escape")
            assert await said(page) is False
            assert await page.locator(".toast").count() == 0  # (no "Stopping …")
            assert native == []
        finally:
            await browser.close()


RECORDED = """() => { const real = window.fetch; window.calls = [];
  window.fetch = async (url, init) => {
    if (init && init.method && init.method !== 'GET')
      window.calls.push([init.method, new URL(String(url), location.href).pathname,
                         init.body ? JSON.parse(init.body) : null]);
    return real(url, init); }; }"""


async def test_the_chat_asks_in_its_own_window_also_in_german(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_app(
            playwright, static_site, "localStorage.setItem('starlight-lang', 'de');\n"
        )
        native: list[str] = []
        page.on("dialog", lambda dialog: native.append(dialog.message))
        try:
            await page.evaluate(NARROWED, [LIST, STATUS])
            await page.evaluate(RECORDED)
            row = page.locator(".conv", has_text="Recipe for Sunday")
            await row.hover()
            await row.locator(".acts button").click()
            menu = page.locator(".popmenu button")
            words = " ".join(await menu.all_inner_texts())
            for word in ("Umbenennen", "Oben anheften", "In einen Ordner legen", "Archivieren",
                         "Als Markdown herunterladen", "Löschen"):  # fmt: skip
                assert word in words, word
            await menu.filter(has_text="Umbenennen").click()
            assert await answer(page, text="  Sunday roast ") == "Neuer Name"
            await page.wait_for_function("() => window.calls.length === 1")
            assert await page.evaluate("() => window.calls[0]") == [
                "PATCH", "/api/conversations/c2", {"title": "Sunday roast"}
            ]  # fmt: skip
            await page.wait_for_selector(".conv:has-text('Sunday roast')")
            # deleting asks first; no leaves it there
            row = page.locator(".conv", has_text="Sunday roast")
            await row.hover()
            await row.locator(".acts button").click()
            await menu.filter(has_text="Löschen").click()
            box = page.locator("dialog.ask")
            await box.wait_for()
            assert await box.locator("button").all_inner_texts() == ["Abbrechen", "Löschen"]
            assert await answer(page, yes=False) == "„Sunday roast“ löschen?"
            assert await page.evaluate("() => window.calls.length") == 1
            await row.hover()
            await row.locator(".acts button").click()
            await menu.filter(has_text="Löschen").click()
            await answer(page)
            await page.wait_for_function("() => window.calls.length === 2")
            assert await page.evaluate("() => window.calls[1].slice(0, 2)") == [
                "DELETE", "/api/conversations/c2"
            ]  # fmt: skip
            assert native == []
        finally:
            await browser.close()
