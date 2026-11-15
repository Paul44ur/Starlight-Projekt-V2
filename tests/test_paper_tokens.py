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


def test_every_token_of_the_plan_has_its_light_and_dark_value() -> None:
    for name, (light, dark) in COLOURS.items():
        assert tokens("light")[name] == light, name
        assert tokens("dark")[name] == dark, name
        # the dark look chosen by the system (no data-theme) is the same look
        if name != "danger":  # (not repeated: same in both looks)
            assert token(SYSTEM_DARK, name) == dark, name
    for name, value in SAME.items():
        assert token(LIGHT, name) == value, name
        assert name not in DARK and name not in SYSTEM_DARK  # (one value for both looks)


def test_the_radius_is_two_pixels_everywhere() -> None:
    for name in ("r-1", "r-2", "r-3", "r-4", "r-pill"):
        assert token(LIGHT, name) == "2px", name
    assert not re.search(r"--r-\d:", DARK + SYSTEM_DARK)


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


def test_the_main_action_is_the_filled_accent() -> None:
    body = rule_of(r"button\.primary")
    assert "background: var(--primary)" in body and "color: var(--on-primary)" in body
    assert "border-color: var(--primary-edge)" in body
    plain = rule_of(r"button")
    assert "border-radius: var(--r-pill)" in plain and "background: none" in plain
    assert "1px solid var(--text)" in plain  # (a thin line in the ink colour)


def test_the_shared_components_use_the_plan_values() -> None:
    def rule(selector: str) -> str:
        found = re.search(rf"\n{re.escape(selector)} \{{([^}}]*)\}}", CSS)
        assert found, selector
        return found.group(1)

    card = rule(".card")  # a section: a thin rule, no box, no shadow
    assert "box-shadow" not in card and "border-top: 1px solid var(--border)" in card
    assert "background: none" in card and "border-radius: 0" in card
    for selector in ("dialog.ask", "dialog#quick", "dialog#shortcuts"):
        body = rule(selector)
        assert "background: var(--panel)" in body, selector
        assert "1px solid var(--border)" in body, selector
        assert "border-radius: var(--r-2)" in body and "box-shadow: var(--shadow-2)" in body
    assert "outline: 2px solid var(--accent)" in rule(":focus-visible")
    assert "border-radius: var(--r-2)" in rule_of(r"[^{}]*textarea, select,\n[^{]*")
    assert "font-family: var(--font-display)" in rule("h1, h2")
    assert "font: var(--fs-md)/1.5 var(--font-body)" in rule("body")
    assert "var(--font-mono)" in rule("code, pre, samp")
    # the helpers
    num = rule(".num")
    assert "var(--font-mono)" in num and "tabular-nums" in num
    label = rule(".section-label")  # small italic serif, normal case
    assert "font-style: italic" in label and "var(--font-display)" in label
    assert "text-transform" not in label and "letter-spacing" not in label
    assert "var(--dim)" in label
    assert "var(--panel2)" in rule("kbd")
    assert "#shortcuts kbd" not in CSS  # (the shortcuts window uses the shared style)


def test_shadows_are_none_at_rest_and_faint_for_what_floats() -> None:
    assert token(LIGHT, "shadow-1") == "none"
    for text in (LIGHT, DARK):
        assert re.search(r"--shadow-2:\s*0 6px 24px rgb\(", text)
    assert "box-shadow: var(--shadow-1)" not in CSS


def test_no_radius_in_style_css_is_larger_than_two_pixels() -> None:
    for value in re.findall(r"border-radius:\s*([^;}]+)", CSS):
        value = value.strip()
        assert value in (
            "var(--r-1)",
            "var(--r-2)",
            "var(--r-3)",
            "var(--r-4)",
            "var(--r-pill)",
            "0",
            "50%",
            "2px",
        ), value  # (50%: a circle)


def test_style_css_keeps_no_colour_of_its_own_outside_the_tokens() -> None:
    """Colours are tokens. The only literal colours left are the token values themselves
    (the three look blocks and the high-contrast overrides) and the scrim's fallback."""
    outside = CSS
    for opening in (":root {", ':root[data-theme="dark"] {', '  :root:not([data-theme="light"]) {'):
        start = outside.index(opening)
        end = outside.index("\n  }" if opening.startswith("  ") else "\n}", start)
        outside = outside[:start] + outside[end:]
    outside = re.sub(r"@media \(prefers-contrast: more\) \{.*?\n\}", "", outside, flags=re.S)
    outside = outside.replace("rgb(0 0 0 / .5)", "")  # the scrim's fallback (old browsers)
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", outside) == []


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


def test_no_page_or_script_names_the_old_heading_font() -> None:
    names = ("space grotesk", "space-grotesk", "@font-face", ".woff2")
    files = [
        *STATIC.glob("*.html"),
        *STATIC.glob("*.css"),
        *STATIC.glob("*.js"),
        REPO / "starlight/viewer/static/index.html",
    ]
    for file in files:
        text = file.read_text(encoding="utf-8").lower()
        for name in names:
            assert name not in text, (file.name, name)
    assert not list(VENDOR.glob("*.woff2")) and not (VENDOR / "fonts.sha256").exists()
    assert not (REPO / "bundles" / "fonts").exists()


def test_the_web_app_serves_no_font(app: Any) -> None:
    for name in (
        "space-grotesk-latin.woff2",
        "space-grotesk-latin-ext.woff2",
        "space-grotesk-LICENSE.txt",
        "fonts.sha256",
    ):
        assert app.get(f"/vendor/{name}", headers=LOCAL).status_code == 404, name
    worker = app.get("/sw.js", headers=LOCAL).text
    assert "woff2" not in worker and "starlight-shell-v7" in worker  # (raised: list changed)


def test_the_viewer_serves_no_font() -> None:
    from starlight.viewer.app import SHARED
    from starlight.web.app import VENDOR as WEB_VENDOR

    assert not [name for name in SHARED if name.endswith(".woff2")]
    assert not [name for name in WEB_VENDOR if name.endswith(".woff2") or "grotesk" in name]


@pytest.mark.parametrize("path", ["/style.css"])
def test_the_stylesheet_is_served_with_the_new_tokens(app: Any, path: str) -> None:
    css = app.get(path, headers=LOCAL).text
    assert "--accent: #2f5d50" in css and "--font-display: Charter" in css
