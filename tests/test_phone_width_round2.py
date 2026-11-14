"""Block R2 of the round 2 plan: three layout faults at 360 px. The Notes card under the search
box is never cut through the middle of a row; the hints on the Automations form can be read
whole; the switches on the Settings page line up. The pages are the real ones, with the real
app behind them (see test_pages_review)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_german_everywhere import full_app as full_app  # (the fixture)
from tests.test_notes_page import notebook as notebook  # (the fixture it needs)
from tests.test_pages_review import open_page

LANGS = ("en", "de")


async def close(context: Any) -> None:
    await context.__aexit__(None, None, None)


# -- Notes: the card under the search box ----------------------------------------------------

# The rows of the card that are cut by its bottom edge (a row is a view button, a list item or
# a heading); and what the card says about "more below" (the fade) at rest and at the end.
CUT = """() => {
  const side = document.querySelector('.side-scroll') || document.querySelector('.side');
  const box = side.getBoundingClientRect();
  const rows = [...side.querySelectorAll('.quick button, .item, h3, summary')]
    .filter(e => e.checkVisibility());
  const cut = rows.filter(e => {
    const r = e.getBoundingClientRect();
    return r.top < box.bottom - 0.5 && r.bottom > box.bottom + 0.5;
  }).map(e => e.textContent.trim());
  const fade = () => {
    const s = getComputedStyle(side, '::after');
    return s.content === 'none' ? 0 : parseFloat(s.opacity);
  };
  return { cut, more: side.scrollHeight - side.clientHeight, top: side.scrollTop,
           fade: fade(), rows: rows.length };
}"""
TO_END = """async () => {
  const side = document.querySelector('.side-scroll') || document.querySelector('.side');
  side.scrollTop = side.scrollHeight;
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
  const box = side.getBoundingClientRect();
  const rows = [...side.querySelectorAll('.quick button, .item, h3, summary')]
    .filter(e => e.checkVisibility());
  const last = rows[rows.length - 1].getBoundingClientRect();
  const s = getComputedStyle(side, '::after');
  return { lastVisible: last.bottom <= box.bottom + 0.5,
           fade: s.content === 'none' ? 0 : parseFloat(s.opacity) };
}"""


@pytest.mark.parametrize("lang", LANGS)
async def test_the_notes_card_is_never_cut_through_a_row_at_360_px(
    lang: str, static_site: str, full_app: Any
) -> None:
    context, page = await open_page(
        static_site, full_app, "notes", width=360, height=760, lang=lang
    )
    try:
        state = await page.evaluate(CUT)
        assert state["cut"] == [], (lang, state)
        # there is more below, and the card says so with a soft fade at its bottom edge
        assert state["more"] > 0 and state["top"] == 0, state
        assert state["fade"] > 0.5, state
        # the rest can be reached by scrolling inside the card, and the fade is gone there
        end = await page.evaluate(TO_END)
        assert end["lastVisible"], end
        assert end["fade"] < 0.1, end
        assert await page.evaluate("() => document.documentElement.scrollWidth") <= 360
    finally:
        await close(context)


async def test_the_notes_card_is_cut_at_a_row_on_a_short_phone_too(
    static_site: str, full_app: Any
) -> None:
    context, page = await open_page(static_site, full_app, "notes", width=360, height=520)
    try:
        state = await page.evaluate(CUT)
        assert state["cut"] == [], state
        assert state["more"] > 0, state
    finally:
        await close(context)


WHOLE_MENU = """() => {
  const menu = document.querySelector('.new-menu');
  if (!menu) return null;
  return [...menu.querySelectorAll('button')].map(b => {
    const r = b.getBoundingClientRect();
    return menu.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2));
  });
}"""


async def test_the_new_note_menu_opens_whole_over_the_cards_edge_at_360_px(
    static_site: str, full_app: Any
) -> None:
    """The card is two rows high on a phone and the menu is longer: it is not cut by the card."""
    context, page = await open_page(static_site, full_app, "notes", width=360, height=760)
    try:
        await page.click("#new-note")
        await page.wait_for_selector(".new-menu button")
        reachable = await page.evaluate(WHOLE_MENU)
        assert reachable and all(reachable), reachable
        assert await page.evaluate("() => document.documentElement.scrollWidth") <= 360
    finally:
        await close(context)


# -- Automations: the hints inside the fields -----------------------------------------------

# Fill the form so that every kind of field is on the page, then measure each placeholder in its
# field's own font. A field is bad when its placeholder is wider than the room inside it (a
# textarea wraps its placeholder, so there the lines must fit its height).
PREPARE = """async () => {
  const wait = () => new Promise(r => setTimeout(r, 60));
  document.querySelector('#add-step').click();
  document.querySelector('#add-step').click();
  const kinds = document.querySelectorAll('#steps .step > select');
  kinds[1].value = 'ask'; kinds[1].dispatchEvent(new Event('change'));
  document.querySelector('input[name=trigger][value=on]').click();
  await wait();
  document.querySelector('#add-if').click();
  await wait();
}"""
WRITTEN_OUT = """async () => {
  document.querySelector('#if-mode').click();
  document.querySelector('#if-written').value = '';  // (an empty field shows its placeholder)
  await new Promise(r => setTimeout(r, 60));
}"""
PLACEHOLDERS = """() => {
  const canvas = document.createElement('canvas').getContext('2d');
  const bad = [], seen = [];
  const fields = [...document.querySelectorAll(
      '#editor input[placeholder], #editor textarea[placeholder]')]
    .filter(f => f.checkVisibility() && !f.value);
  for (const f of fields) {
    const s = getComputedStyle(f);
    canvas.font = `${s.fontStyle} ${s.fontWeight} ${s.fontSize} ${s.fontFamily}`;
    const inner = f.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
    const width = canvas.measureText(f.placeholder).width;
    seen.push(f.placeholder);
    if (f.tagName === 'TEXTAREA') {
      const lines = Math.ceil(width / inner);
      const room = f.clientHeight - parseFloat(s.paddingTop) - parseFloat(s.paddingBottom);
      if (lines * parseFloat(s.lineHeight || '18') > room + 1)
        bad.push([f.placeholder, 'lines', lines]);
    } else if (width > inner + 0.5) {
      bad.push([f.placeholder, Math.round(width), Math.round(inner)]);
    }
  }
  return { bad, seen };
}"""
# What the whole example is, per language: it must be readable on the page.
READABLE = {
    "en": (
        "usb-backup",
        "every 2 hours",
        "every month on the 1st at 10:00",
        "server is router and status is down",
        "count > 0",
    ),
    "de": (
        "usb-backup",
        "every 2 hours",
        "every month on the 1st at 10:00",
        "server ist router und status ist down",
        "count > 0",
    ),
}


@pytest.mark.parametrize("lang", LANGS)
async def test_no_placeholder_is_cut_on_the_automations_form_at_360_px(
    lang: str, static_site: str, full_app: Any
) -> None:
    context, page = await open_page(
        static_site, full_app, "automations", width=360, height=760, lang=lang
    )
    try:
        read = "() => document.querySelector('#editor').innerText"
        text = await page.evaluate(read)  # (the schedule's hint shows before "On an event")
        await page.evaluate(PREPARE)
        first = await page.evaluate(PLACEHOLDERS)
        assert first["bad"] == [], (lang, first)
        assert len(first["seen"]) >= 5, first  # (the name, a condition, two steps ...)
        await page.evaluate(WRITTEN_OUT)
        second = await page.evaluate(PLACEHOLDERS)
        assert second["bad"] == [], (lang, second)
        assert any("nas" in p or "server" in p for p in second["seen"]), second
        # the whole example is on the page as text, not only in a field
        text += await page.evaluate(read)
        for part in READABLE[lang]:
            assert part in text, (lang, part, text)
        assert await page.evaluate("() => document.documentElement.scrollWidth") <= 360
    finally:
        await close(context)


# -- Settings: the switches -------------------------------------------------------------------

# Per card: where the switches are, how wide, and how far each is from the first line of its
# label (the title of a safety row, the bold start of a sentence, the name of a module).
SWITCHES = """() => {
  const cards = new Map();
  for (const sw of document.querySelectorAll('.switch')) {
    if (!sw.checkVisibility()) continue;
    const card = sw.closest('.card');
    const row = sw.closest('.srow, .practice, details.mod > summary');
    const label = row && row.querySelector('.stitle, b, .mname');
    const r = sw.getBoundingClientRect();
    const l = label.getBoundingClientRect();
    const first = label.getClientRects()[0];
    const name = card.querySelector('h2').textContent.trim();
    if (!cards.has(name)) cards.set(name, []);
    cards.get(name).push({ right: r.right, width: r.width, top: r.top, height: r.height,
      label: first.top, left: first.left, labelRight: l.right });
  }
  return [...cards.entries()];
}"""


@pytest.mark.parametrize("lang", LANGS)
async def test_the_switches_line_up_on_the_settings_page_at_360_px(
    lang: str, static_site: str, full_app: Any
) -> None:
    context, page = await open_page(
        static_site, full_app, "settings", width=360, height=760, lang=lang
    )
    try:
        cards = await page.evaluate(SWITCHES)
        assert len(cards) >= 4, cards  # (answer ready, voice, plain words, safety, modules)
        normal = await page.evaluate(
            "() => document.querySelector('.switch').getBoundingClientRect().width"
        )
        for name, switches in cards:
            rights = {round(s["right"]) for s in switches}
            assert max(s["right"] for s in switches) - min(s["right"] for s in switches) <= 1, (
                lang,
                name,
                rights,
            )
            for s in switches:
                assert s["right"] > 320, (lang, name, s)  # on the right of the card
                assert s["width"] >= normal - 0.5, (lang, name, s, normal)
                assert s["left"] < s["right"] - s["width"], (lang, name, s)  # label on the left
                # level with the first line of the label (a few px: the switch is taller)
                assert abs(s["top"] - s["label"]) <= 6, (lang, name, s)
        assert await page.evaluate("() => document.documentElement.scrollWidth") <= 360
    finally:
        await close(context)
