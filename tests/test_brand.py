"""Icons and brand (UI block U9): app icons a phone takes (PNG, from the two SVG logos), the
browser's bar in the colour of the page, and one set of line icons for the buttons that carry
no words."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest

from starlight.web import app as web
from starlight.web.app import STATIC
from tests.test_chat_page import SEND, static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import CHAT, browser_page
from tests.test_notes_page import app as app  # (a fixture)
from tests.test_notes_page import notebook as notebook  # (a fixture)
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL, client, home

PAGES = ("chat", "notes", "settings", "memory", "activity", "automations", "pair")
# Emoji and the symbol glyphs the pages used before (a button without words shows an icon).
GLYPH = re.compile("[\u2190-\u2bff\U0001f300-\U0001faff\uff0b]")


def test_the_app_icons_are_pictures_a_phone_takes() -> None:
    image = pytest.importorskip("PIL.Image")  # (Pillow comes with the desktop extra)
    for name, size in web.APP_ICONS.items():
        picture = image.open(STATIC / "icons" / name)
        assert picture.size == (size, size) and picture.mode in ("RGB", "RGBA"), name
        picture = picture.convert("RGBA")
        corner = picture.getpixel((0, 0))[3]
        if name.endswith("-180.png") or "maskable" in name:
            assert corner == 255, name  # square: the phone rounds it (an iPhone paints black)
        else:
            assert corner == 0, name  # rounded, like the SVG
        middle = picture.getpixel((size // 2, size // 2))
        assert middle[3] == 255, name


def logo_digest(data: bytes) -> str:
    """As bundles/icons/make_icons.py counts it: a Windows checkout's CRLF is the same logo."""
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def test_the_app_icons_were_drawn_from_the_logos_as_they_are() -> None:
    # (else: run bundles/icons/make_icons.py)
    recorded = (STATIC / "icons" / "icons.sha256").read_text("utf-8").split()
    for logo in ("icon.svg", "kill-icon.svg"):
        data = (STATIC / logo).read_bytes()
        assert recorded[recorded.index(logo) - 1] == logo_digest(data), logo
        assert logo_digest(data.replace(b"\n", b"\r\n")) == logo_digest(data)  # (Windows)
    script = (STATIC.parents[2] / "bundles" / "icons" / "make_icons.py").read_text("utf-8")
    assert 'replace(b"\\r\\n", b"\\n")' in script  # it counts them the same way


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
        for scheme, colour in (("light", "#ffffff"), ("dark", "#171a21")):
            media = f"(prefers-color-scheme: {scheme})"
            meta = f'<meta name="theme-color" content="{colour}" media="{media}" />'
            assert meta in page, (name, scheme)  # (right before the scripts run)
    kill = (STATIC / "kill.html").read_text("utf-8")
    assert '<link rel="apple-touch-icon" href="/icons/kill-icon-180.png" />' in kill


async def test_the_browser_bar_takes_the_colour_of_the_page(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    bar = (
        "() => [...new Set([...document.querySelectorAll('meta[name=theme-color]')]"
        ".map(m => m.content))].join()"
    )
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


async def test_stop_takes_a_click_while_an_answer_streams(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    sent = CHAT.replace("send() {}", "send(d) { (window.sent = window.sent || []).push(d); }")
    async with async_api.async_playwright() as playwright:
        browser, page = await browser_page(playwright, static_site, "chat.html", sent)
        try:
            await page.wait_for_selector(".conv")
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
            stop = page.locator("#send")
            assert await stop.get_attribute("data-icon") == "stop"
            word = await stop.locator(".label").bounding_box()
            assert word
            # words keep arriving every 10 ms, while the mouse is held on the word "Stop"
            await page.evaluate(
                "() => window.ticker = setInterval(() => window.sockets[window.sockets.length - 1]"
                ".onmessage({data: JSON.stringify({type: 'token', text: 'word '})}), 10)"
            )
            for _ in range(3):
                await page.mouse.move(word["x"] + word["width"] / 2, word["y"] + word["height"] / 2)
                await page.mouse.down()
                await page.wait_for_timeout(80)
                await page.mouse.up()
            await page.evaluate("() => clearInterval(window.ticker)")
            stops = await page.evaluate("() => (window.sent || []).filter(d => d.includes('stop'))")
            assert len(stops) == 3  # every click reached Stop
        finally:
            await browser.close()


async def test_an_icon_alone_is_larger_than_one_beside_words(
    static_site: str,  # noqa: F811
    app: Any,
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    width = "s => Math.round(s.getBoundingClientRect().width * 10) / 10"
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FShopping.md")
        await page.wait_for_selector("h1.title")
        assert await page.locator("#memo > svg.ico.alone").evaluate(width) == 18
        today = page.locator(".quick button[data-view=today] > svg.ico")
        assert await today.evaluate("s => !s.classList.contains('alone')")
        assert await today.evaluate(width) == 16.8  # 1.2em of its 14 px words
        find = page.locator("#find > svg.ico")  # beside its keys: a little room
        assert await find.evaluate("s => getComputedStyle(s).marginRight") != "0px"
        # the editor's toolbar: the task and picture buttons are line icons too
        await page.locator("#edit-toggle").click()
        for tool in ("task", "picture"):
            button = page.locator(f".toolbar .tool[data-tool={tool}]")
            await button.wait_for()
            assert await button.locator("svg.ico.alone").count() == 1, tool
            assert (await button.text_content() or "") == "", tool
        assert page.errors == []  # type: ignore[attr-defined]
