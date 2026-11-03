"""Automations, Memory, Activity and Pair (UI block U5) in a real browser: every text box
readable in the dark look (also one made without a type), quiet Delete buttons in lists, a
skill import the keyboard can reach, and the approval that waits for you standing out."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import CHROMIUM, static_site  # noqa: F401  (a fixture)
from tests.test_settings_look import CONTRAST

DATA = {
    "/api/status": {"local": True, "kill": {"engaged": False}},
    "/api/automations": {
        "rules": [
            {
                "name": "usb-backup",
                "enabled": True,
                "does": "when a USB drive is plugged in",
                "may_run_without_asking": [],
            },
        ],
        "events": {"usb": {"when": "a USB drive is plugged in", "fields": ["label"]}},
        "tools": [],
        "templates": {},
    },
    "/api/memory": {
        "count": 1,
        "memories": [{"id": "m1", "topic": "Garden", "content": "Tomatoes", "source": "chat"}],
    },
    "/api/skills": {
        "skills": [
            {
                "name": "weekly-report",
                "status": "approved",
                "description": "Report",
                "instructions": "1. Read",
                "version": 3,
                "uses": 1,
            },
        ]
    },
    "/api/activity": {"entries": []},
    "/api/approvals": {
        "items": [{"id": "a1", "title": "Send the report", "created": "", "runs": "mail.send"}]
    },
}
FAKE = (
    f"const DATA = {json.dumps(DATA)};\n"
    "window.fetch = async (url) => { const u = new URL(String(url), location.href).pathname;"
    " const hit = Object.keys(DATA).find(k => u === k);"
    " return new Response(JSON.stringify(hit ? DATA[hit] : {}), {status: hit ? 200 : 404,"
    " headers: {'content-type': 'application/json'}}); };"
)


async def open_page(playwright: Any, site: str, name: str) -> tuple[Any, Any]:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    page = await browser.new_page(viewport={"width": 1200, "height": 900}, color_scheme="dark")
    await page.add_init_script(FAKE)
    await page.goto(f"{site}/{name}.html")
    return browser, page


async def test_every_text_box_reads_well_in_the_dark(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_page(playwright, static_site, "pair")
        try:
            # the pairing code's box had no type, so no style: light text on white
            assert await page.locator("#code").evaluate(CONTRAST) >= 4.5
            assert await page.locator("#go").get_attribute("class") == "primary"
            # an automation's condition: its value box is made without a type, too
            await page.goto(f"{static_site}/automations.html")
            await page.wait_for_selector(".rule")
            await page.check('input[name="trigger"][value="on"]')
            await page.click("#add-if")
            value = page.locator(".if-value")
            assert await value.evaluate(CONTRAST) >= 4.5
            background = await value.evaluate("v => getComputedStyle(v).backgroundColor")
            assert background != "rgb(255, 255, 255)"
            # check boxes and radio buttons in Starlight's accent
            radio = page.locator('input[name="trigger"]').first
            accent = await radio.evaluate("r => getComputedStyle(r).accentColor")
            assert accent == "rgb(110, 168, 254)"
            # deleting a rule is a quiet red outline (it asks first anyway)
            delete = page.locator(".rule .actions button", has_text="Delete")
            assert await delete.get_attribute("class") == "danger-outline"
        finally:
            await browser.close()


async def test_memory_lists_stay_calm_and_the_import_is_a_real_button(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_page(playwright, static_site, "memory")
        try:
            await page.wait_for_selector(".mem")
            await page.wait_for_selector(".skill")
            deletes = page.locator("button", has_text="Delete")
            classes = await deletes.evaluate_all("bs => bs.map(b => b.className)")
            assert classes == ["danger-outline", "danger-outline"]
            # Import a skill: a button the keyboard reaches, which opens the file box
            button = page.locator("#importbutton")
            assert await button.evaluate("b => b.tagName") == "BUTTON"
            await page.evaluate(
                "() => { document.getElementById('importskill').click = () => window.opened = 1; }"
            )
            await button.focus()
            await page.keyboard.press("Enter")
            assert await page.evaluate("() => window.opened") == 1
            # Export is a link that sizes like the buttons beside it
            buttons = page.locator(".skill .row", has=page.locator("a.button-link")).first
            sizes = await buttons.evaluate(
                "r => [...r.children].map(c => Math.round(c.getBoundingClientRect().height))"
            )
            assert max(sizes) - min(sizes) <= 1, sizes
        finally:
            await browser.close()


async def test_the_approval_that_waits_for_you_stands_out(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_page(playwright, static_site, "activity")
        try:
            await page.wait_for_selector("#approvals:not([hidden]) .waiting")
            edge = await page.locator("#approvals").evaluate("c => getComputedStyle(c).boxShadow")
            assert "rgb(210, 153, 34)" in edge  # an amber edge (--warn)
        finally:
            await browser.close()
