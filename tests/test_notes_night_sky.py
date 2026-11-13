"""Block V4 of the UI 2 plan (docs/plan-ui-2.md): the Notes page in the Night Sky look. The
list is a card inset on the night ground, with 44 px rows; titles use the display font; every
view has one gold button; the editor, the graph and the boards take their colours from the
tokens (in the night and in the day look); no colour is written into notes.css or the scripts."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_page import app as app  # (the fixture)
from tests.test_notes_page import notebook as notebook  # (the fixture)
from tests.test_notes_page import notes_page, opened

STATIC = REPO / "starlight" / "web" / "static"
SCRIPTS = (
    "notes-page.js",
    "notes-edit.js",
    "notes-editor.js",
    "notes-find.js",
    "notes-info.js",
    "notes-tasks.js",
    "notes-views.js",
    "board.js",
)
SHOWCASE = (
    "# Showcase\n\n> [!todo] Do this week\n> Call the dentist\n\n> [!note] A plain note\n"
    "> Some text\n\nA `code` word and [[Plan]].\n"
)
BOARD = {
    "nodes": [
        {"id": "a1", "type": "text", "x": 0, "y": 0, "width": 240, "height": 100,
         "text": "Ideas", "color": "1"},
        {"id": "b2", "type": "text", "x": 400, "y": 0, "width": 240, "height": 100,
         "text": "Own", "color": "#aa5500"},
    ],
    "edges": [{"id": "e1", "fromNode": "a1", "toNode": "b2", "color": "4"}],
}  # fmt: skip
# What a page script reads to turn a token into the colour the browser computes.
TOKEN = """(name) => {
  const probe = document.createElement('i');
  probe.style.color = `var(${name})`;
  document.body.appendChild(probe);
  const colour = getComputedStyle(probe).color;
  probe.remove();
  return colour;
}"""
NAMED = (
    "white|black|red|green|blue|yellow|orange|purple|pink|gray|grey|silver|gold|navy|teal|"
    "maroon|lime|aqua|cyan|magenta|brown|violet|indigo|crimson|tomato|coral|salmon"
)


@pytest.fixture
def showcase(notebook: Path) -> Path:
    (notebook / "Notes" / "Showcase.md").write_text(SHOWCASE, encoding="utf-8")
    (notebook / "Boards").mkdir(exist_ok=True)
    (notebook / "Boards" / "Plan.canvas").write_text(json.dumps(BOARD), encoding="utf-8")
    return notebook


def rgb(colour: str) -> str:
    """``rgba(1, 2, 3, 1)`` is ``rgb(1, 2, 3)``: the browser says either."""
    return re.sub(r"^rgba\((\d+, \d+, \d+), 1\)$", r"rgb(\1)", str(colour))


async def token(page: Any, name: str) -> str:
    return rgb(await page.evaluate(TOKEN, name))


async def open_note(page: Any, site: str) -> None:
    await opened(page, f"{site}/notes.html?path=Notes%2FShowcase.md")
    await page.wait_for_selector("h1.title")


# -- no colour written into the page ------------------------------------------------------------


def test_notes_css_and_scripts_hold_no_colour_of_their_own() -> None:
    css = (STATIC / "notes.css").read_text(encoding="utf-8")
    found = re.findall(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", css)
    assert found == [], found
    named = re.findall(
        rf"(?:color|background|fill|stroke|border[a-z-]*|outline|shadow)\s*:[^;{{}}]*\b({NAMED})\b",
        css,
        re.I,
    )
    assert named == [], named
    for name in SCRIPTS:
        script = (STATIC / name).read_text(encoding="utf-8")
        script = re.sub(r"//.*", "", script)  # (comments may talk about colours)
        assert re.findall(r"""["']#[0-9a-fA-F]{3,8}["']|\brgba?\(""", script) == [], name


def test_notes_css_has_no_key_style_of_its_own() -> None:
    css = (STATIC / "notes.css").read_text(encoding="utf-8")
    assert not re.search(r"(^|\n)\s*kbd\s*\{", css)  # (the shared one in style.css applies)


# -- the list, the rows and the titles ---------------------------------------------------------


@pytest.mark.parametrize("scheme", ["dark", "light"])
async def test_the_list_is_a_card_with_44_pixel_rows(
    static_site: str, app: Any, showcase: Path, monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app, scheme=scheme) as page:
        await open_note(page, static_site)
        card = await page.evaluate(
            "() => { const s = getComputedStyle(document.querySelector('.notes > .side'));"
            " return [s.backgroundColor, s.borderTopLeftRadius, s.borderTopWidth]; }"
        )
        assert card == [await token(page, "--panel"), "16px", "1px"]
        inset = await page.evaluate(
            "() => document.querySelector('.notes > .side').getBoundingClientRect().left"
        )
        assert inset == 12  # (inset on the night ground, not against the window's edge)
        for selector in (".quick button[data-view=today]", "#folders .item", "#folders summary"):
            height = await page.locator(selector).first.evaluate(
                "e => [e.getBoundingClientRect().height, getComputedStyle(e).borderTopLeftRadius]"
            )
            assert height[0] >= 44 and height[1] == "12px", (selector, height)
        # the open note: --panel2 behind it and a small --accent dot
        here = page.locator('#folders .item[aria-current="page"]')
        assert rgb(await here.evaluate("e => getComputedStyle(e).backgroundColor")) == await token(
            page, "--panel2"
        )
        dot = await here.evaluate(
            "e => { const d = getComputedStyle(e, '::before');"
            " return [d.backgroundColor, d.width, d.borderTopLeftRadius]; }"
        )
        assert dot == [await token(page, "--accent"), "6px", "50%"]
        # the section labels are the shared ones; the brand row has the gold star
        label = page.locator(".side h3.section-label").first
        assert await label.evaluate("e => getComputedStyle(e).textTransform") == "uppercase"
        star = page.locator(".brand svg.ico .star")
        assert await star.evaluate("e => getComputedStyle(e).fill") == await token(
            page, "--primary"
        )


@pytest.mark.parametrize("scheme", ["dark", "light"])
async def test_titles_use_the_display_font_and_counts_are_mono(
    static_site: str, app: Any, showcase: Path, monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    family = "e => getComputedStyle(e).fontFamily"
    async with notes_page(static_site, app, scheme=scheme) as page:
        await open_note(page, static_site)
        assert "Space Grotesk" in await page.locator("h1.title").evaluate(family)
        assert "Space Grotesk" in await page.locator(".brand-name").evaluate(family)
        assert "Space Grotesk" in await page.locator(".note-body .md-h").first.evaluate(family)
        assert "monospace" in await page.locator("#folders summary .count").first.evaluate(family)
        await page.locator(".quick button[data-view=boards]").click()
        await page.wait_for_selector("h1.title")
        assert "Space Grotesk" in await page.locator("h1.title").evaluate(family)
        await page.locator(".quick button[data-view=tasks]").click()
        await page.wait_for_selector(".task-group h3 .count")
        assert "monospace" in await page.locator(".task-group h3 .count").first.evaluate(family)
        assert "Space Grotesk" in await page.locator(".task-group h3").first.evaluate(family)


# -- one gold button per view ------------------------------------------------------------------


async def test_every_view_has_exactly_one_gold_button(
    static_site: str, app: Any, showcase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    gold = (
        "() => [...document.querySelectorAll('.notes button.primary')]"
        ".map(b => b.id || b.textContent.trim())"
    )
    async with notes_page(static_site, app, scheme="dark") as page:
        await open_note(page, static_site)
        assert await page.evaluate(gold) == ["new-note"]  # (the main action of a note's view)
        for view in ("inbox", "tasks", "graph", "trash", "cards"):
            await page.locator(f".quick button[data-view={view}]").click()
            await page.wait_for_selector("h1.title")
            await page.wait_for_timeout(250)
            assert await page.evaluate(gold) == ["new-note"], view
        # a view with a main action of its own: that one is gold, the list's button is plain
        await page.locator(".quick button[data-view=boards]").click()
        await page.wait_for_selector("button.primary:has-text('New board')")
        assert await page.evaluate(gold) == ["\uff0b New board"]
        assert await page.locator("#new-note").evaluate(
            "e => getComputedStyle(e).backgroundColor"
        ) != (await token(page, "--primary"))
        await page.locator(".quick button[data-view=today]").click()
        await page.wait_for_selector('button.primary:has-text("Write today\'s page")')
        assert await page.evaluate(gold) == ["Write today's page"]
        assert await page.locator(".notes button.primary").evaluate(
            "e => getComputedStyle(e).backgroundColor"
        ) == await token(page, "--primary")


# -- the editor, the callouts, the graph and the boards follow the tokens -----------------------


async def editor_look(static_site: str, app: Any, scheme: str) -> dict[str, Any]:
    async with notes_page(static_site, app, scheme=scheme) as page:
        await open_note(page, static_site)
        await page.locator("#edit-toggle").click()
        await page.wait_for_selector(".cm-editor .cm-line")
        await page.keyboard.press("Control+f")
        await page.wait_for_selector(".cm-search")
        look = await page.evaluate(
            """() => {
              const css = (sel, prop) => getComputedStyle(document.querySelector(sel))[prop];
              return {
                text: css('.cm-content', 'color'), caret: css('.cm-cursor', 'borderLeftColor'),
                ground: css('body', 'backgroundColor'),
                editor: css('.cm-editor', 'backgroundColor'),
                field: css('.cm-search .cm-textfield', 'backgroundColor'),
                button: css('.cm-search .cm-button', 'backgroundImage'),
                buttonBg: css('.cm-search .cm-button', 'backgroundColor'),
                panel: css('.cm-panels', 'backgroundColor'),
              };
            }"""
        )
        look["tokens"] = {
            name: await token(page, f"--{name}")
            for name in ("text", "accent", "bg", "panel", "panel2")
        }
        return {k: rgb(v) if isinstance(v, str) else v for k, v in look.items()}


async def test_the_editor_follows_the_tokens_in_both_looks(
    static_site: str, app: Any, showcase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    seen = {}
    for scheme in ("dark", "light"):
        look = await editor_look(static_site, app, scheme)
        tokens = look["tokens"]
        assert look["text"] == tokens["text"], scheme
        assert look["caret"] == tokens["accent"], scheme
        assert look["ground"] == tokens["bg"], scheme
        assert look["editor"] == "rgba(0, 0, 0, 0)", scheme  # (the page's ground shows through)
        assert look["panel"] == tokens["panel"], scheme
        assert look["field"] == tokens["panel2"], scheme  # (the search panel is not light grey)
        assert look["buttonBg"] == tokens["panel2"] and look["button"] == "none", scheme
        seen[scheme] = look
    assert seen["dark"]["text"] != seen["light"]["text"]
    assert seen["dark"]["field"] != seen["light"]["field"]


async def test_the_todo_callout_has_a_look_of_its_own(
    static_site: str, app: Any, showcase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app, scheme="dark") as page:
        await open_note(page, static_site)
        find = (
            "k => { const e = document.querySelector('.callout-' + k);"
            " const s = getComputedStyle(e);"
            " return [s.borderLeftColor, s.borderLeftStyle,"
            " getComputedStyle(e.querySelector('.callout-title')).color]; }"
        )
        todo = await page.evaluate(find, "todo")
        plain = await page.evaluate(find, "note")
        assert todo[0] == await token(page, "--warn")  # (the line and the title)
        assert todo[2] == await token(page, "--warn")
        assert plain[0] == await token(page, "--accent")
        assert todo != plain and todo[1] != plain[1]  # (dashed, not solid)


@pytest.mark.parametrize("scheme", ["dark", "light"])
async def test_the_graph_and_boards_take_their_colours_from_tokens(
    static_site: str, app: Any, showcase: Path, monkeypatch: pytest.MonkeyPatch, scheme: str
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app, scheme=scheme) as page:
        await opened(page, f"{static_site}/notes.html?view=graph")
        await page.wait_for_selector(".whole-graph .node circle")
        fills = await page.locator(".whole-graph .node circle").evaluate_all(
            "cs => cs.map(c => getComputedStyle(c).fill)"
        )
        palette = [
            await token(page, f"--{n}")
            for n in ("accent", "ok", "warn", "hl-f", "bad", "hl-v", "hl-k", "dim")
        ]
        assert set(fills) <= set(palette) and len(set(fills)) >= 2
        swatches = await page.locator(".graph-key .swatch").evaluate_all(
            "ss => ss.map(s => getComputedStyle(s).backgroundColor)"
        )
        assert set(swatches) <= set(palette)
        edge = await page.locator(".whole-graph .edge").first.evaluate(
            "e => getComputedStyle(e).stroke"
        )
        assert edge == await token(page, "--border")
        # a board: the preset colours are tokens; a colour written in the file stays as it is
        await opened(page, f"{static_site}/notes.html?board=Boards%2FPlan.canvas")
        await page.wait_for_selector(".board-card")
        first = page.locator('.board-card[data-id="a1"]')
        assert await first.evaluate("e => e.style.getPropertyValue('--card')") == "var(--bad)"
        assert await first.evaluate("e => getComputedStyle(e).borderTopColor") == await token(
            page, "--bad"
        )
        own = page.locator('.board-card[data-id="b2"]')
        assert await own.evaluate("e => e.style.getPropertyValue('--card')") == "#aa5500"
        arrow = page.locator(".board-line").first
        assert await arrow.evaluate("e => getComputedStyle(e).stroke") == await token(page, "--ok")


async def test_the_big_graph_is_redrawn_when_the_look_changes(
    static_site: str, app: Any, showcase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Beyond 300 notes the graph is a canvas, which cannot use var(): it reads the tokens."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    root = showcase / "Many"
    root.mkdir()
    for n in range(310):
        (root / f"n{n}.md").write_text(f"note {n}\n", encoding="utf-8")
    async with notes_page(static_site, app, scheme="dark") as page:
        await opened(page, f"{static_site}/notes.html?view=graph")
        await page.wait_for_selector("canvas.whole-graph")
        sample = (
            "() => [...document.querySelector('canvas.whole-graph').getContext('2d')"
            ".getImageData(0, 0, 400, 400).data].join(',')"
        )
        before = await page.evaluate(sample)
        await page.evaluate("() => { document.documentElement.dataset.theme = 'light'; }")
        await page.wait_for_function(f"() => ({sample})() !== {json.dumps(before)}")
        assert page.errors == []  # type: ignore[attr-defined]


# -- a phone ---------------------------------------------------------------------------------


async def test_nothing_scrolls_sideways_at_360_pixels(
    static_site: str, app: Any, showcase: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app, scheme="light", width=360) as page:
        await open_note(page, static_site)
        wide = "() => document.documentElement.scrollWidth"
        assert await page.evaluate(wide) <= 360
        for view in ("tasks", "graph", "boards", "cards", "trash"):
            await page.locator(f".quick button[data-view={view}]").click()
            await page.wait_for_selector("h1.title")
            await page.wait_for_timeout(250)
            assert await page.evaluate(wide) <= 360, view
        await opened(page, f"{static_site}/notes.html?board=Boards%2FPlan.canvas")
        await page.wait_for_selector(".board-card")
        assert await page.evaluate(wide) <= 360
