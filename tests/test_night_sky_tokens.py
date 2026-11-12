"""Block V1 of the UI 2 plan (docs/plan-ui-2.md): the Night Sky tokens in style.css (night and
day), the heading font bundled in bundles/fonts, and the shared components."""

from __future__ import annotations

import hashlib
import re
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_notes_page import app as app  # (the fixture)
from tests.test_notes_page import notebook as notebook  # (the fixture)
from tests.test_remote import LOCAL

STATIC = REPO / "starlight" / "web" / "static"
VENDOR = STATIC / "vendor"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")

# Plan section 2: token -> (night, day). Fonts and --r-4 are the same in both looks.
COLOURS = {
    "bg": ("#0b0e1a", "#f5f6fb"),
    "panel": ("#12162a", "#ffffff"),
    "panel2": ("#1a1f38", "#ecedf7"),
    "border": ("#262c4a", "#d8dbea"),
    "text": ("#e8eaf6", "#14172b"),
    "dim": ("#9aa1c4", "#4e5578"),
    "accent": ("#a99bff", "#5543d1"),
    "on-accent": ("#0b0e1a", "#ffffff"),
    "you": ("#1a1f38", "#ecebfd"),
    "ok": ("#6ee7b7", "#157a4b"),
    "warn": ("#fdba74", "#a3510b"),
    "bad": ("#ff8a80", "#c0262d"),
    "danger": ("#b42318", "#b42318"),
    "primary": ("#f2c14e", "#f2c14e"),
    "on-primary": ("#1a1404", "#1a1404"),
    "primary-edge": ("transparent", "#b8860b"),
}
SAME = {
    "font-display": '"Space Grotesk", var(--font-body)',
    "font-body": (
        '"Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, Roboto, sans-serif'
    ),
    "font-mono": 'ui-monospace, "Cascadia Mono", Consolas, monospace',
    "r-4": "16px",
}


def block(opening: str) -> str:
    """The text of the first rule that starts with this selector, up to its closing brace."""
    start = CSS.index(opening) + len(opening)
    return CSS[start : CSS.index("\n  }" if opening.startswith("  ") else "\n}", start)]


NIGHT = block(":root {")
DAY = block(':root[data-theme="light"] {')
SYSTEM_DAY = block('  :root:not([data-theme="dark"]) {')


def token(text: str, name: str) -> str:
    found = re.search(rf"--{re.escape(name)}:\s*([^;]+);", text)
    assert found, name
    return found.group(1).strip()


