"""Block V7 of the UI 2 plan: the same checks on EVERY page, in both looks: the header pattern,
one gold button, nothing sideways at 360 px, touch targets of 44 px, a focus ring on Tab, every
control still visible in high contrast and forced colours, and no animation with reduced
motion. The pages are the real ones, with the real app behind them (see test_german_everywhere)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_chat_page import SEND
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_german_everywhere import APP, DEVICES, german_page, settle
from tests.test_german_everywhere import full_app as full_app  # (the fixture)
from tests.test_notes_page import notebook as notebook  # (the fixture it needs)

PAGES = ("chat", "notes", "settings", "memory", "activity", "automations", "kill", "pair")
LOOKS = ("dark", "light")
READY = {
    "chat": ".conv",
    "notes": "h1.title",
    "settings": "#safety .srow",
    "memory": "#memories > *:not(.skeleton)",
    "activity": "#entries > *",
    "automations": ".rule",
    "kill": "button",
    "pair": "#go",
}


async def open_page(
    site: str, app_: Any, name: str, **options: Any
) -> Any:  # an async context manager
    url = f"{site}/{name}.html" + ("?path=Notes%2FShopping.md" if name == "notes" else "")
    context = german_page(
        site,
        app_,
        name,
        lang=options.pop("lang", "en"),
        init=APP if name == "chat" else "",
        overrides={"/api/devices": DEVICES} if name == "settings" else {},
        **options,
    )
    page = await context.__aenter__()
    await page.goto(url)
    await page.wait_for_selector(READY[name])
    await settle(page)
    return context, page


# What a touch screen may leave smaller than 44 px, each with its reason.
SMALL_TARGETS = (
    ".skip",  # a link that shows only when it has the focus
    "kbd",
    ".cm-editor *",  # the editor has its own controls
    ".task-box",  # the box of a to-do in a note: the whole row is the target
    "a.note-link, a.tag, .note-body a, .msg a, .hello a, p a, .hint a, .small a",  # in a text
    ".mini-graph *",  # a picture
    "input[type=checkbox], input[type=radio], input[type=file]",  # their label is the target
    ".switch input",
    "td button, td a",  # a row of a table
    ".folder-toggle, #folders summary",
)

TARGETS = """(skip) => {
  const bad = [];
  const small = document.querySelectorAll(skip);
  const ok = el => [...small].some(s => s === el || s.contains(el));
  for (const el of document.querySelectorAll('button, a[href], select, input, summary, '
      + '[role=button], [role=tab], [role=menuitem]')) {
    if (!el.checkVisibility() || ok(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    // the label around a switch, or a row that holds the control, counts as the target
    const area = el.closest('label, li, tr, .conv, .row, summary') || el;
    const box = area.getBoundingClientRect();
    if (Math.max(r.height, box.height) < 43.5)
      bad.push((el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + el.className) + ' '
        + Math.round(r.height) + 'px');
  }
  return bad;
}"""


# the top bar's links must stay inside the screen (the page may hide what sticks out)
BAR = """() => [...document.querySelectorAll('header.top > *, header.top nav a')]
  .filter(e => e.checkVisibility()).filter(e => e.getBoundingClientRect().right > innerWidth + 0.5)
  .map(e => e.id || e.textContent.trim())"""


@pytest.mark.parametrize("lang", ["en", "de"])
@pytest.mark.parametrize("name", [p for p in PAGES if p not in ("chat", "kill", "pair")])
async def test_the_top_bar_stays_on_a_360_px_screen(
    name: str, lang: str, static_site: str, full_app: Any
) -> None:
    context, page = await open_page(static_site, full_app, name, width=360, height=760, lang=lang)
    try:
        assert await page.evaluate(BAR) == [], (name, lang)
    finally:
        await context.__aexit__(None, None, None)


@pytest.mark.parametrize("scheme", LOOKS)
@pytest.mark.parametrize("name", PAGES)
async def test_nothing_scrolls_sideways_at_360_px(
    name: str, scheme: str, static_site: str, full_app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("playwright.async_api")
    context, page = await open_page(
        static_site, full_app, name, width=360, height=760, scheme=scheme
    )
    try:
        width = await page.evaluate("() => document.documentElement.scrollWidth")
        assert width <= 360, (name, scheme, width)
    finally:
        await context.__aexit__(None, None, None)


@pytest.mark.parametrize("name", PAGES)
async def test_touch_targets_are_44_px_on_a_touch_screen(
    name: str, static_site: str, full_app: Any
) -> None:
    pytest.importorskip("playwright.async_api")
    context, page = await open_page(static_site, full_app, name, width=390, height=800, touch=True)
    try:
        assert await page.evaluate("() => matchMedia('(pointer: coarse)').matches")
        assert await page.evaluate(TARGETS, ", ".join(SMALL_TARGETS)) == [], name
    finally:
        await context.__aexit__(None, None, None)


FOCUS = """() => {
  const el = document.activeElement;
  const s = getComputedStyle(el);
  return [el.tagName, s.outlineStyle, parseFloat(s.outlineWidth), s.outlineColor,
          s.boxShadow];
}"""


@pytest.mark.parametrize("scheme", LOOKS)
@pytest.mark.parametrize("name", PAGES)
async def test_tab_shows_a_focus_ring_in_the_accent_colour(
    name: str, scheme: str, static_site: str, full_app: Any
) -> None:
    pytest.importorskip("playwright.async_api")
    context, page = await open_page(static_site, full_app, name, scheme=scheme)
    try:
        await page.evaluate("() => document.activeElement && document.activeElement.blur()")
        await page.keyboard.press("Tab")  # (the kill page has two controls: stop at the first)
        tag, style, width, colour, shadow = await page.evaluate(FOCUS)
        ring = (style != "none" and width >= 2) or "rgb" in shadow
        assert ring, (name, scheme, tag, style, width, colour, shadow)
        # the ring is the accent colour of the look (not a browser default)
        accent = await page.evaluate(
            "() => { const p = document.createElement('i'); p.style.color = 'var(--accent)';"
            " document.body.appendChild(p); const c = getComputedStyle(p).color; p.remove();"
            " return c; }"
        )
        assert colour == accent or accent in shadow, (name, scheme, colour, accent, shadow)
    finally:
        await context.__aexit__(None, None, None)


CONTROLS = """() => {
  const bad = [];
  for (const el of document.querySelectorAll('button, select, input:not([type=checkbox])'
      + ':not([type=radio]):not([type=file]):not([type=hidden]), textarea')) {
    if (!el.checkVisibility()) continue;
    const s = getComputedStyle(el);
    const edge = ['Top', 'Right', 'Bottom', 'Left'].some(
      side => s['border' + side + 'Style'] !== 'none'
        && parseFloat(s['border' + side + 'Width']) > 0);
    // the message box is a pill: its edge is the form's, the box itself has none
    if (el.id === 'input') continue;
    // an icon button or a quiet one may have no edge, but then it must show something
    const shows = el.querySelector('svg, img') || el.textContent.trim() || el.value;
    if (!edge && !shows)
      bad.push((el.id ? '#' + el.id : el.tagName.toLowerCase() + '.' + el.className));
  }
  return bad;
}"""


@pytest.mark.parametrize("mode", ["forced-colors", "prefers-contrast"])
@pytest.mark.parametrize("name", PAGES)
async def test_every_control_stays_visible_in_high_contrast(
    name: str, mode: str, static_site: str, full_app: Any
) -> None:
    pytest.importorskip("playwright.async_api")
    options = {"forced_colors": "active"} if mode == "forced-colors" else {"contrast": "more"}
    context, page = await open_page(static_site, full_app, name, **options)
    try:
        assert await page.evaluate(CONTROLS) == [], (name, mode)
        if mode == "forced-colors":
            # the plain buttons keep a line of their own (the system draws the colour)
            lines = await page.evaluate(
                "() => [...document.querySelectorAll('button:not(.icon):not(.quiet)')]"
                ".filter(b => b.checkVisibility()).filter(b => getComputedStyle(b)"
                ".borderTopStyle === 'none'"
                " && !b.closest('.conv, .quick, nav, summary, li, .info, .side'))"
                ".map(b => b.id || b.className)"
            )
            assert lines == [], (name, lines)
    finally:
        await context.__aexit__(None, None, None)


MOTION = """() => {
  const moving = [];
  for (const el of document.querySelectorAll('*')) {
    for (const where of [null, '::before', '::after']) {
      const s = getComputedStyle(el, where);
      const seconds = v => Math.max(...v.split(',').map(x => parseFloat(x) || 0));
      if (s.animationName !== 'none' && seconds(s.animationDuration) > 0.011)
        moving.push((el.id || el.className || el.tagName) + (where || '') + ' ' + s.animationName);
    }
  }
  return moving;
}"""


@pytest.mark.parametrize("name", PAGES)
async def test_reduced_motion_stops_every_animation(
    name: str, static_site: str, full_app: Any
) -> None:
    pytest.importorskip("playwright.async_api")
    context, page = await open_page(static_site, full_app, name, reduced_motion="reduce")
    try:
        if name == "chat":  # an answer on its way is the page's busiest moment
            await page.evaluate(SEND, {"type": "start", "conversation": "c1"})
        assert await page.evaluate(MOTION) == [], name
    finally:
        await context.__aexit__(None, None, None)


HEADER = """() => {
  const h = document.querySelector('header.top h1, .brand-name, .side-brand .brand-name');
  const s = h ? getComputedStyle(h) : null;
  return s ? s.fontFamily : '';
}"""


@pytest.mark.parametrize("name", [p for p in PAGES if p not in ("kill", "pair")])
async def test_every_page_has_its_title_in_the_display_font(
    name: str, static_site: str, full_app: Any
) -> None:
    pytest.importorskip("playwright.async_api")
    context, page = await open_page(static_site, full_app, name)
    try:
        assert "Space Grotesk" in await page.evaluate(HEADER), name
    finally:
        await context.__aexit__(None, None, None)


GOLD = """() => [...document.querySelectorAll('button.primary, a.primary')]
  .filter(b => b.checkVisibility()).map(b => b.id || b.textContent.trim())"""


@pytest.mark.parametrize("name", PAGES)
async def test_a_view_has_one_gold_button_at_most(
    name: str, static_site: str, full_app: Any
) -> None:
    pytest.importorskip("playwright.async_api")
    context, page = await open_page(static_site, full_app, name)
    try:
        # (Settings is a column of cards: a card has one, see test_settings_night_sky)
        found = await page.evaluate(GOLD)
        assert len(found) <= (3 if name == "settings" else 2 if name == "chat" else 1), found
    finally:
        await context.__aexit__(None, None, None)


# -- the leftovers the review fixed ----------------------------------------------------------


async def test_notes_has_one_star_and_no_empty_info_headings(
    static_site: str, full_app: Any
) -> None:
    context, page = await open_page(static_site, full_app, "notes")
    try:
        assert await page.locator("header.top img.logo").count() == 0  # (the list card has it)
        assert await page.locator("header.top h1").inner_text() == "Notes"
        for view in ("graph", "boards", "tasks"):
            await page.locator(f'.quick [data-view="{view}"]').click()
            await settle(page)
            empty = await page.evaluate(
                "() => [...document.querySelectorAll('.info h3.section-label')]"
                ".filter(h => h.nextElementSibling && h.nextElementSibling.matches(':empty'))"
                ".filter(h => h.checkVisibility()).map(h => h.textContent)"
            )
            assert empty == [], (view, empty)
    finally:
        await context.__aexit__(None, None, None)


async def test_settings_shows_the_level_text_once(static_site: str, full_app: Any) -> None:
    context, page = await open_page(static_site, full_app, "settings")
    try:
        assert await page.locator("#level-help").count() == 0  # (the cards show it)
        assert await page.locator("#levels button").count() == 3
    finally:
        await context.__aexit__(None, None, None)


@pytest.mark.parametrize("scheme", LOOKS)
async def test_an_expired_memory_keeps_a_readable_contrast(
    scheme: str, static_site: str, full_app: Any
) -> None:
    context, page = await open_page(static_site, full_app, "memory", scheme=scheme)
    try:
        ratio = await page.evaluate(
            """() => {
              const rgb = c => c.match(/[\\d.]+/g).slice(0, 3).map(Number);
              const lum = c => { const [r, g, b] = rgb(c).map(v => { v /= 255;
                return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
                return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
              const row = document.querySelector('.mem.expired');
              const a = lum(getComputedStyle(row.querySelector('.text')).color);
              const b = lum(getComputedStyle(row.closest('.card')).backgroundColor);
              const ratio = (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
              return [ratio, getComputedStyle(row).opacity];
            }"""
        )
        assert ratio[0] >= 4.5 and ratio[1] == "1", (scheme, ratio)  # (colour, not opacity)
    finally:
        await context.__aexit__(None, None, None)


async def test_the_next_time_of_an_automation_stays_in_one_piece(
    static_site: str, full_app: Any
) -> None:
    context, page = await open_page(static_site, full_app, "automations", width=360, height=760)
    try:
        # (the real app has no rules: the page is given one by the table of test_german_everywhere)
        assert (
            await page.locator(".rule .num").first.evaluate("n => getComputedStyle(n).whiteSpace")
            == "nowrap"
        )
    finally:
        await context.__aexit__(None, None, None)


async def test_the_help_command_lists_the_commands_in_english_too(
    static_site: str, full_app: Any
) -> None:
    context, page = await open_page(static_site, full_app, "chat")
    try:
        await page.locator("#input").fill("/help")
        await page.keyboard.press("Enter")
        await page.wait_for_selector(".msg.sys")
        text = await page.locator(".msg.sys").last.inner_text()
        assert text.startswith("Commands: /new (new conversation)"), text
    finally:
        await context.__aexit__(None, None, None)
