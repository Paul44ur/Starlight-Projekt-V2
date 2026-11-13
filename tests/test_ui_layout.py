"""Layout guards for the web pages and the launcher window: the real logo instead of an emoji,
bubbles that stay in their column, a bar that wraps, cards that scroll their tables, a short
input hint on a phone, a window that fits the screen, and a page that scrolls when it is taller.
The page checks read the files (they guard the CSS that was checked in a browser at phone,
tablet and desktop widths); the window checks need a display (CI has one on Windows)."""

from __future__ import annotations

import pytest

from tests.conftest import REPO
from tests.test_cleanup_launcher import load_gui
from tests.test_launcher import L
from tests.test_launcher_window import has_display

STATIC = REPO / "starlight" / "web" / "static"


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


NIGHT_SKY = ("settings.html", "memory.html", "pair.html", "activity.html", "automations.html",
             "kill.html")  # fmt: skip


def test_every_page_shows_the_real_logo_not_an_emoji() -> None:
    for name in ("chat.html", "memory.html", "settings.html", "pair.html", "notes.html",
                 "activity.html", "automations.html", "kill.html"):  # fmt: skip
        page = read(name)
        assert "⭐" not in page, name
        if name in NIGHT_SKY:  # (the Night Sky look: the gold star icon is its mark)
            assert 'data-icon="star"' in page, name
            continue
        assert '<img class="logo" src="/icon.svg"' in page, name


def test_hidden_wins_over_a_class_that_sets_display() -> None:
    # #where is a .pill (display: inline-block) that starts out with the hidden attribute.
    assert "[hidden] { display: none !important; }" in read("style.css")


def test_message_bubbles_never_wider_than_their_column() -> None:
    chat = read("chat.css")  # (the page's look moved out of chat.html)
    assert "max-width: min(780px, 100%)" in chat
    assert "overflow-wrap: anywhere" in chat


def test_the_bar_wraps_and_a_wide_table_scrolls_inside_its_card() -> None:
    assert "flex-wrap: wrap" in read("style.css")
    assert "overflow-x: auto" in read("style.css")
    assert ".trow { flex-wrap: wrap;" in read("settings.html")  # (a tool's row, on a phone)


def test_the_input_hint_is_short_on_a_phone() -> None:
    assert 'matchMedia("(max-width: 760px)")' in read("chat-core.js")


@pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")
def test_the_window_fits_the_screen_and_a_taller_page_scrolls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gui = load_gui()
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    app = gui.App(L, L.build_parser().parse_args(["--gui"]))
    root = app.root
    try:
        root.deiconify()
        root.update()
        assert root.winfo_width() <= root.winfo_screenwidth()
        assert root.winfo_height() <= root.winfo_screenheight()
        root.minsize(1, 1)
        root.geometry("500x300")  # shorter than the setup page
        app.open_setup(False)
        root.update()
        root.update()
        assert app._scrollable and app.scrollbar.winfo_ismapped()
        app.scroller.yview_moveto(1.0)
        assert app.scroller.yview()[1] == pytest.approx(1.0)
    finally:
        root.destroy()
