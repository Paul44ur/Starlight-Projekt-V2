"""The tray icon (launcher/launcher_tray.py): what its menu and tooltip say, how its events become
actions, the attention file the chat window writes for it, and the launcher window with it
(a stand-in for the icon: the real one is Windows-only)."""

from __future__ import annotations

import json
import sys
import time
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_launcher import L
from tests.test_launcher_look import make_app, needs_display

sys.path.insert(0, str(REPO / "launcher"))
try:
    import launcher_tray as T
finally:
    sys.path.pop(0)


class FakeBackend:
    """What the icon would do, recorded. ``click`` plays what the user does to the icon."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[str, str]] = []
        self.on_event: Callable[[str], None] = lambda event: None
        self.menu: Callable[[], list[Any]] = list

    def start(
        self, tip: str, on_event: Callable[[str], None], menu: Callable[[], list[Any]]
    ) -> bool:
        self.on_event, self.menu = on_event, menu
        self.calls.append(("start", tip))
        return self.ok

    def set_tip(self, tip: str) -> None:
        self.calls.append(("tip", tip))

    def balloon(self, text: str) -> None:
        self.calls.append(("balloon", text))

    def stop(self) -> None:
        self.calls.append(("stop", ""))

    def click(self, event: str) -> None:
        self.on_event(event)

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.calls]


def german(words: dict[str, str]) -> Callable[[str], str]:
    return lambda text: words.get(text, text)


def labels(state: Any) -> list[tuple[str, str, bool]]:
    return [(i.action, i.label, i.enabled) for i in T.tray_menu(state)]


# -- the menu and the tooltip ------------------------------------------------------------------


def test_the_menu_for_a_running_starlight() -> None:
    state = T.TrayState(chat=True, model="ready", kill=False)
    assert labels(state) == [
        ("open", "Open Starlight", True),
        ("", "", True),
        ("kill", "Kill switch", True),
        ("", "", True),
        ("quit", "Quit Starlight", True),
    ]
    assert T.tray_menu(state)[0].default  # (a double click does what the bold item does)
    assert [i.separator for i in T.tray_menu(state)] == [False, True, False, True, False]


def test_the_menu_follows_the_state() -> None:
    starting = T.TrayState(chat=False, model="loading", kill=None)
    assert labels(starting)[0] == ("open", "Open Starlight", False)  # not up yet
    assert ("kill", "Kill switch", False) in labels(starting)  # unknown: greyed, like the button
    engaged = T.TrayState(chat=True, model="ready", kill=True)
    assert ("kill", "Resume", True) in labels(engaged)
    hidden = T.TrayState(chat=True, model="ready", kill=False, window_hidden=True)
    assert [a for a, _l, _e in labels(hidden)][:2] == ["open", "show"]
    assert ("show", "Show launcher window", True) in labels(hidden)
    assert all(a != "show" for a, _l, _e in labels(T.TrayState(chat=True)))  # only when hidden
    browser = T.TrayState(chat=True, own_window=False)
    assert labels(browser)[0] == ("open", "Open chat", True)  # (the chat is in the browser)


def test_the_tooltip_says_what_is_going_on() -> None:
    ready = T.TrayState(chat=True, model="ready", kill=False, model_name="Qwen3 8B")
    assert T.tray_tooltip(ready) == "Starlight - ready - Qwen3 8B"
    assert T.tray_tooltip(T.TrayState(chat=True, model="off", kill=False)) == ("Starlight - ready")
    assert T.tray_tooltip(T.TrayState(chat=True, model="ready", kill=True, model_name="M")) == (
        "Starlight - kill switch on"
    )
    assert T.tray_tooltip(T.TrayState(model="loading")) == "Starlight - starting"
    assert T.tray_tooltip(T.TrayState(chat=True, model="paused", kill=False)) == (
        "Starlight - paused for a game"
    )
    assert T.tray_tooltip(T.TrayState(chat=True, model="down")) == "Starlight - model stopped"
    long = T.TrayState(chat=True, model="ready", kill=False, model_name="M" * 400)
    assert len(T.tray_tooltip(long)) <= T.TOOLTIP_LETTERS  # (Windows cuts at 127 letters)
    translated = T.tray_tooltip(ready, german({"ready": "bereit"}))
    assert translated == "Starlight - bereit - Qwen3 8B"


def test_clean_text_is_plain_and_short() -> None:
    assert T.clean("a\x00b\x1b[0m\r\nc\t d", 50) == "a b [0m c d"
    assert T.clean("x" * 300, 20) == "x" * 19 + "…"
    assert T.clean(None, 20) == "None"
    assert T.clean("  \n ", 20) == ""


# -- the icon's events ---------------------------------------------------------------------


def make_tray(
    ok: bool = True, now: list[float] | None = None
) -> tuple[T.Tray, FakeBackend, list[str]]:
    backend = FakeBackend(ok)
    actions: list[str] = []
    clock = now if now is not None else [0.0]
    tray = T.Tray(backend, actions.append, clock=lambda: clock[0])
    return tray, backend, actions


def test_a_tray_that_cannot_be_made_says_so() -> None:
    tray, backend, _actions = make_tray(ok=False)
    assert tray.start(T.TrayState(chat=True, model="ready", kill=False)) is False
    assert tray.running is False
    tray.update(T.TrayState(chat=True))
    tray.balloon("hello")
    tray.stop()
    assert backend.kinds() == ["start"]  # (nothing is asked of an icon that is not there)

    class Broken(FakeBackend):
        def start(self, *args: Any) -> bool:
            raise OSError("no")

    bad = T.Tray(Broken(), lambda action: None)
    assert bad.start() is False  # (an error is not thrown at the window)


def test_clicks_and_menu_choices_become_actions() -> None:
    now = [0.0]
    tray, backend, actions = make_tray(now=now)
    tray.start(T.TrayState(chat=True, model="ready", kill=False))
    backend.click("left")
    now[0] += 5
    backend.click("balloon")  # a click on the notification opens the chat too
    for choice in ("show", "kill", "quit", "open"):
        backend.click(f"menu:{choice}")
    backend.click("menu:rm-rf")  # (not a choice we offer)
    backend.click("right")
    backend.click("")
    assert actions == ["open", "open", "show", "kill", "quit", "open"]


def test_a_double_click_opens_the_chat_once() -> None:
    now = [10.0]
    tray, backend, actions = make_tray(now=now)
    tray.start()
    backend.click("left")
    now[0] += 0.2  # (the second click, and the double click message)
    backend.click("left")
    backend.click("left")
    assert actions == ["open"]
    now[0] += T.DOUBLE_CLICK + 0.1
    backend.click("left")
    assert actions == ["open", "open"]


def test_the_menu_is_built_from_the_newest_state_in_the_apps_words() -> None:
    backend = FakeBackend()
    tray = T.Tray(backend, lambda action: None, translate=german({"Kill switch": "Not-Aus"}))
    tray.start(T.TrayState(chat=True, model="ready", kill=False))
    first = [i.label for i in backend.menu()]
    assert "Not-Aus" in first
    tray.update(T.TrayState(chat=True, model="ready", kill=True, window_hidden=True))
    assert backend.menu()[1].action == "show"
    assert [i.action for i in backend.menu()] == ["open", "show", "", "kill", "", "quit"]


def test_the_tooltip_is_only_rewritten_when_it_changes() -> None:
    tray, backend, _actions = make_tray()
    ready = T.TrayState(chat=True, model="ready", kill=False, model_name="M")
    tray.start(ready)
    tray.update(ready)
    tray.update(
        T.TrayState(chat=True, model="ready", kill=False, model_name="M", window_hidden=True)
    )
    assert backend.kinds() == ["start"]  # (only the menu changed)
    tray.update(T.TrayState(chat=True, model="ready", kill=True, model_name="M"))
    assert backend.calls[-1] == ("tip", "Starlight - kill switch on")
    tray.update(T.TrayState(chat=True, model="ready", kill=True, model_name="M"))
    assert backend.kinds() == ["start", "tip"]


def test_a_balloon_is_clipped_and_plain_and_the_tray_stops_once() -> None:
    tray, backend, _actions = make_tray()
    tray.start()
    tray.balloon("Done\x1b[0m\n" + "x" * 400)
    kind, text = backend.calls[-1]
    assert kind == "balloon" and len(text) <= T.BALLOON_LETTERS and "\x1b" not in text
    tray.balloon("   ")  # nothing to say
    assert backend.kinds() == ["start", "balloon"]
    tray.stop()
    tray.stop()
    assert backend.kinds() == ["start", "balloon", "stop"]


def test_without_windows_there_is_no_icon(monkeypatch: pytest.MonkeyPatch) -> None:
    assert sys.platform != "win32"
    assert T.default_backend(Path("x")) is None
    backend = T.WinBackend()
    assert backend.start("tip", lambda event: None, list) is False
    backend.set_tip("x")
    backend.balloon("x")
    backend.stop()  # (nothing happens, nothing is thrown)
    with pytest.raises(OSError):
        T._win_api()


# -- the attention file ------------------------------------------------------------------------


def test_the_attention_file_round_trips(tmp_path: Path) -> None:
    assert L.read_attention(tmp_path) is None  # no file
    first = L.write_attention(tmp_path, "Your answer is ready")
    assert (tmp_path / "data" / "app-window" / "attention.json").is_file()
    assert L.read_attention(tmp_path) == (first, "Your answer is ready")
    assert L.read_attention(tmp_path, first) is None  # seen already
    time.sleep(0.001)
    second = L.write_attention(tmp_path, "Another")
    assert second != first
    assert L.read_attention(tmp_path, first) == (second, "Another")


def test_a_damaged_or_hostile_attention_file_is_ignored_or_cleaned(tmp_path: Path) -> None:
    path = tmp_path / "data" / "app-window" / "attention.json"
    path.parent.mkdir(parents=True)
    for junk in ("", "{", "[1]", '"text"', '{"id": "7", "text": "x"}', '{"id": true}', "{}"):
        path.write_text(junk, encoding="utf-8")
        assert L.read_attention(tmp_path) is None, junk
    path.write_text(json.dumps({"id": 5, "text": "Hi\x1b[31m\n\x00" + "y" * 900}), encoding="utf-8")
    found = L.read_attention(tmp_path)
    assert found is not None and found[0] == 5
    assert found[1].startswith("Hi [31m ") and len(found[1]) <= L.ATTENTION_LETTERS
    assert not any(c in found[1] for c in "\x1b\x00\n")
    path.write_text(json.dumps({"id": 6, "text": ["not", "text"]}), encoding="utf-8")
    assert L.read_attention(tmp_path) == (6, "['not', 'text']")  # (text anyway, never run)
    path.write_text(json.dumps({"id": 7}), encoding="utf-8")
    assert L.read_attention(tmp_path) == (7, "")


def test_the_launcher_and_the_chat_window_agree_on_the_file(tmp_path: Path) -> None:
    from starlight import app_window

    assert app_window.ATTENTION_TEXT == L.ATTENTION_LETTERS
    folder = tmp_path / "data" / "app-window"
    folder.mkdir(parents=True)
    assert (folder / app_window.ATTENTION_FILE) == tmp_path / L.ATTENTION_FILE
    assert app_window.write_attention(folder, "From the window\x00")
    found = L.read_attention(tmp_path)
    assert found is not None and found[1] == "From the window"


def test_the_tray_is_a_saved_setting_that_is_on_by_default(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "starlight"\n')

    def load() -> Any:
        return L.load_settings(L.build_parser().parse_args(["--root", str(tmp_path)]))

    assert load().tray is True and load().tray_hint_shown is False
    L.save_choices(tmp_path, {"tray": False, "tray_hint_shown": True})
    saved = load()
    assert saved.tray is False and saved.tray_hint_shown is True
    L.save_choices(tmp_path, {"tray": True})
    assert load().tray is True and load().tray_hint_shown is True  # (the other key is kept)


# -- the launcher window with the icon --------------------------------------------------------


def with_tray(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, language: str = "en"
) -> tuple[Any, FakeBackend]:
    """The window on its running page, with a stand-in for the icon."""
    app = make_app(monkeypatch, language)
    backend = FakeBackend()
    monkeypatch.setattr(T, "default_backend", lambda root: backend)  # (the window's own name)
    app.settings.root = tmp_path  # (what the window saves goes here, not into the checkout)
    app.root.deiconify()  # (the window is shown, as run() does it)
    app.root.update()
    return app, backend


def run_events(app: Any) -> None:
    """What the window's loop does: handle what the threads (or the icon) put on the queue."""
    while not app.events.empty():
        event = app.events.get_nowait()
        getattr(app, f"on_{event[0]}")(*event[1:])


