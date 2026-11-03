"""The Settings page's look (UI block U4) in a real browser: number boxes readable in the dark
look, the permission summary in colour, a keyboard focus ring the segmented buttons do not
clip, and German that reaches every sentence (also one that wraps in the page's source) and
every count."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import CHROMIUM, static_site  # noqa: F401  (a fixture)

TOOLS = [
    ("mail.read", "safe", "safe"),
    ("mail.send", "confirm", "confirm"),
    ("mail.delete", "blocked", "confirm"),
    ("weather.forecast", "safe", "safe"),
]
PAGES = {
    "key": "max_pages",
    "kind": "int",
    "value": 40,
    "title": "Pages per task",
    "help": "",
    "changed": False,
    "live": True,
    "module_on": True,
    "module": "web",
    "warning": "",
    "unit": "pages",
    "low": 1,
    "high": 500,
    "de": {"title": "", "help": "", "warning": ""},
}
DATA = {
    "/api/permissions": {
        "tools": [{"name": n, "description": "", "now": now, "tag": tag} for n, now, tag in TOOLS],
        "summary": {
            "safe": ["mail.read", "weather.*"],
            "confirm": ["mail.send"],
            "blocked": ["mail.delete"],
        },
        "limits": {"mail.send": "20 a day"},
        "practice": False,
    },
    "/api/modules": {
        "modules": [
            {"name": "mail", "description": "Mail", "status": "loaded"},
            {"name": "weather", "description": "Weather", "status": "loaded"},
            {"name": "absolution", "description": "Security tools", "status": "disabled"},
        ]
    },
    "/api/devices": {
        "remote": {"enabled": True, "hosts": ["pc.ts.net"]},
        "devices": [{"id": "d1", "name": "Pixel", "paired": "2026-10-20"}],
    },
    "/api/safety": {
        "levels": ["strict", "balanced", "relaxed"],
        "level": "balanced",
        "changed": 0,
        "local_file": False,
        "groups": [{"title": "Web", "de": "Web", "switches": [PAGES]}],
    },
    "/api/status": {"local": True},
}
FAKE = (
    f"const DATA = {json.dumps(DATA)};\n"
    "window.fetch = async (url) => { const u = new URL(String(url), location.href).pathname;"
    " return new Response(JSON.stringify(DATA[u] || {}), {status: u in DATA ? 200 : 404,"
    " headers: {'content-type': 'application/json'}}); };"
)
CONTRAST = """el => {
  const rgb = c => c.match(/\\d+(\\.\\d+)?/g).slice(0, 3).map(Number);
  const lum = c => { const [r, g, b] = rgb(c).map(v => { v /= 255;
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const s = getComputedStyle(el), a = lum(s.color), b = lum(s.backgroundColor);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}"""


async def settings(playwright: Any, site: str, german: bool = False) -> tuple[Any, Any]:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    page = await browser.new_page(viewport={"width": 1366, "height": 900}, color_scheme="dark")
    if german:
        await page.add_init_script("localStorage.setItem('starlight-lang', 'de');")
    await page.add_init_script(FAKE)
    await page.goto(f"{site}/settings.html")
    await page.wait_for_selector("details.mod")
    await page.wait_for_selector("#safety .srow")
    return browser, page


async def test_settings_reads_well_in_the_dark_and_shows_permissions_in_colour(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await settings(playwright, static_site)
        try:
            number = page.locator("#safety input[type=number]")
            assert await number.evaluate(CONTRAST) >= 4.5  # it was light text on white
            # each group of the summary in its colour, as the buttons below
            safe = await page.locator(".plain .names.safe code").all_text_contents()
            assert safe == ["mail.read", "weather.*"]
            assert await page.locator(".plain .names.blocked code").count() == 1
            assert await page.locator(".plain h3.confirm").count() == 1
            # one tool, several tools; a module that is off says so
            counts = await page.locator(".mcount").all_inner_texts()
            assert counts[1] == "1 tool"
            assert counts[2] == "off"
            # the main action of the devices card stands out; Remove is quieter
            assert await page.locator("#pair").get_attribute("class") == "primary"
            remove = await page.locator("#devices button").get_attribute("class")
            assert remove == "danger-outline"
            # the segmented buttons clip what sticks out: the focus ring sits inside
            await page.locator('details.mod[data-name="mail"] > summary .mname').click()
            await page.keyboard.press("Tab")  # (from here on the keyboard is in use)
            button = page.locator(".trow .seg button").first
            await button.focus()
            offset = await button.evaluate("b => getComputedStyle(b).outlineOffset")
            assert offset == "-3px"
        finally:
            await browser.close()


async def test_settings_is_german_in_every_sentence_and_count(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await settings(playwright, static_site, german=True)
        try:
            await page.wait_for_function(
                "() => document.getElementById('summary').textContent.includes('Modulen')"
            )
            intro = await page.locator(".card.mods > p.dim").inner_text()
            # this sentence wraps in the page's source: it was left in English before
            assert intro.startswith("Schalte ein Modul aus")
            assert "erst fragt oder gesperrt ist" in intro and "Änderungen" in intro
            assert await page.locator("#summary").inner_text() == (
                "2 von 3 Modulen an · 4 Werkzeuge: 1 mit Nachfrage, 1 gesperrt, "
                "der Rest läuft von selbst"
            )
            counts = await page.locator(".mcount").all_inner_texts()
            assert counts == ["3 Werkzeuge · 1 fragt erst · 1 gesperrt", "1 Werkzeug", "aus"]
            # "Zurücksetzen" stays inside the card
            await page.locator('details.mod[data-name="mail"] > summary .mname').click()
            reset = page.locator(".trow .reset:not(:disabled)").first
            assert await reset.inner_text() == "Zurücksetzen"
            inside = await reset.evaluate(
                "r => r.getBoundingClientRect().right"
                " <= r.closest('.card').getBoundingClientRect().right"
            )
            assert inside
            # a key with two spaces in it still matches as it is (the chat's message box)
            await page.goto(f"{static_site}/chat.html")
            await page.wait_for_function(
                "() => !document.getElementById('input').placeholder.startsWith('Ask')"
            )
            placeholder = await page.locator("#input").get_attribute("placeholder") or ""
            assert (
                placeholder
                == "Frag Starlight…  (Enter zum Senden, Umschalt+Enter für eine neue Zeile)"
            )
        finally:
            await browser.close()