def tokens(look: str) -> dict[str, str]:
    """A look's tokens: the day look is the night one with the day block laid over it."""
    values = dict(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", NIGHT))
    if look == "day":
        values.update(re.findall(r"--([a-z0-9-]+):\s*([^;]+);", DAY))
    return {k: v.strip() for k, v in values.items()}


def luminance(colour: str) -> float:
    parts = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [p / 12.92 if p <= 0.04045 else ((p + 0.055) / 1.055) ** 2.4 for p in parts]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_every_token_of_the_plan_has_its_night_and_day_value() -> None:
    for name, (night, day) in COLOURS.items():
        assert token(NIGHT, name) == night, name
        assert tokens("night")[name] == night, name
        assert tokens("day")[name] == day, name
        # the day look chosen by the system (no data-theme) is the same look
        if name not in ("danger", "primary", "on-primary"):  # (not repeated: same as night)
            assert token(SYSTEM_DAY, name) == day, name
    for name, value in SAME.items():
        assert token(NIGHT, name) == value, name
        assert name not in DAY and name not in SYSTEM_DAY  # (one value for both looks)


def test_the_two_day_blocks_say_the_same() -> None:
    def parsed(text: str) -> dict[str, str]:
        return {k: v.strip() for k, v in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", text)}

    assert parsed(DAY) == parsed(SYSTEM_DAY)


def test_the_text_colours_are_readable_on_every_surface_in_both_looks() -> None:
    for look in ("night", "day"):
        t = tokens(look)
        for text in ("text", "dim", "accent", "ok", "warn", "bad"):
            for surface in ("bg", "panel", "panel2"):
                ratio = contrast(t[text], t[surface])
                assert ratio >= 4.5, (look, text, surface, round(ratio, 2))
        # the fills behind text
        assert contrast(t["on-accent"], t["accent"]) >= 4.5, look
        assert contrast(t["on-accent"], t["ok"]) >= 4.5, look  # pill.on, button.ok
        assert contrast(t["on-accent"], t["warn"]) >= 4.5, look  # pill.warn
        assert contrast(t["on-primary"], t["primary"]) >= 4.5, look
        assert contrast(t["on-danger"], t["danger"]) >= 4.5, look
    # the gold button on a day page needs its edge (3:1 against the white of a card)
    assert contrast(tokens("day")["primary-edge"], tokens("day")["panel"]) >= 3


def test_the_code_colours_are_readable_in_both_looks() -> None:
    for look in ("night", "day"):
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


def test_the_main_action_is_gold_and_keeps_its_edge() -> None:
    rule = re.search(r"\nbutton\.primary \{([^}]*)\}", CSS)
    assert rule
    body = rule.group(1)
    assert "background: var(--primary)" in body and "color: var(--on-primary)" in body
    assert "border-color: var(--primary-edge)" in body
    assert "border-radius: var(--r-pill)" in rule_of(r"button")
    assert "var(--accent)" not in body  # (violet is for links, focus and switches)


def test_the_shared_components_use_the_plan_values() -> None:
    def rule(selector: str) -> str:
        found = re.search(rf"\n{re.escape(selector)} \{{([^}}]*)\}}", CSS)
        assert found, selector
        return found.group(1)

    card = rule(".card")
    assert "border-radius: var(--r-4)" in card and "padding: 20px" in card
    assert "1px solid var(--border)" in card
    for selector in ("dialog.ask", "dialog#quick", "dialog#shortcuts"):
        body = rule(selector)
        assert "background: var(--panel2)" in body, selector
        assert "border-radius: 18px" in body and "box-shadow: var(--shadow-2)" in body, selector
    assert "outline: 2px solid var(--accent)" in rule(":focus-visible")
    assert "border-radius: var(--r-3)" in rule_of(r"[^{}]*textarea, select,\n[^{]*")
    assert "font-family: var(--font-display)" in rule("h1, h2")
    assert "font: var(--fs-md)/1.5 var(--font-body)" in rule("body")
    assert "var(--font-mono)" in rule("code, pre, samp")
    # the helpers
    num = rule(".num")
    assert "var(--font-mono)" in num and "tabular-nums" in num
    label = rule(".section-label")
    assert "text-transform: uppercase" in label and "letter-spacing: .8px" in label
    assert "var(--dim)" in label and "font-size: var(--fs-xs)" in label
    assert "var(--panel2)" in rule("kbd")
    assert "#shortcuts kbd" not in CSS  # (the shortcuts window uses the shared style)


def test_style_css_keeps_no_colour_of_its_own_outside_the_tokens() -> None:
    """Colours are tokens. The only literal colours left are the token values themselves
    (the three look blocks and the high-contrast overrides) and the scrim's fallback."""
    outside = CSS
    for opening in (":root {", ':root[data-theme="light"] {', '  :root:not([data-theme="dark"]) {'):
        start = outside.index(opening)
        end = outside.index("\n  }" if opening.startswith("  ") else "\n}", start)
        outside = outside[:start] + outside[end:]
    outside = re.sub(r"@media \(prefers-contrast: more\) \{.*?\n\}", "", outside, flags=re.S)
    outside = outside.replace("rgb(0 0 0 / .5)", "")  # the scrim's fallback (old browsers)
    assert re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", outside) == []


def test_the_star_is_a_filled_gold_icon() -> None:
    common = (STATIC / "common.js").read_text(encoding="utf-8")
    path = "M12 2 L14.2 9.8 L22 12 L14.2 14.2 L12 22 L9.8 14.2 L2 12 L9.8 9.8 Z"
    assert f'star: \'<path class="star" d="{path}"/>\'' in common
    assert re.search(r"svg\.ico \.star \{[^}]*fill: var\(--primary\)[^}]*stroke: none", CSS)


def test_every_page_has_the_new_theme_colours() -> None:
    pages = sorted(STATIC.glob("*.html"))
    assert len(pages) >= 8
    for page in pages:
        text = page.read_text(encoding="utf-8")
        if page.name == "kill.html":  # keeps its red (data-fixed)
            assert 'content="#b3231d" data-fixed' in text
            continue
        for colour, scheme in (("#ffffff", "light"), ("#12162a", "dark")):
            media = f"(prefers-color-scheme: {scheme})"
            meta = f'<meta name="theme-color" content="{colour}" media="{media}" />'
            assert meta in text, (page.name, scheme)
        assert "#171a21" not in text, page.name  # (the old dark panel)


# The heading font (bundles/fonts).
FONTS = ("space-grotesk-latin.woff2", "space-grotesk-latin-ext.woff2")


def test_the_shipped_font_files_are_the_ones_the_build_made() -> None:
    listed = dict(
        line.split()[::-1] for line in (VENDOR / "fonts.sha256").read_text("utf-8").splitlines()
    )
    assert set(listed) == set(FONTS)
    for name in FONTS:
        data = (VENDOR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == listed[name], name
        assert data[:4] == b"wOF2" and 5_000 < len(data) < 100_000, name
    licence = (VENDOR / "space-grotesk-LICENSE.txt").read_text("utf-8")
    assert "SIL Open Font License, Version 1.1" in licence and "Space Grotesk" in licence
    package = (REPO / "bundles" / "fonts" / "package.json").read_text("utf-8")
    assert '"@fontsource-variable/space-grotesk": "5.3.0"' in package
    assert (REPO / "bundles" / "fonts" / "README.md").is_file()
    assert (REPO / "bundles" / "fonts" / "build.mjs").is_file()
    assert "starlight/web/static/vendor/** -text" in (REPO / ".gitattributes").read_text("utf-8")


def test_the_font_is_declared_for_swap_with_the_packages_ranges() -> None:
    faces = re.findall(r"@font-face \{([^}]*)\}", CSS)
    assert len(faces) == 2
    for face, name in zip(faces, ("space-grotesk-latin-ext", "space-grotesk-latin"), strict=True):
        assert 'font-family: "Space Grotesk"' in face
        assert "font-display: swap" in face and "font-weight: 300 700" in face
        assert f"url(/vendor/{name}.woff2)" in face and "unicode-range: U+" in face
    assert "http" not in "".join(faces)  # (nothing from the internet)


def test_the_web_app_serves_the_font(app: Any) -> None:
    for name in FONTS:
        served = app.get(f"/vendor/{name}", headers=LOCAL)
        assert served.status_code == 200, name
        assert served.headers["content-type"] == "font/woff2", name
        assert served.content == (VENDOR / name).read_bytes(), name
    licence = app.get("/vendor/space-grotesk-LICENSE.txt", headers=LOCAL)
    assert licence.status_code == 200 and "Open Font License" in licence.text
    assert app.get("/vendor/fonts.sha256", headers=LOCAL).status_code == 404  # (not listed)


def test_the_service_worker_keeps_the_font_with_the_shell(app: Any) -> None:
    worker = app.get("/sw.js", headers=LOCAL).text
    for name in FONTS:
        assert f'"/vendor/{name}"' in worker, name
    assert "starlight-shell-v6" in worker  # (raised: the shell's file list changed)


@pytest.mark.parametrize("path", ["/style.css"])
def test_the_stylesheet_is_served_with_the_new_tokens(app: Any, path: str) -> None:
    css = app.get(path, headers=LOCAL).text
    assert "--primary: #f2c14e" in css and "--font-display" in css
