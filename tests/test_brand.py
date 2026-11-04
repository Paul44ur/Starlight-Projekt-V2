"""Icons and brand (UI block U9): app icons a phone takes (PNG, from the two SVG logos), the
browser's bar in the colour of the page, and one set of line icons for the buttons that carry
no words."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from starlight.web import app as web
from starlight.web.app import STATIC
from tests.test_chat_page import static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import CHAT, browser_page
from tests.test_notes_page import app as app  # (a fixture)
from tests.test_notes_page import notebook as notebook  # (a fixture)
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL, client, home

PAGES = ("chat", "notes", "settings", "memory", "activity", "automations", "pair")
# Emoji and the symbol glyphs the pages used before (a button without words shows an icon).
GLYPH = re.compile("[\u2190-\u2bff\U0001f300-\U0001faff\uff0b]")


def test_the_app_icons_are_pictures_a_phone_takes() -> None:
    for name, size in web.APP_ICONS.items():
        picture = Image.open(STATIC / "icons" / name)
        assert picture.size == (size, size) and picture.mode == "RGBA", name
        corner = picture.getpixel((0, 0))[3]
        if name.endswith("-180.png") or "maskable" in name:
            assert corner == 255, name  # square: the phone rounds it (an iPhone paints black)
        else:
            assert corner == 0, name  # rounded, like the SVG
        middle = picture.getpixel((size // 2, size // 2))
        assert middle[3] == 255, name
    # drawn from the logos as they are now (else: run bundles/icons/make_icons.py)
    recorded = (STATIC / "icons" / "icons.sha256").read_text("utf-8").split()
    for logo in ("icon.svg", "kill-icon.svg"):
        digest = hashlib.sha256((STATIC / logo).read_bytes()).hexdigest()
        assert recorded[recorded.index(logo) - 1] == digest, logo


def test_the_manifests_and_pages_name_the_app_icons(tmp_path: Path) -> None:
    with client(home(tmp_path / "h", remote=False)) as c:
        for name in web.APP_ICONS:
            answer = c.get(f"/icons/{name}", headers=LOCAL)
            assert answer.status_code == 200 and answer.headers["content-type"] == "image/png"
        for other in ("icons.sha256", "nothing.png", "..%2Fapp.py"):
            assert c.get(f"/icons/{other}", headers=LOCAL).status_code == 404, other
        for manifest, logo in (("manifest.webmanifest", "icon"), ("kill.webmanifest", "kill-icon")):
            icons = c.get(f"/{manifest}", headers=LOCAL).json()["icons"]
            found = {(i["src"], i["sizes"], i.get("purpose", "any")) for i in icons}
            assert (f"/icons/{logo}-192.png", "192x192", "any") in found, manifest
            assert (f"/icons/{logo}-512.png", "512x512", "any") in found, manifest
            assert (f"/icons/{logo}-maskable-512.png", "512x512", "maskable") in found, manifest
            for src, _, _ in found:
                assert c.get(src, headers=LOCAL).status_code == 200, src
        shortcuts = c.get("/manifest.webmanifest", headers=LOCAL).json()["shortcuts"]
        assert all(any(i["type"] == "image/png" for i in s["icons"]) for s in shortcuts)
    for name in PAGES:
        page = (STATIC / f"{name}.html").read_text("utf-8")
        assert '<link rel="apple-touch-icon" href="/icons/icon-180.png" />' in page, name
    kill = (STATIC / "kill.html").read_text("utf-8")
    assert '<link rel="apple-touch-icon" href="/icons/kill-icon-180.png" />' in kill


async def test_the_browser_bar_takes_the_colour_of_the_page(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    bar = "() => document.querySelector('meta[name=theme-color]').content"
    async with async_api.async_playwright() as playwright:
        for url in ("chat.html", "settings.html"):
            browser, page = await browser_page(
                playwright, static_site, url, CHAT, color_scheme="light"
            )
            try:
                await page.wait_for_load_state("load")
                assert await page.evaluate(bar) == "#ffffff", url  # like the system: light
                await page.evaluate("() => cycleTheme()")  # light
                assert await page.evaluate(bar) == "#ffffff", url
                await page.evaluate("() => cycleTheme()")  # dark
                assert await page.evaluate(bar) == "#171a21", url
                assert await page.locator("meta[name=theme-color]").count() == 1
            finally:
                await browser.close()
        # the kill switch keeps its red, whatever the theme
        browser, page = await browser_page(playwright, static_site, "kill.html", CHAT)
        try:
            await page.wait_for_load_state("load")
            await page.evaluate("() => cycleTheme()")
            assert await page.evaluate(bar) == "#b3231d"
            # and it says whose switch it is
            assert "Starlight" in await page.locator("h1").inner_text()
            assert await page.locator("h1 img.logo").is_visible()
        finally:
            await browser.close()


async def test_buttons_without_words_show_the_one_icon_set(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    plain = (
        "els => els.filter(b => b.offsetParent).map(b => [b.id || b.className,"
        " !!b.querySelector(':scope > svg.ico'), b.getAttribute('aria-label') || b.title,"
        " [...b.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent).join('')])"
    )
    async with async_api.async_playwright() as playwright:
        for url in ("chat.html", "settings.html", "memory.html", "activity.html",
                    "automations.html"):  # fmt: skip
            browser, page = await browser_page(playwright, static_site, url, CHAT)
            try:
                await page.wait_for_load_state("load")
                buttons = await page.locator("button.icon").evaluate_all(plain)
                assert buttons, url
                for name, has_icon, label, text in buttons:
                    if not GLYPH.sub("", text).strip():  # no words: an icon and a name
                        assert has_icon and label and not GLYPH.search(text), (url, name, text)
            finally:
                await browser.close()
        browser, page = await browser_page(playwright, static_site, "chat.html", CHAT)
        try:
            await page.wait_for_selector(".conv")
            for button in ("#menu", "#new", "#newprivate", "#attach", "#camera", "#mic",
                           "#speaker", "#theme", "#tolatest", ".conv .acts button"):  # fmt: skip
                count = await page.locator(f"{button} > svg.ico").count()
                assert count >= 1, button
            speaker = page.locator("#speaker")
            assert await speaker.get_attribute("data-icon") == "mute"
            await page.evaluate("() => cycleTheme()")
            assert await page.locator("#theme").get_attribute("data-icon") == "sun"
            assert await page.locator("#theme2").inner_text() == "Theme"  # an icon and a word
        finally:
            await browser.close()


async def test_the_notes_page_uses_the_same_icons(
    static_site: str,  # noqa: F811
    app: Any,
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FShopping.md")
        await page.wait_for_selector("h1.title")
        for button in ("#new-note", "#find", "#memo", "#theme", "#info-toggle", "#export-toggle",
                       "#link-copy", ".quick button[data-view=today]",
                       ".quick button[data-view=cards]"):  # fmt: skip
            assert await page.locator(f"{button} > svg.ico").count() == 1, button
        assert await page.locator("#find kbd").inner_text() == "Ctrl K"  # icon and keys
        assert await page.locator(".quick button[data-view=today]").inner_text() == "Today"
        assert page.errors == []  # type: ignore[attr-defined]