def running_and_polled_by_hand(app: Any, services: Any) -> None:
    """``on_running()`` without the window's own polling. With ``services`` set, on_running
    starts a thread that runs ``_poll_once`` and goes on every 1.5 seconds; it would race with
    the test's own ``_poll_once`` calls (both read the same message, so it shows twice) and
    only on a busy machine, when the thread starts late."""
    app.services = None
    app.on_running()
    app.polling = False  # (the timer stops at its next tick)
    if app._poll_job is not None:
        app.root.after_cancel(app._poll_job)
        app._poll_job = None
    app.services = services


def ready(app: Any, kill: bool = False) -> None:
    app.on_status(L.Status("ready", "Qwen3 8B", True, True, kill, 50.0))


@needs_display
def test_the_icon_appears_when_starlight_runs_and_follows_its_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, backend = with_tray(monkeypatch, tmp_path)
    try:
        assert app.tray is None
        app.on_running()
        assert app.tray is not None and backend.kinds() == ["start"]
        assert backend.calls[0][1] == "Starlight - starting"
        ready(app)
        name = L.model_label(app.settings)  # (the model's own name, not its file's)
        assert backend.calls[-1] == ("tip", f"Starlight - ready - {name}")
        assert [i.action for i in backend.menu()] == ["open", "", "kill", "", "quit"]
        ready(app, kill=True)
        assert backend.calls[-1] == ("tip", "Starlight - kill switch on")
        assert ("kill", "Resume") in [(i.action, i.label) for i in backend.menu()]
        app.on_running()  # "Save and restart": still one icon
        assert backend.kinds().count("start") == 1
        ready(app)
        assert "Closing this window leaves Starlight running" in app.r_sub.cget("text")
    finally:
        app.on_exit()


