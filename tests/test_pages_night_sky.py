"""Block V5 of the UI 2 plan (docs/plan-ui-2.md): Memory, Activity, Automations, Kill and Pair in
the Night Sky look: the star and the title in the display font, one gold button at most, no
colour written out in a page's own style (the kill switch's fixed red excepted), the kill
switch's Kill and "Stopped" looks apart, and nothing sideways at 360 px."""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_chat_page import CHROMIUM, static_site  # noqa: F401  (a fixture)
from tests.test_pages_look import DATA

STATIC = REPO / "starlight" / "web" / "static"
PAGES = ("memory", "activity", "automations", "kill", "pair")
COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")


def style_blocks(name: str) -> str:
    html = (STATIC / f"{name}.html").read_text(encoding="utf-8")
    css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # (a comment may name a colour)


@pytest.mark.parametrize("name", PAGES)
def test_no_colour_is_written_out_in_a_pages_own_style(name: str) -> None:
    found = []
    for line in style_blocks(name).splitlines():
        if not COLOUR.search(line):
            continue
        # the kill switch keeps one fixed red edge, named once in a documented variable
        if name == "kill" and line.strip().startswith("body { --kill-edge:"):
            continue
        found.append(line.strip())
    assert not found, found
    if name == "kill":
        html = (STATIC / "kill.html").read_text(encoding="utf-8")
        assert "fixed red identity" in html  # (the comment that says why)


async def open_at(
    playwright: Any,
    site: str,
    name: str,
    *,
    width: int = 1200,
    scheme: str = "dark",
    kill_engaged: bool = False,
) -> tuple[Any, Any]:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    data = copy.deepcopy(DATA)
    data["/api/status"]["kill"]["engaged"] = kill_engaged
    data["/api/memory"]["memories"][0] |= {"date": "2026-11-10", "expires": "2026-12-01"}
    data["/api/activity"] = {
        "entries": [{"time": "2026-11-12T10:00:00", "kind": "action", "text": "mail.send ok"}]
    }
    fake = (
        f"const DATA = {json.dumps(data)};\n"
        "window.fetch = async (url) => { const u = new URL(String(url), location.href).pathname;"
        " const hit = Object.keys(DATA).find(k => u === k);"
        " return new Response(JSON.stringify(hit ? DATA[hit] : {}), {status: hit ? 200 : 404,"
        " headers: {'content-type': 'application/json'}}); };"
    )
    browser = await playwright.chromium.launch(executable_path=executable)
    page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    await page.add_init_script(fake)
    await page.goto(f"{site}/{name}.html")
    await page.wait_for_load_state("load")
    return browser, page


async def test_each_page_shows_the_star_and_a_title_in_the_display_font(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for name in PAGES:
            browser, page = await open_at(playwright, static_site, name)
            try:
                await page.wait_for_selector("h1 svg.ico .star", state="attached")
                font = await page.locator("h1").evaluate("h => getComputedStyle(h).fontFamily")
                assert "Space Grotesk" in font, (name, font)
            finally:
                await browser.close()


async def test_a_view_has_at_most_one_gold_button(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for name in PAGES:
            browser, page = await open_at(playwright, static_site, name)
            try:
                await page.wait_for_timeout(300)  # (the lists draw their rows)
                assert await page.locator("button.primary").count() <= 1, name
                if name == "automations":  # Save is the main action of the form
                    assert await page.locator("#save.primary").count() == 1
                if name == "pair":
                    assert await page.locator("#go.primary").count() == 1
            finally:
                await browser.close()


async def test_numbers_are_in_mono(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for name, selector in (
            ("memory", "#count"),
            ("memory", ".mem .num"),
            ("activity", ".entry .when"),
            ("activity", "#count"),
        ):
            browser, page = await open_at(playwright, static_site, name)
            try:
                await page.wait_for_selector(selector)
                await page.wait_for_function(
                    "s => document.querySelector(s).textContent.trim() !== ''", arg=selector
                )
                font = await page.locator(selector).first.evaluate(
                    "n => getComputedStyle(n).fontFamily"
                )
                assert "mono" in font.lower() or "consolas" in font.lower(), (name, selector)
            finally:
                await browser.close()


async def test_the_kill_button_and_the_stopped_state_look_apart(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    look = "b => { const s = getComputedStyle(b); return [s.backgroundColor, s.color]; }"
    async with async_api.async_playwright() as playwright:
        seen = {}
        for scheme in ("dark", "light"):
            browser, page = await open_at(playwright, static_site, "kill", scheme=scheme)
            try:
                seen[scheme] = await page.locator("#stop").evaluate(look)
            finally:
                await browser.close()
        # the red does not change with the look
        assert seen["dark"] == seen["light"] == ["rgb(180, 35, 24)", "rgb(255, 255, 255)"]
        browser, page = await open_at(playwright, static_site, "kill", kill_engaged=True)
        try:
            await page.wait_for_selector("#stop.done")
            assert await page.locator("#stop").evaluate(look) != seen["dark"]
        finally:
            await browser.close()


async def test_nothing_scrolls_sideways_at_360_px(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for name in PAGES:
            for scheme in ("dark", "light"):
                browser, page = await open_at(
                    playwright, static_site, name, width=360, scheme=scheme
                )
                try:
                    await page.wait_for_timeout(300)
                    wide = await page.evaluate("document.documentElement.scrollWidth")
                    assert wide <= 360, (name, scheme, wide)
                finally:
                    await browser.close()
