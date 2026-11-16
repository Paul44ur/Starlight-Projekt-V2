"""The launcher window and the terminal in the Paper look (UI 3 blocks P1 and P6): the green
main button and flat others, no star, serif headings, light by default, readable colours, and
the terminal's colours as the web's tokens."""

from __future__ import annotations

import re
import sys
import tkinter as tk
import types
from tkinter import ttk
from typing import Any

import pytest

from starlight import termstyle
from tests.conftest import REPO
from tests.test_cleanup_launcher import load_gui
from tests.test_launcher_look import contrast, make_app, needs_display

CSS = (REPO / "starlight" / "web" / "static" / "style.css").read_text(encoding="utf-8")


def tokens(look: str) -> dict[str, str]:
    """The colour tokens of the light (``:root``) or the dark look of style.css. The main
    action (``--primary``) is the accent: style.css says ``var(--accent)``."""
    pattern = r"--([a-z0-9-]+):\s*(#[0-9a-f]{6})"
    start = CSS.index(":root {")
    found = dict(re.findall(pattern, CSS[start : CSS.index("}", start)]))
    if look == "dark":  # (the dark look only overrides what differs)
        start = CSS.index(':root[data-theme="dark"] {')
        found |= dict(re.findall(pattern, CSS[start : CSS.index("}", start)]))
    found["primary"], found["on-primary"] = found["accent"], found["on-accent"]
    return found


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
def test_one_main_button_on_each_page(monkeypatch: pytest.MonkeyPatch, light: bool) -> None:
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
        assert style.lookup(str(app.btn_chat.cget("style")), "background") == c.bg  # (flat)
    finally:
        app.root.destroy()


def canvases_of(widget: tk.Misc) -> list[tk.Canvas]:
    found: list[tk.Canvas] = [widget] if isinstance(widget, tk.Canvas) else []
    for child in widget.winfo_children():
        found += canvases_of(child)
    return found


@needs_display
@pytest.mark.parametrize("page", ["running", "setup", "error"])
def test_no_star_and_no_rounded_shapes(monkeypatch: pytest.MonkeyPatch, page: str) -> None:
    """The header is the word "Starlight" (no mark), and nothing is drawn as a polygon (so
    nothing is a smoothed, rounded shape): the status lights and model marks are circles."""
    app = make_app(monkeypatch)
    try:
        if page == "setup":
            app.open_settings()
        elif page == "error":
            app.on_error("It broke.", "")
        app.root.update_idletasks()
        header = app.root.winfo_children()[0]
        assert canvases_of(header) == []  # the word only, no drawn mark
        assert not hasattr(app, "star_canvas")
        for canvas in canvases_of(app.root):
            for item in canvas.find_all():
                assert canvas.type(item) != "polygon", canvas.coords(item)
        gui = load_gui()
        assert not hasattr(gui, "STAR") and not hasattr(gui, "rounded_rect")
    finally:
        app.root.destroy()


@needs_display
def test_headings_use_a_serif_family(monkeypatch: pytest.MonkeyPatch) -> None:
    gui = load_gui()
    app = make_app(monkeypatch)
    try:
        for font in (app.f_big, app.f_head, app.f_title):
            assert font.cget("family") in (*gui.SERIFS, "Times"), font.cget("family")
        assert app.f.cget("family") not in gui.SERIFS  # the body stays sans
    finally:
        app.root.destroy()


def test_the_serif_is_cambria_then_georgia_then_the_default() -> None:
    gui = load_gui()
    assert gui.serif_family({"Segoe UI", "Georgia", "Cambria"}) == "Cambria"
    assert gui.serif_family({"Segoe UI", "Georgia"}) == "Georgia"
    assert gui.serif_family({"Segoe UI"}) == "Times"


@needs_display
def test_only_the_main_button_is_filled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(monkeypatch)
    try:
        c, style = app.c, ttk.Style(app.root)
        assert style.lookup("Accent.TButton", "background") == c.primary
        for name in ("TButton", "Tool.TButton"):
            assert style.lookup(name, "background") == c.bg, name  # the window's own ground
            assert style.lookup(name, "bordercolor") in (c.dim, c.border, c.text), name
            assert str(style.lookup(name, "relief")) == "solid", name  # a thin line, flat
    finally:
        app.root.destroy()


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_the_window_is_light_when_the_setting_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    gui = load_gui()
    monkeypatch.setattr(gui.sys, "platform", platform)

    def broken(*args: object) -> None:
        raise OSError("no such key")

    monkeypatch.setitem(sys.modules, "winreg", types.SimpleNamespace(
        HKEY_CURRENT_USER=0, OpenKey=broken, QueryValueEx=broken))  # fmt: skip
    assert gui.system_uses_light_theme() is True


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
    assert term["star"] == web["accent"]
    # every colour can be read on the page's ground (4.5:1; the star 3:1: it is a mark)
    for name in ("accent", "dim", "ok", "warn", "bad", "code"):
        assert contrast(term[name], web["bg"]) >= 4.5, name
    assert contrast(term["star"], web["bg"]) >= 3


def test_the_terminal_has_a_colour_at_every_depth_and_none_without() -> None:
    for light in (False, True):
        for depth in ("truecolor", "256", "standard"):
            look = termstyle.palette(depth, light=light)
            assert look.star.startswith("\x1b[") and look.warn.startswith("\x1b[")
    assert termstyle.palette("truecolor").star == "\x1b[38;2;141;189;170m"  # --accent
    assert termstyle.palette("256").star == "\x1b[38;5;109m"
    assert termstyle.palette("standard").ok == "\x1b[92m"
    plain = termstyle.palette("truecolor", no_color=True)
    assert plain.star == plain.ok == plain.warn == plain.bad == plain.dim == ""
