"""The launcher window's look (UI block U2): its status page in German, the order of its
buttons, a focus ring for keyboard use only, and one set of colours with the web pages that
stays readable (WCAG AA: 4.5:1 for text)."""

from __future__ import annotations

import re
import time
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


def test_the_tray_icons_words_are_all_in_german() -> None:
    """The icon's menu, its tooltips, its notifications and the new choice (launcher_tray.py
    gives English keys; the window translates them)."""
    import sys

    gui = load_gui()
    T = gui.launcher_tray
    assert sys.modules["launcher_tray"] is T
    states = [
        T.TrayState(chat=c, model=m, kill=k, window_hidden=h, own_window=o)
        for c in (True, False)
        for m in ("ready", "off", "loading", "paused", "down")
        for k in (True, False, None)
        for h in (True, False)
        for o in (True, False)
    ]
    keys = {item.label for state in states for item in T.tray_menu(state) if item.label}
    keys |= {T.tray_status(state) for state in states}
    assert {"Open Starlight", "Open chat", "Show launcher window", "Kill switch", "Resume"} <= keys
    assert {"Quit Starlight", "kill switch on", "paused for a game", "model stopped"} <= keys
    keys |= {
        "Keep Starlight next to the clock when its window is closed",
        "Starlight is still running here. Right-click the icon to open or quit.",
        "Your answer is ready.",
        "The chat is in its own window (Open chat brings it back).",
        "Your chat is at {url}.",
        "Closing this window leaves Starlight running next to the clock; Quit Starlight stops it.",
    }
    missing = sorted(key for key in keys if key not in gui.GERMAN)
    assert missing == []


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
        # the kill switch keeps its size when it turns into "Resume" (now the main button)
        app.on_status(L.Status("ready", "", True, True, True, 5.0))
        assert app.btn_kill.cget("style") == "Big.Accent.TButton"
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
        # The X server hands the keyboard only to a window that is on the screen (mapped), and
        # a request made before that is ignored without a word. Mapping is the server's work:
        # on a busy machine (four workers on one display) it comes later than this test goes
        # on. So: wait until the window is shown, ask for the focus, then wait until Tk has
        # heard (FocusIn) that it has it; a key event made before that is dropped.
        deadline = time.monotonic() + 10
        while not app.root.winfo_viewable() and time.monotonic() < deadline:
            app.root.update()
        assert app.root.winfo_viewable(), "the window was not shown"
        app.root.focus_force()
        app.btn_stats.focus_set()  # (key events go to the widget with the focus)
        app.root.winfo_pointerxy()  # (a question to the server: answered after the focus change)
        app.root.update()
        while app.root.focus_displayof() is None and time.monotonic() < deadline:
            app.root.update()
        assert app.root.focus_displayof() is not None, "the window did not get the keyboard"
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


def test_the_error_page_says_what_to_try() -> None:
    hints = L.error_hints
    assert "smaller model" in hints("The model server did not start: CUDA out of memory")[0]
    assert "drive is full" in hints("not enough free disk space for the download")[0]
    assert "internet" in hints("A network problem stopped the download (timed out)")[0]
    assert "port" in hints("[WinError 10048] Only one usage of each socket address")[0]
    assert "OneDrive" in hints("[WinError 5] Access is denied: 'C:\\\\x'")[0]
    assert "launcher.json" in hints("C:\\\\S\\\\config\\\\launcher.json is not valid JSON")[0]
    assert "Try again" in hints("the model server did not become ready within 600 s")[0]
    assert hints("something nobody planned for") == []
    many = "out of memory; no space left; timed out; address already in use; access is denied"
    assert len(hints(many)) == 3  # (the first three)


def test_the_model_has_its_own_name(tmp_path: Any) -> None:
    settings = L.Settings(root=tmp_path)
    assert L.model_label(settings) == "Qwen2.5 14B, uncensored"  # the default, as setup shows it
    settings.model_url = L.MODEL_PRESETS["qwen3-14b"]
    assert L.model_label(settings) == L.MODEL_INFO["qwen3-14b"][0]
    settings.model_url = "https://example.org/models/My-Own-Model-Q5_K_M.gguf"
    assert L.model_label(settings) == "My-Own-Model-Q5_K_M"  # one of your own: its file
    settings.use_model = False
    assert L.model_label(settings) == ""


