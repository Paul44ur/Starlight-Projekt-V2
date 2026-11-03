"""The launcher window's look (UI block U2): its status page in German, the order of its
buttons, a focus ring for keyboard use only, and one set of colours with the web pages that
stays readable (WCAG AA: 4.5:1 for text)."""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_cleanup_launcher import load_gui
from tests.test_launcher import L
from tests.test_launcher_window import has_display

STATIC = REPO / "starlight" / "web" / "static"
needs_display = pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")


def contrast(a: str, b: str) -> float:
    def lum(colour: str) -> float:
        parts = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [p / 12.92 if p <= 0.04045 else ((p + 0.055) / 1.055) ** 2.4 for p in parts]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]

    high, low = sorted((lum(a), lum(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def make_app(monkeypatch: pytest.MonkeyPatch, language: str = "en") -> Any:
    gui = load_gui()
    monkeypatch.setattr(gui, "LANGUAGE", language)
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    app = gui.App(L, L.build_parser().parse_args(["--gui"]))
    app.opened_browser = True  # (a ready status would open the chat in a browser)
    app.show("running")
    return app


def test_mix_moves_a_colour_towards_another() -> None:
    gui = load_gui()
    assert gui.mix("#000000", "#ffffff", 0.5) == "#808080"
    assert gui.mix("#123456", "#abcdef", 0) == "#123456"
    assert gui.mix("#123456", "#abcdef", 1) == "#abcdef"


def test_the_window_and_the_pages_share_readable_colours() -> None:
    gui = load_gui()
    for palette in (gui.DARK, gui.LIGHT):
        assert contrast("#ffffff", palette.danger) >= 4.5  # the stop button's text
        assert contrast(palette.on_accent, palette.accent) >= 4.5  # "Open chat"
        assert contrast(palette.accent, palette.bg) >= 4.5  # links
        assert contrast(palette.text, palette.panel) >= 4.5
        assert contrast(palette.dim, palette.panel) >= 4.5
        assert contrast(palette.bad, palette.panel) >= 4.5  # red text (the kill switch)
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    dark = css[css.index(":root {") : css.index("}", css.index(":root {"))]
    light = css[css.index(':root[data-theme="light"]') :]
    light = light[: light.index("}")]

    def token(block: str, name: str) -> str:
        found = re.search(rf"--{name}:\s*(#[0-9a-f]{{6}})", block)
        assert found, name
        return found.group(1)

    assert token(dark, "accent") == gui.DARK.accent
    assert token(dark, "on-accent") == gui.DARK.on_accent
    assert token(dark, "danger") == gui.DARK.danger == gui.LIGHT.danger
    assert token(light, "accent") == gui.LIGHT.accent
    assert token(light, "on-accent") == gui.LIGHT.on_accent
    assert token(light, "bad") == gui.LIGHT.bad
    assert token(dark, "bad") == gui.DARK.bad
    # the light look chosen by the system (no data-theme) gets the same accent
    system = css[css.index("@media (prefers-color-scheme: light)") :]
    assert token(system[: system.index("}")], "accent") == gui.LIGHT.accent


def test_no_page_puts_white_text_on_a_light_fill() -> None:
    """White text on the accent (light blue in the dark look) or on --bad (a light red) is too
    faint to read; those fills take --on-accent, or --danger behind white."""
    rule = re.compile(r"\{([^{}]*)\}")
    bad = []
    for path in sorted(STATIC.glob("*.html")) + sorted(STATIC.glob("*.css")):
        for body in rule.findall(path.read_text(encoding="utf-8")):
            light_fill = re.search(r"background:\s*var\(--(accent|bad)\)", body)
            white = re.search(r"(?<!-)color:\s*#fff(fff)?\b", body)
            if light_fill and white:
                bad.append(f"{path.name}: {body.strip()}")
    assert bad == []


@needs_display
def test_the_status_page_speaks_german(monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(monkeypatch, "de")
    try:
        rows = {name: label.cget("text") for name, (_dot, _oval, label) in app.status_rows.items()}
        names = [w.cget("text") for w in app.status_rows["Model"][0].master.grid_slaves(column=0)]
        assert sorted(names) == ["Chat", "Modell", "Not-Aus", "Statistik"]

        app.on_status(L.Status("ready", "Qwen3 8B", True, True, False, 50.0))
        rows = {name: label.cget("text") for name, (_dot, _oval, label) in app.status_rows.items()}
        assert app.r_title.cget("text") == "Starlight läuft"
        assert app.r_sub.cget("text").startswith("Dein Chat ist unter http")
        assert rows["Model"] == "Bereit: Qwen3 8B"
        assert rows["Kill switch"] == "Aus: Starlight kann handeln"
        assert app.btn_kill.cget("text") == "Alles anhalten"
        assert app.root.title() == "Starlight · bereit"
        assert app.r_footer.cget("text").startswith("Ordner: ")

        app.on_status(L.Status("ready", "Qwen3 8B", True, True, True, 60.0))
        assert app.r_title.cget("text") == "Starlight ist angehalten"
        assert app.btn_kill.cget("text") == "Fortsetzen"
        assert app.root.title() == "Starlight · Not-Aus aktiv"

        app.on_status(L.Status("loading", "", False, None, None, 5.0))
        rows = {name: label.cget("text") for name, (_dot, _oval, label) in app.status_rows.items()}
        assert app.r_title.cget("text") == "Starlight startet ..."
        assert rows["Model"].startswith("Lädt in den Grafikspeicher ...")
        assert rows["Chat"] == "Startet ..."
        assert rows["Statistics"] == "Aus"
        assert rows["Kill switch"] == "Bereit, sobald der Chat läuft"

        app.on_status(L.Status("down", "", False, True, None, 5.0))
        rows = {name: label.cget("text") for name, (_dot, _oval, label) in app.status_rows.items()}
        assert app.r_title.cget("text") == "Etwas braucht deine Aufmerksamkeit"
        assert rows["Model"] == "Angehalten: siehe die Logs"

        app.on_status(L.Status("paused", "a game runs", True, True, False, 5.0))
        assert app.r_title.cget("text") == "Starlight pausiert für ein Spiel"
        assert app.btn_restart.cget("text") == "Jetzt fortsetzen"
    finally:
        app.root.destroy()


@needs_display
def test_the_main_buttons_stand_out_and_the_tools_are_quieter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app(monkeypatch)
    try:
        main = app.btn_chat.master
        assert app.btn_stats.master is main and app.btn_kill.master is main
        assert app.btn_chat.cget("style") == "Big.Accent.TButton"
        assert app.btn_kill.cget("style") == "Big.Danger.TButton"
        tools = (app.btn_restart, app.btn_check, app.btn_logs, app.btn_speed, app.btn_exam,
                 app.btn_disk)  # fmt: skip
        assert all(b.master is not main and b.cget("style") == "Tool.TButton" for b in tools)
        # the kill switch keeps its size when it turns into "Resume" and back
        app.on_status(L.Status("ready", "", True, True, True, 5.0))
        assert app.btn_kill.cget("style") == "Big.TButton"
        app.on_status(L.Status("ready", "", True, True, False, 5.0))
        assert app.btn_kill.cget("style") == "Big.Danger.TButton"
    finally:
        app.root.destroy()


@needs_display
def test_the_focus_ring_shows_only_while_the_keyboard_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tkinter import ttk

    app = make_app(monkeypatch)
    try:
        style = ttk.Style(app.root)

        def ring() -> int:
            return int(str(style.lookup("TButton", "focusthickness")))

        app.root.deiconify()
        app.root.focus_force()
        app.btn_stats.focus_set()  # (key events go to the widget with the focus)
        app.root.update()
        assert ring() == 0
        app.btn_stats.event_generate("<Tab>")
        app.root.update()
        assert ring() > 0
        assert app.root.focus_get() is app.btn_kill  # and Tab still moves on
        app.btn_stats.event_generate("<ButtonPress-1>", x=4, y=4)
        app.btn_stats.event_generate("<ButtonRelease-1>", x=-50, y=-50)  # (released outside)
        app.root.update()
        assert ring() == 0
    finally:
        app.root.destroy()
