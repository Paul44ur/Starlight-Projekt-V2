"""The Settings page in the Night Sky look (plan UI 2, block V3) in a real browser: one centred
column of cards, the safety level as three choice cards, switches in the accent colour, one gold
button at most per card, nothing sideways at 360 px, and a phone sees what it saw before."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import CHROMIUM, answer
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_settings_look import DATA

MODEL = {
    "server": "llamacpp",
    "current": {"model": "qwen-8b.gguf", "context": 32768},
    "running": {"model": "qwen-8b.gguf", "context": 32768},
    "options": [{"id": "qwen-8b.gguf", "label": "qwen-8b", "size": 5200000000}],
    "contexts": [0, 8192, 32768],
    "problem": "",
    "pending": False,
    "overridden": False,
    "restart": {"possible": True},
}
VOICES = {
    "installed": [{"name": "de-kerstin", "language": "de", "speaks": True}],
    "can_install": [],
    "wake_word": False,
}


def stub(local: bool = True) -> str:
    data = copy.deepcopy(DATA)
    data["/api/status"] = {"local": local}
    data["/api/model"] = MODEL
    data["/api/voices"] = VOICES
    return (
        f"const DATA = {json.dumps(data)}; window.posted = [];\n"
        "window.fetch = async (url, init) => {\n"
        "  const u = new URL(String(url), location.href).pathname;\n"
        "  if (init && init.method === 'POST') {\n"
        "    const body = JSON.parse(init.body); window.posted.push([u, body]);\n"
        "    if (u === '/api/safety' && body.level) DATA[u].level = body.level;\n"
        "    return new Response(JSON.stringify(DATA[u] || {}), {status: 200,\n"
        "      headers: {'content-type': 'application/json'}});\n"
        "  }\n"
        "  return new Response(JSON.stringify(DATA[u] || {}), {status: u in DATA ? 200 : 404,\n"
        "    headers: {'content-type': 'application/json'}});\n"
        "};"
    )


async def open_page(
    playwright: Any, site: str, width: int = 1280, local: bool = True, scheme: str = "dark"
) -> Any:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    await page.add_init_script(stub(local))
    await page.goto(f"{site}/settings.html")
    await page.wait_for_selector("#safety .srow")
    await page.wait_for_selector("details.mod")
    if local:
        await page.wait_for_selector("#model-card:not([hidden])")
    return browser, page


# A token's colour as the browser computes it.
TOKEN = """name => {
  const d = document.createElement('i'); d.style.backgroundColor = `var(${name})`;
  document.body.appendChild(d); const c = getComputedStyle(d).backgroundColor; d.remove();
  return c; }"""


async def test_one_centred_column_of_cards_with_display_titles(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_page(playwright, static_site)
        try:
            box = await page.locator(".page").bounding_box()
            assert box["width"] <= 760.5
            assert abs((box["x"] + box["width"] / 2) - 1280 / 2) <= 1  # centred
            widths = await page.locator(".card:visible").evaluate_all(
                "cards => cards.map(c => Math.round(c.getBoundingClientRect().width))"
            )
            assert widths and max(widths) <= 760 and max(widths) - min(widths) <= 1
            fonts = await page.locator(".card:visible h2").evaluate_all(
                "hs => hs.map(h => getComputedStyle(h).fontFamily)"
            )
            assert fonts and all("Space Grotesk" in f for f in fonts)
            # the header: the gold star icon, then the title in the display font
            assert await page.locator("header.top h1 svg.ico .star").count() == 1
            title = page.locator("header.top h1")
            assert "Space Grotesk" in await title.evaluate("h => getComputedStyle(h).fontFamily")
            links = await page.locator("header.top nav a").all_inner_texts()
            assert links == ["Chat", "Today", "Notes", "Memory", "Activity", "Automations"]
        finally:
            await browser.close()


async def test_the_level_is_three_choice_cards_and_relaxed_still_asks_first(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_page(playwright, static_site)
        try:
            cards = page.locator("#levels button")
            assert await cards.count() == 3
            pressed = await cards.evaluate_all("bs => bs.map(b => b.getAttribute('aria-pressed'))")
            assert pressed == ["false", "true", "false"]
            tops = await cards.evaluate_all("bs => bs.map(b => b.getBoundingClientRect().top)")
            assert max(tops) - min(tops) <= 1  # side by side
            assert await page.locator("#levels .lname").all_inner_texts() == [
                "Strict",
                "Balanced",
                "Relaxed",
            ]
            for line in await page.locator("#levels .lhelp").all_inner_texts():
                assert len(line) > 20  # each card carries its level's help line
            # the chosen one: a 2px accent line and --panel2 behind it; the others a 1px border
            chosen = await page.locator("#levels button.balanced").evaluate(
                "b => { const s = getComputedStyle(b);"
                " return [s.borderTopWidth, s.borderTopColor, s.backgroundColor]; }"
            )
            accent, panel2 = (
                await page.evaluate(TOKEN, "--accent"),
                await page.evaluate(TOKEN, "--panel2"),
            )
            assert chosen == ["2px", accent, panel2]
            other = page.locator("#levels button.strict")
            assert await other.evaluate("b => getComputedStyle(b).borderTopWidth") == "1px"
            # Relaxed asks first (the existing question); "no" sends nothing
            await page.locator("#levels button.relaxed").click()
            assert await answer(page, yes=False) == "Choose this level?"
            assert await page.evaluate("window.posted") == []
            await page.locator("#levels button.relaxed").click()
            await answer(page, yes=True)
            await page.wait_for_function(
                "() => document.querySelector('#levels button.relaxed.sel')"
            )
            assert await page.evaluate("window.posted") == [["/api/safety", {"level": "relaxed"}]]
            relaxed = page.locator("#levels button.relaxed")
            assert await relaxed.get_attribute("aria-pressed") == "true"
        finally:
            await browser.close()


async def test_switches_use_the_accent_and_a_card_has_one_gold_button_at_most(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for scheme in ("dark", "light"):
            browser, page = await open_page(playwright, static_site, scheme=scheme)
            try:
                accent = await page.evaluate(TOKEN, "--accent")
                assert accent != await page.evaluate(TOKEN, "--ok")
                # "Tell me when an answer is ready" is on by default
                assert await page.locator("#ready-sign").is_checked()
                track = page.locator("#ready-sign + span")
                # (the switch fades to its colour: on a busy machine the page is read in the
                # middle of the fade, so wait until it is over; the check below still decides)
                await page.wait_for_function(
                    "sel => { const t = document.querySelector(sel);"
                    " return getComputedStyle(t).backgroundColor && !t.getAnimations().length; }",
                    arg="#ready-sign + span",
                )
                assert await track.evaluate("t => getComputedStyle(t).backgroundColor") == accent
                # a switch that is off is not lit
                off = page.locator("#practice + span")
                assert await off.evaluate("t => getComputedStyle(t).backgroundColor") != accent
                counts = await page.locator(".card").evaluate_all(
                    "cards => cards.map(c => c.querySelectorAll('button.primary').length)"
                )
                assert counts and max(counts) <= 1 and sum(counts) >= 3
                # the one main action of the cards that have one
                for ident in ("#pair", "#model-save", "#backup-save"):
                    assert await page.locator(ident).get_attribute("class") == "primary"
            finally:
                await browser.close()


async def test_nothing_scrolls_sideways_at_360_px_and_the_levels_stack(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for scheme in ("dark", "light"):
            browser, page = await open_page(playwright, static_site, width=360, scheme=scheme)
            try:
                assert await page.evaluate("document.documentElement.scrollWidth") <= 360
                tops = await page.locator("#levels button").evaluate_all(
                    "bs => bs.map(b => Math.round(b.getBoundingClientRect().top))"
                )
                assert tops == sorted(set(tops))  # stacked, one under the other
                inside = await page.locator("#levels button").evaluate_all(
                    "bs => bs.every(b => b.getBoundingClientRect().right <= 360)"
                )
                assert inside
            finally:
                await browser.close()


async def test_a_phone_sees_what_it_saw_before(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_page(playwright, static_site, width=360, local=False)
        try:
            await page.wait_for_function("() => document.body.classList.contains('remote')")
            assert await page.locator(".locked").is_visible()
            assert not await page.locator("#backup-card").is_visible()
            await page.wait_for_selector("#model-card:not([hidden])")
            assert not await page.locator("#model-edit").is_visible()
            assert await page.locator("#model-remote").is_visible()
            assert await page.locator("#model-now").inner_text() == "qwen-8b.gguf"
            # PC-only controls are shown but cannot be used
            for sel in ("#pair", "#levels button.relaxed"):
                events = await page.locator(sel).evaluate("n => getComputedStyle(n).pointerEvents")
                assert events == "none", sel
            assert await page.locator("#language").is_visible()
            assert await page.evaluate("document.documentElement.scrollWidth") <= 360
        finally:
            await browser.close()