@needs_display
def test_no_icon_when_it_is_off_or_cannot_be_made(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, backend = with_tray(monkeypatch, tmp_path)
    try:
        app.settings.tray = False
        app.on_running()
        assert app.tray is None and backend.calls == []
        quits: list[bool] = []
        monkeypatch.setattr(app, "quit_app", lambda: quits.append(True))
        app.on_close_window()
        assert quits == [True] and app.root.state() == "normal"
        # the setting on, but the icon cannot be made: same as without
        app.settings.tray = True
        backend.ok = False
        app.on_running()
        assert app.tray is None
        app.on_close_window()
        assert quits == [True, True]
        ready(app)  # (the status goes on without an icon)
    finally:
        app.on_exit()


@needs_display
def test_the_icon_is_not_made_where_there_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = make_app(monkeypatch)
    try:
        app.on_running()  # (here: not Windows, the real default_backend says None)
        assert app.tray is None
    finally:
        app.on_exit()


@needs_display
def test_the_x_hides_the_window_instead_of_stopping_starlight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, backend = with_tray(monkeypatch, tmp_path)
    stopped: list[bool] = []
    app.services = types.SimpleNamespace(stop=lambda: stopped.append(True))
    try:
        app.on_running()
        ready(app)
        app.root.deiconify()
        app.root.update()
        app.on_close_window()  # the X
        assert app.root.state() == "withdrawn" and app.closing is False
        assert stopped == [] and app.page == "running" and app.services is not None
        hint = [text for kind, text in backend.calls if kind == "balloon"]
        assert hint == ["Starlight is still running here. Right-click the icon to open or quit."]
        assert json.loads((tmp_path / "config" / "launcher.json").read_text())[
            "tray_hint_shown"
        ] is True  # fmt: skip
        assert [i.action for i in backend.menu()][:2] == ["open", "show"]  # it can come back
        app.on_tray("show")
        assert app.root.state() == "normal"
        assert "show" not in [i.action for i in backend.menu()]
        app.on_close_window()  # the second time: no hint again
        assert len([1 for kind, _ in backend.calls if kind == "balloon"]) == 1
        assert app.root.state() == "withdrawn"
    finally:
        app.services = None
        app.on_exit()


@needs_display
def test_on_the_other_pages_the_x_stops_as_before(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, backend = with_tray(monkeypatch, tmp_path)
    quits: list[bool] = []
    monkeypatch.setattr(app, "quit_app", lambda: quits.append(True))
    try:
        app.on_running()
        for page in ("progress", "setup", "error"):
            app.show(page)
            app.on_close_window()
        assert quits == [True, True, True] and "balloon" not in backend.kinds()
        # a page that needs you shows the window again, if it was hidden
        app.show("running")
        app.on_close_window()
        assert app.root.state() == "withdrawn"
        app.show("progress")
        assert app.root.state() == "normal"
        app.on_error("A problem")
        assert app.tray is None and backend.kinds()[-1] == "stop"  # (no icon for a stopped one)
    finally:
        app.on_exit()


@needs_display
def test_the_icon_menu_opens_stops_and_quits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, backend = with_tray(monkeypatch, tmp_path)
    opened: list[str] = []
    monkeypatch.setattr(sys.modules["launcher_gui"].webbrowser, "open", opened.append)
    kills: list[bool] = []
    app.services = types.SimpleNamespace(set_kill_switch=kills.append)
    quits: list[str] = []
    monkeypatch.setattr(app, "quit_app", lambda: quits.append(app.root.state()))
    try:
        app.on_running()
        app.on_close_window()  # (hidden in the tray)
        ready(app)
        # a click on the icon, through the icon's queue as the real thread does it
        backend.click("left")
        run_events(app)
        assert opened and opened[0].startswith("http://127.0.0.1:")  # (no own window here)
        backend.click("menu:kill")
        run_events(app)
        deadline = time.monotonic() + 5
        while not kills and time.monotonic() < deadline:
            time.sleep(0.01)
        assert kills == [True]  # the same call as the window's button: engage
        ready(app, kill=True)
        backend.click("menu:kill")
        run_events(app)
        deadline = time.monotonic() + 5
        while len(kills) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert kills == [True, False]  # Resume
        backend.click("menu:quit")
        run_events(app)
        assert quits == ["normal"]  # the question has a window to show in
    finally:
        app.services = None
        app.on_exit()


@needs_display
def test_the_kill_switch_waits_while_it_is_not_known(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, backend = with_tray(monkeypatch, tmp_path)
    kills: list[bool] = []
    app.services = types.SimpleNamespace(set_kill_switch=kills.append)
    try:
        app.on_running()
        app.on_status(L.Status("loading", "", False, None, None, 5.0))
        assert ("kill", "Kill switch", False) in [
            (i.action, i.label, i.enabled) for i in backend.menu()
        ]
        backend.click("menu:kill")
        run_events(app)
        time.sleep(0.1)
        assert kills == []
        # and a click on the icon while it starts shows the window, not a chat that is not there
        app.root.withdraw()
        backend.click("left")
        run_events(app)
        assert app.root.state() == "normal"
    finally:
        app.services = None
        app.on_exit()


@needs_display
def test_a_closed_chat_window_leaves_the_launcher_window_alone_with_the_tray(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, _backend = with_tray(monkeypatch, tmp_path)
    state = {"open": True}
    chat = types.SimpleNamespace(
        wanted=True, reason="", running=lambda: state["open"], open=lambda url: None
    )
    app.chat_window = chat
    try:
        app.on_running()
        ready(app)
        app.root.withdraw()  # (hidden in the tray)
        state["open"] = False  # the chat window is closed
        ready(app)
        assert app.root.state() == "withdrawn"  # it does not pop back up
        chat.reason = "needs WebView2"  # but a problem is something to say
        ready(app)
        assert app.root.state() == "normal"
        # without the icon it comes back as it always did
        app._stop_tray()
        app.root.withdraw()
        app.window_was_open, chat.reason, app.told_why = True, "", False
        ready(app)
        assert app.root.state() == "normal"
    finally:
        app.on_exit()


@needs_display
def test_opening_the_chat_does_not_bring_a_hidden_launcher_window_to_the_taskbar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, _backend = with_tray(monkeypatch, tmp_path)
    app.chat_window = types.SimpleNamespace(
        wanted=True, reason="", running=lambda: True, open=lambda url: None
    )
    try:
        app.on_running()
        iconified: list[bool] = []
        monkeypatch.setattr(app.root, "iconify", lambda: iconified.append(True))
        app.root.withdraw()
        app.open_chat()
        assert app.root.state() == "withdrawn" and iconified == []  # (a taskbar button: no)
        app.root.deiconify()
        app.open_chat()
        assert iconified == [True]  # as before: this window waits in the taskbar
    finally:
        app.on_exit()


@needs_display
def test_quitting_removes_the_icon_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    app, backend = with_tray(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_shutdown", lambda: None)  # (nothing is really stopped here)
    answers: list[str] = []
    gui = sys.modules["launcher_gui"]
    monkeypatch.setattr(
        gui.messagebox, "askyesno", lambda title, text, **kw: answers.append(text) or False
    )
    app.services = object()  # (a running Starlight: quit_app asks first)
    try:
        app.on_running()
        app.on_tray("quit")
        run_events(app)
        assert answers == ["Stop Starlight and close the window?"]
        assert app.tray is not None and "stop" not in backend.kinds()  # "no": all stays
        monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **kw: True)
        app.on_tray("quit")
        run_events(app)
        assert app.tray is None and backend.kinds()[-1] == "stop"
        assert app.closing is True
    finally:
        app.services = None
        app.on_exit()


@needs_display
def test_the_window_asks_for_a_notification_when_the_chat_window_has_news(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, backend = with_tray(monkeypatch, tmp_path)
    try:
        running_and_polled_by_hand(app, types.SimpleNamespace(poll=lambda: None))
        app._poll_once()
        run_events(app)
        assert "balloon" not in backend.kinds()  # (nothing was said)
        L.write_attention(tmp_path, "Your answer is ready\x1b[0m")
        app._poll_once()
        run_events(app)
        assert backend.calls[-1] == ("balloon", "Your answer is ready [0m")
        app._poll_once()  # (the same message is not shown twice)
        run_events(app)
        assert [k for k, _ in backend.calls].count("balloon") == 1
        L.write_attention(tmp_path, "")
        app._poll_once()
        run_events(app)
        assert backend.calls[-1] == ("balloon", "Your answer is ready.")  # (a plain default)
        backend.click("balloon")  # a click on it opens the chat
        opened: list[str] = []
        monkeypatch.setattr(app, "open_chat", lambda page="": opened.append("chat"))
        ready(app)
        run_events(app)
        backend.click("balloon")
        run_events(app)
        assert opened == ["chat"]
    finally:
        app.services = None
        app.on_exit()


@needs_display
def test_an_old_message_does_not_pop_up_when_the_window_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    L.write_attention(tmp_path, "From yesterday")
    monkeypatch.setattr(L, "load_settings", lambda args: L.Settings(root=tmp_path))
    app = make_app(monkeypatch)
    try:
        assert app._attention_seen == L.read_attention(tmp_path)[0]  # type: ignore[index]
    finally:
        app.on_exit()


@needs_display
def test_the_settings_page_has_the_tray_choice_and_saves_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = make_app(monkeypatch)
    app.settings.root = tmp_path
    try:
        app.open_setup(settings_mode=True)
        assert app.tray_var.get() is True  # (on by default)
        app.tray_var.set(False)
        app.begin = lambda: None  # type: ignore[method-assign]
        app.in_settings = False
        monkeypatch.setattr(sys, "platform", "win32")  # (Windows saves its own choices)
        monkeypatch.setattr(L, "autostart_enabled", lambda: False)
        app.on_setup_go()
        monkeypatch.undo()
        assert app.settings.tray is False
        assert json.loads((tmp_path / "config" / "launcher.json").read_text())["tray"] is False
    finally:
        app.on_exit()