@needs_display
def test_the_error_page_shows_advice_first_and_the_details_on_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app(monkeypatch)
    try:
        app.on_error("The model server did not start: out of video memory", "CUDA out of memory")
        app.root.update()
        hints = [label.cget("text") for label in app.err_hint_labels]
        assert len(hints) == 1 and "choose a smaller model" in hints[0]
        assert app.err_hints.winfo_manager() == "grid"
        assert app.err_text.winfo_manager() == ""  # the traceback is folded away
        assert app.err_toggle.cget("text") == "Show details"
        app._show_error_details(None)  # "Show details"
        assert app.err_text.winfo_manager() == "grid"
        assert "CUDA out of memory" in app.err_text.get("1.0", "end")
        assert app.err_settings.winfo_manager() == "grid"
        app.err_settings.invoke()  # a smaller model, without starting over
        assert app.page == "setup" and app.in_settings

        app.on_error("Something nobody planned for", "details")
        assert app.err_hints.winfo_manager() == ""  # no advice: the details are shown at once
        assert app.err_text.winfo_manager() == "grid"
        app.fatal = "launcher.json is not valid JSON"
        app.on_error(app.fatal)
        assert app.err_settings.winfo_manager() == ""  # (the settings cannot be read)
    finally:
        app.root.destroy()
    app = make_app(monkeypatch, "de")
    try:
        app.on_error("The model server did not start: out of video memory")
        assert app.err_hint_labels[0].cget("text").startswith("•  Das Modell passt nicht")
        assert app.details_toggle.cget("text") == "Details zeigen"  # (the progress page's too)
    finally:
        app.root.destroy()


@needs_display
def test_while_stopped_resume_is_the_main_button(monkeypatch: pytest.MonkeyPatch) -> None:
    app = make_app(monkeypatch)
    try:
        app.on_status(L.Status("ready", "", True, True, False, 50.0))
        assert str(app.btn_chat.cget("style")) == "Big.Accent.TButton"
        assert str(app.btn_kill.cget("style")) == "Big.Danger.TButton"
        rows = {name: label.cget("text") for name, (_dot, _oval, label) in app.status_rows.items()}
        assert rows["Model"] == "Ready: Qwen2.5 14B, uncensored"  # its name, not its file's
        app.on_status(L.Status("ready", "", True, True, True, 60.0))
        assert str(app.btn_kill.cget("style")) == "Big.Accent.TButton"  # Resume, highlighted
        assert str(app.btn_chat.cget("style")) == "Big.TButton"
    finally:
        app.root.destroy()


@needs_display
def test_the_chat_row_says_where_the_chat_is(monkeypatch: pytest.MonkeyPatch) -> None:
    import types

    app = make_app(monkeypatch)
    try:
        app.chat_window = types.SimpleNamespace(wanted=True, reason="", running=lambda: True)
        app.on_status(L.Status("ready", "", True, True, False, 50.0))
        assert app.status_rows["Chat"][2].cget("text").endswith(" · in Starlight's own window")
        app.chat_window = types.SimpleNamespace(
            wanted=True, reason="no WebView2", running=lambda: False
        )
        app.on_status(L.Status("ready", "", True, True, False, 50.0))
        assert app.status_rows["Chat"][2].cget("text").endswith(" · in the browser")
    finally:
        app.root.destroy()


@needs_display
def test_enter_presses_the_main_button_and_esc_leaves_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app(monkeypatch)
    pressed: list[str] = []
    try:
        for name in ("btn_chat", "btn_kill", "btn_stats", "err_try", "setup_go", "setup_cancel"):
            getattr(app, name).configure(command=lambda name=name: pressed.append(name))
        app.root.deiconify()  # (shown, so a button can have the keyboard focus)
        app.on_status(L.Status("ready", "", True, True, False, 5.0))
        app.root.focus_force()
        app.root.update()
        app._on_return()
        app.on_status(L.Status("ready", "", True, True, True, 5.0))  # stopped: Resume
        app._on_return()
        app.btn_stats.focus_set()  # the button with the keyboard focus wins
        app.root.update()
        app._on_return()
        app.root.focus_set()
        app.on_error("Something nobody planned for")
        app.root.update()
        app._on_return()
        assert pressed == ["btn_chat", "btn_kill", "btn_stats", "err_try"]
        assert app._on_escape() is None  # (nothing to leave on the error page)
        app.open_setup(settings_mode=True)
        app.root.update()
        app._on_return()
        app._on_escape()
        assert pressed[-2:] == ["setup_go", "setup_cancel"]
    finally:
        app.root.destroy()


@needs_display
def test_cancel_in_settings_goes_back_where_settings_was_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_app(monkeypatch)
    try:
        app.on_error("The model server did not start: out of video memory")
        app.err_settings.invoke()  # Settings from the error page ...
        assert app.page == "setup"
        app._on_escape()  # ... and back: to the error, not to a "running" page
        assert app.page == "error"
        app.show("running")
        app.open_settings()
        app.on_setup_cancel()
        assert app.page == "running"
    finally:
        app.root.destroy()
