"""Block P1 of the UI 3 plan (docs/plan-ui-3.md): the Paper tokens in style.css (light as the
default, dark as its twin), no bundled font, and the shared components."""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_chat_page import static_site  # noqa: F401  (a fixture)
from tests.test_feedback_look import browser_page
from tests.test_notes_page import app as app  # (the fixture)
from tests.test_notes_page import notebook as notebook  # (the fixture)
from tests.test_remote import LOCAL

STATIC = REPO / "starlight" / "web" / "static"
VENDOR = STATIC / "vendor"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")

# Plan section 2: token -> (light, dark).
COLOURS = {
    "bg": ("#f6f3ec", "#1d1b18"),
    "panel": ("#fbf9f4", "#23211d"),
    "panel2": ("#efeadf", "#2c2924"),
    "border": ("#ddd6c8", "#3a362f"),
    "text": ("#26221d", "#ece6da"),
    "dim": ("#665e53", "#a99f8f"),
    "accent": ("#2f5d50", "#8dbdaa"),
    "on-accent": ("#fbf9f4", "#1d1b18"),
    "ok": ("#2e6b3f", "#8fc79c"),
    "warn": ("#8a5a12", "#e0b46a"),
    "bad": ("#a3361e", "#f0a08a"),
    "danger": ("#a3361e", "#a3361e"),
}
SAME = {
    "font-display": 'Charter, "Bitstream Charter", Cambria, Georgia, serif',
    "font-body": (
        '"Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif'
    ),
    "font-mono": 'ui-monospace, "Cascadia Mono", Consolas, monospace',
    "primary": "var(--accent)",
    "on-primary": "var(--on-accent)",
    "primary-edge": "transparent",
    "shadow-1": "none",
}


def block(opening: str) -> str:
    """The text of the first rule that starts with this selector, up to its closing brace."""
    start = CSS.index(opening) + len(opening)
    return CSS[start : CSS.index("\n  }" if opening.startswith("  ") else "\n}", start)]


LIGHT = block(":root {")
DARK = block(':root[data-theme="dark"] {')
SYSTEM_DARK = block('  :root:not([data-theme="light"]) {')


def token(text: str, name: str) -> str:
    found = re.search(rf"--{re.escape(name)}:\s*([^;]+);", text)
    assert found, name
    return found.group(1).strip()


def tokens(look: str) -> dict[str, str]:
    """A look's tokens: the dark look is the light one with the dark block laid over it."""
    values = dict(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", LIGHT))
    if look == "dark":
        values.update(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", DARK))
    return {k: v.strip() for k, v in values.items()}


def luminance(colour: str) -> float:
    parts = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [p / 12.92 if p <= 0.04045 else ((p + 0.055) / 1.055) ** 2.4 for p in parts]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_the_two_dark_blocks_say_the_same() -> None:
    def parsed(text: str) -> dict[str, str]:
        return {k: v.strip() for k, v in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", text)}

    assert parsed(DARK) == parsed(SYSTEM_DARK)
    assert "color-scheme: dark" in DARK and "color-scheme: light" in LIGHT


def test_the_text_colours_are_readable_on_every_surface_in_both_looks() -> None:
    for look in ("light", "dark"):
        t = tokens(look)
        for text in ("text", "dim", "accent", "ok", "warn", "bad"):
            for surface in ("bg", "panel", "panel2"):
                ratio = contrast(t[text], t[surface])
                assert ratio >= 4.5, (look, text, surface, round(ratio, 2))
        # the fills behind text
        assert contrast(t["on-accent"], t["accent"]) >= 4.5, look  # the main action, switches
        assert contrast(t["on-accent"], t["ok"]) >= 4.5, look  # pill.on, button.ok
        assert contrast(t["on-accent"], t["warn"]) >= 4.5, look  # pill.warn
        assert contrast(t["on-danger"], t["danger"]) >= 4.5, look


def test_the_code_colours_are_readable_in_both_looks() -> None:
    for look in ("light", "dark"):
        t = tokens(look)
        names = [n for n in t if n.startswith("hl-")]
        assert len(names) == 6, names
        for name in names:
            for surface in ("panel", "panel2"):
                assert contrast(t[name], t[surface]) >= 4.5, (look, name, surface)


def rule_of(selector_pattern: str) -> str:
    found = re.search(rf"\n{selector_pattern} \{{([^}}]*)\}}", CSS)
    assert found, selector_pattern
    return found.group(1)


def test_the_brand_is_the_word_only_and_the_star_icon_is_gone() -> None:
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    assert not re.search(r"^\s*star:", common, flags=re.M)
    for page in [*sorted(STATIC.glob("*.html")), REPO / "starlight/viewer/static/index.html"]:
        text = page.read_text(encoding="utf-8")
        assert 'data-icon="star"' not in text and "brand-star" not in text, page.name
        assert 'src="/icon.svg"' not in text, page.name  # (no logo picture in a page)
    for script in ("chat-page.js", "chat-messages.js"):
        assert "/icon.svg" not in (STATIC / script).read_text(encoding="utf-8"), script
    assert "empty-logo" not in (STATIC / "chat.css").read_text(encoding="utf-8")


def test_every_page_has_the_new_theme_colours() -> None:
    pages = sorted(STATIC.glob("*.html"))
    assert len(pages) >= 8
    for page in pages:
        text = page.read_text(encoding="utf-8")
        if page.name == "kill.html":  # keeps its red (data-fixed)
            assert 'content="#b3231d" data-fixed' in text
            continue
        for colour, scheme in (("#fbf9f4", "light"), ("#23211d", "dark")):
            media = f"(prefers-color-scheme: {scheme})"
            meta = f'<meta name="theme-color" content="{colour}" media="{media}" />'
            assert meta in text, (page.name, scheme)


# -- the default look is light; "dark" and "system" still work --------------------------------


async def _look(
    playwright: Any, site: str, stored: str | None, system: str, monkeypatch: pytest.MonkeyPatch
) -> str:
    script = f"localStorage.setItem('starlight-theme', '{stored}')" if stored else ""
    browser, page = await browser_page(playwright, site, "memory.html", script, color_scheme=system)
    await page.wait_for_selector("header.top")
    colour: str = await page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--bg').trim()"
    )
    await browser.close()
    return colour


async def test_the_default_is_light_and_dark_and_system_still_work(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:

        async def look(stored: str | None, system: str) -> str:
            return await _look(playwright, static_site, stored, system, monkeypatch)

        assert await look(None, "no-preference") == "#f6f3ec"  # the default: paper
        assert await look("light", "dark") == "#f6f3ec"  # a stored choice wins
        assert await look("dark", "light") == "#1d1b18"
        assert await look(None, "dark") == "#1d1b18"  # "like your system"
        assert await look(None, "light") == "#f6f3ec"


# -- no font is bundled ------------------------------------------------------------------------


def test_the_web_app_serves_no_font(app: Any) -> None:
    for name in (
        "space-grotesk-latin.woff2",
        "space-grotesk-latin-ext.woff2",
        "space-grotesk-LICENSE.txt",
        "fonts.sha256",
    ):
        assert app.get(f"/vendor/{name}", headers=LOCAL).status_code == 404, name
    worker = app.get("/sw.js", headers=LOCAL).text
    assert "woff2" not in worker


def test_the_viewer_serves_no_font() -> None:
    from starlight.viewer.app import SHARED
    from starlight.web.app import VENDOR as WEB_VENDOR

    assert not [name for name in SHARED if name.endswith(".woff2")]
    assert not [name for name in WEB_VENDOR if name.endswith(".woff2") or "grotesk" in name]
