"""The launcher window and the terminal in the Night Sky look (UI 2, block V6): the gold main
button, the star, readable colours, and the terminal's colours as the web's tokens."""

from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Any

import pytest

from starlight import termstyle
from tests.conftest import REPO
from tests.test_cleanup_launcher import load_gui
from tests.test_launcher_look import contrast, make_app, needs_display

CSS = (REPO / "starlight" / "web" / "static" / "style.css").read_text(encoding="utf-8")


def tokens(look: str) -> dict[str, str]:
    """The colour tokens of the night (``:root``) or the day look of style.css."""
    start = CSS.index(":root {" if look == "dark" else ':root[data-theme="light"] {')
    block = CSS[start : CSS.index("}", start)]
    found = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-f]{6})", block))
    if look == "light":  # (the day look only overrides what differs)
        return {**tokens("dark"), **found}
    return found


def test_the_palettes_have_the_gold_of_the_plan() -> None:
    gui = load_gui()
    for palette in (gui.DARK, gui.LIGHT):
        assert palette.primary == "#f2c14e" and palette.on_primary == "#1a1404"
        assert contrast(palette.on_primary, palette.primary) >= 4.5  # the main button's text
        for colour in (palette.text, palette.dim, palette.accent, palette.bad):
            assert contrast(colour, palette.bg) >= 4.5, colour
            assert contrast(colour, palette.panel) >= 4.5, colour
    assert gui.DARK.primary_edge == gui.DARK.primary  # (Tk has no transparent: no edge shows)
    assert gui.LIGHT.primary_edge == "#b8860b"  # the day look's gold button gets its edge
    assert contrast(gui.LIGHT.primary_edge, gui.LIGHT.panel) >= 3  # (3:1 against white)
    for look, palette in (("dark", gui.DARK), ("light", gui.LIGHT)):
        assert tokens(look)["primary"] == palette.primary
        assert tokens(look)["on-primary"] == palette.on_primary


def buttons_of(widget: tk.Misc) -> list[ttk.Button]:
    found: list[ttk.Button] = [widget] if isinstance(widget, ttk.Button) else []
    for child in widget.winfo_children():
        found += buttons_of(child)
    return found


def gold_buttons(app: Any, page: str) -> list[Any]:
    style = ttk.Style(app.root)
    return [
        button
        for button in buttons_of(app.pages[page])
        + (buttons_of(app.setup_bar) if page == "setup" else [])
        if style.lookup(str(button.cget("style")), "background") == app.c.primary
    ]


@needs_display
@pytest.mark.parametrize("light", [False, True])
def test_one_gold_main_button_on_each_page(monkeypatch: pytest.MonkeyPatch, light: bool) -> None:
    gui = load_gui()
    monkeypatch.setattr(gui, "system_uses_light_theme", lambda: light)
    app = make_app(monkeypatch)
    try:
        c = app.c
        style = ttk.Style(app.root)
        assert gold_buttons(app, "running") == [app.btn_chat]
        assert style.lookup("Big.Accent.TButton", "foreground") == c.on_primary
        assert style.lookup("Accent.TButton", "bordercolor") == c.primary_edge
        assert gold_buttons(app, "setup") == [app.setup_go]
        assert gold_buttons(app, "error") == [app.err_try]
        # the kill switch turns into "Resume", the main button then; the chat button is quiet
        app.on_status(app.L.Status("ready", "", True, True, True, 5.0))
        assert gold_buttons(app, "running") == [app.btn_kill]
        assert style.lookup(str(app.btn_chat.cget("style")), "background") == c.panel2
    finally:
        app.root.destroy()


@needs_display
def test_the_brand_is_the_gold_star(monkeypatch: pytest.MonkeyPatch) -> None:
    gui = load_gui()
    app = make_app(monkeypatch)
    try:
        canvas = app.star_canvas
        assert [canvas.type(item) for item in canvas.find_all()] == ["polygon"]  # one star
        item = canvas.find_all()[0]
        assert canvas.itemcget(item, "fill") == app.c.primary
        size = int(canvas.cget("width"))
        points = [float(v) for v in canvas.coords(item)]
        assert points == pytest.approx([v * size / 24 for v in gui.STAR])
        assert gui.STAR[:4] == [12, 2, 14.2, 9.8] and len(gui.STAR) == 16  # the web's path
    finally:
        app.root.destroy()


@needs_display
def test_headings_are_larger_than_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(monkeypatch)
    try:
        assert app.f_big.actual("size") > app.f_head.actual("size") > app.f.actual("size")
        style = ttk.Style(app.root)
        assert str(style.lookup("CardTitle.TLabel", "font")) == str(app.f_title)
    finally:
        app.root.destroy()


@pytest.mark.parametrize("look", ["dark", "light"])
def test_the_terminal_colours_are_the_webs_tokens(look: str) -> None:
    web, term = tokens(look), termstyle.COLOURS[look]
    for name in ("accent", "dim", "ok", "warn", "bad"):
        assert term[name] == web[name], name
    assert term["code"] == web["text"] and term["code_bg"] == web["panel2"]
    assert term["star"] == (web["primary"] if look == "dark" else web["primary-edge"])
    # every colour can be read on the page's ground (4.5:1; the star 3:1: it is a mark)
    for name in ("accent", "dim", "ok", "warn", "bad", "code"):
        assert contrast(term[name], web["bg"]) >= 4.5, name
    assert contrast(term["star"], web["bg"]) >= 3


def test_the_terminal_has_a_colour_at_every_depth_and_none_without() -> None:
    for light in (False, True):
        for depth in ("truecolor", "256", "standard"):
            look = termstyle.palette(depth, light=light)
            assert look.star.startswith("\x1b[") and look.warn.startswith("\x1b[")
    assert termstyle.palette("truecolor").star == "\x1b[38;2;242;193;78m"  # --primary
    assert termstyle.palette("256").star == "\x1b[38;5;215m"
    assert termstyle.palette("standard").ok == "\x1b[92m"
    plain = termstyle.palette("truecolor", no_color=True)
    assert plain.star == plain.ok == plain.warn == plain.bad == plain.dim == ""
