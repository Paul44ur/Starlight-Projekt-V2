"""The launcher's window and its helpers: sizes and times, the graphics-card choice, saved
choices, when the window opens, the status of the running Starlight, and the console version.
The window itself is built on a virtual display where there is one (CI has it on Windows)."""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_launcher import L

GB = 1024**3
MB = 1024**2


def parse(*argv: str) -> argparse.Namespace:
    return L.build_parser().parse_args(list(argv))


# -- numbers shown to people -----------------------------------------------------------------------


def test_sizes_and_times_read_naturally() -> None:
    assert L.format_size(9 * GB) == "9.0 GB"
    assert L.format_size(12_345_678) == "11.8 MB"
    assert L.format_size(250 * MB) == "250 MB"
    assert L.format_size(2048) == "2 KB"
    assert L.format_duration(0) == "0 s"
    assert L.format_duration(75) == "1 min 15 s"
    assert L.format_duration(5000) == "1 h 23 min"


def test_transfer_text_has_speed_and_time_left() -> None:
    started = time.monotonic() - 10
    fraction, text = L.transfer_text(100 * MB, 400 * MB, started)
    assert 0.24 < fraction < 0.26
    assert "100 MB of 400 MB" in text and "MB/s" in text and "left" in text
    fraction, text = L.transfer_text(5 * MB, None, started)  # size not known
    assert fraction == -1 and "left" not in text


# -- the graphics card -----------------------------------------------------------------------------


def test_the_real_graphics_card_is_picked_not_a_placeholder() -> None:
    gpu = L.pick_gpu(
        [
            ("Microsoft Basic Display Adapter", None),
            ("Intel(R) UHD Graphics", 1 * GB),
            ("AMD Radeon RX 9060 XT", 16 * GB),
        ]
    )
    assert gpu is not None and gpu.name == "AMD Radeon RX 9060 XT" and gpu.vram == 16 * GB
    assert L.pick_gpu([("Microsoft Remote Display Adapter", None)]) is None
    assert L.pick_gpu([]) is None


@pytest.mark.parametrize(
    ("gpu", "preset"),
    [
        (L.Gpu("RTX 5090", 32 * GB), "qwen3.8-27b"),
        (L.Gpu("RX 7900 XTX", 24 * GB), "qwen3.8-27b"),
        (L.Gpu("RX 9060 XT", 16 * GB), "qwen14b-uncensored"),
        (L.Gpu("RTX 3060", 12 * GB), "qwen14b-uncensored"),
        (L.Gpu("GTX 1660", 6 * GB), "qwen7b"),
        (L.Gpu("Unknown", None), "qwen14b-uncensored"),
        (None, "qwen14b-uncensored"),
    ],
)
def test_the_model_fits_the_video_memory(gpu: Any, preset: str) -> None:
    chosen, why = L.recommend_model(gpu)
    assert chosen == preset and chosen in L.MODEL_PRESETS and why.endswith(".")


def test_every_preset_is_described_in_the_window() -> None:
    assert set(L.MODEL_INFO) == set(L.MODEL_PRESETS)
    for title, text, memory in L.MODEL_INFO.values():
        assert title and text and memory > 0


def test_every_preset_has_a_download_size_and_its_own_file() -> None:
    from tests.test_cleanup_launcher import load_gui  # (it imports this module)

    gui = load_gui()
    assert set(gui.App.APPROX_GB) == set(L.MODEL_PRESETS)
    files = [L._file_name(url) for url in L.MODEL_PRESETS.values()]
    assert len(set(files)) == len(files)  # no two presets would share models/<file>
    for url in L.MODEL_PRESETS.values():
        assert url.startswith("https://huggingface.co/") and "/resolve/main/" in url
    # the large and the changed models come last, each under a heading in the setup window
    keys = list(L.MODEL_PRESETS)
    assert keys[-len(L.MODIFIED) :] == list(L.MODIFIED)
    start = keys.index(L.LARGE[0])
    assert keys[start : start + len(L.LARGE)] == list(L.LARGE)
    assert all(L.MODEL_INFO[key][2] > 16 for key in L.LARGE)  # what a 16 GB card cannot hold


# -- remembered choices ----------------------------------------------------------------------------


def test_choices_are_saved_and_other_settings_are_kept(tmp_path: Path) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "launcher.json").write_text('{"port": 8123, "model_url": "old"}', encoding="utf-8")
    L.save_choices(tmp_path, {"model_url": "new", "voice": True}, drop=("tesseract",))
    saved = json.loads((config / "launcher.json").read_text(encoding="utf-8"))
    assert saved == {"port": 8123, "model_url": "new", "voice": True}
    assert not list(config.glob("*.part"))


def test_choices_are_saved_when_there_is_no_file_yet(tmp_path: Path) -> None:
    L.save_choices(tmp_path, {"voice": False})
    assert json.loads((tmp_path / "config" / "launcher.json").read_text()) == {"voice": False}


# -- window or text --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "window"),
    [
        ([], True),
        (["--gui"], True),
        (["--no-gui"], False),
        (["--yes"], False),
        (["--terminal"], False),
        (["--both"], False),
        (["--setup-only"], False),
        (["--update"], False),
        (["--ask", "hi"], False),
        (["--yes", "--gui"], True),  # asking for the window wins
        (["--gui", "--no-gui"], False),  # and asking for no window wins over that
    ],
)
def test_the_window_opens_unless_a_script_or_terminal_option_is_used(
    argv: list[str], window: bool
) -> None:
    assert L.gui_wanted(parse(*argv)) is window


# -- the console version ---------------------------------------------------------------------------


def run_console(monkeypatch: pytest.MonkeyPatch, action: Any) -> str:
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    action(L.ConsoleUi(assume_yes=True))
    return out.getvalue()


def test_the_console_shows_a_bar_with_speed_and_time(monkeypatch: pytest.MonkeyPatch) -> None:
    text = run_console(
        monkeypatch, lambda ui: ui.progress(200 * MB, 400 * MB, time.monotonic() - 10)
    )
    assert "50%" in text and "200 MB of 400 MB" in text and "left" in text


def test_the_console_marks_steps_and_shows_a_waiting_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def action(ui: Any) -> None:
        ui.step("Starting the model server")
        ui.waiting("\rloading the model ... 12 s")

    text = run_console(monkeypatch, action)
    assert "Starting the model server" in text and "loading the model ... 12 s" in text


def test_colors_only_on_a_real_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", io.StringIO())  # a file or a pipe: plain lines
    assert type(L.make_ui(True)) is L.Ui

    class Terminal(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdout", Terminal())
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(L, "enable_ansi", lambda: True)  # (CI's Windows has no real console)
    assert isinstance(L.make_ui(True), L.ConsoleUi)
    monkeypatch.setattr(L, "enable_ansi", lambda: False)  # an old console that cannot do colors
    assert type(L.make_ui(True)) is L.Ui
    monkeypatch.setattr(L, "enable_ansi", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    assert type(L.make_ui(True)) is L.Ui


# -- the status of the running Starlight -----------------------------------------------------------


class Answers(BaseHTTPRequestHandler):
    code = 200
    kill: dict[str, Any] = {"engaged": False}  # noqa: RUF012
    posts: list[tuple[str, str | None]] = []  # noqa: RUF012

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, code: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(type(self).code, {"status": "ok"})
        else:
            self._send(200, {"kill": type(self).kill})

    def do_POST(self) -> None:
        type(self).posts.append((self.path, self.headers.get("Origin")))
        type(self).kill = {"engaged": self.path == "/api/kill"}
        self._send(200, {"ok": True})


@pytest.fixture
def answering() -> Any:
    Answers.code, Answers.kill, Answers.posts = 200, {"engaged": False}, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Answers)
    import threading

    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_the_model_health_is_read_from_its_server(answering: int) -> None:
    assert L.health_status(answering) == 200
    Answers.code = 503
    assert L.health_status(answering) == 503
    server = ThreadingHTTPServer(("127.0.0.1", 0), Answers)
    free = server.server_address[1]
    server.server_close()
    assert L.health_status(free) is None  # nothing listens there


def test_local_json_is_none_when_nothing_answers(answering: int) -> None:
    assert L.fetch_json(f"http://127.0.0.1:{answering}/api/status") == {"kill": {"engaged": False}}
    assert L.fetch_json("http://127.0.0.1:9/api/status", timeout=0.5) is None


def test_local_calls_do_not_go_through_a_system_proxy(
    answering: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behind a company proxy that does not skip loopback, the model server on this PC must
    still be reached directly, or the launcher waits for it forever."""
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "http_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")  # nothing listens there
    # (urlopen reads the proxy settings once, when its opener is built)
    monkeypatch.setattr("urllib.request._opener", urllib.request.build_opener())
    with pytest.raises(urllib.error.URLError):  # the setup of this test: the proxy is in the way
        urllib.request.urlopen(f"http://127.0.0.1:{answering}/health", timeout=2)
    assert L.health_status(answering) == 200
    assert L.fetch_json(f"http://127.0.0.1:{answering}/api/status") == {"kill": {"engaged": False}}
    L.wait_for_model(answering, None, tmp_path / "model.log", L.Ui(True), timeout=10)


def test_the_status_says_loading_then_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answering: int
) -> None:
    settings = L.Settings(root=tmp_path)
    settings.port, settings.viewer = answering, False
    monkeypatch.setattr(L, "WEB_PORT", answering)  # the same stand-in plays the chat page
    services = L.Services(settings, L.Ui(True), L.Installed(uv=Path("uv")))
    Answers.code = 503
    status = services.poll()
    assert status.model == "loading" and status.chat and status.kill is False
    Answers.code = 200
    assert services.poll().model == "ready"


def test_the_kill_switch_is_set_through_the_chat_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answering: int
) -> None:
    settings = L.Settings(root=tmp_path)
    settings.port, settings.viewer = answering, False
    monkeypatch.setattr(L, "WEB_PORT", answering)
    services = L.Services(settings, L.Ui(True), L.Installed(uv=Path("uv")))
    assert services.set_kill_switch(True)
    assert services.poll().kill is True
    assert services.set_kill_switch(False)
    assert services.poll().kill is False
    origin = f"http://127.0.0.1:{answering}"
    assert Answers.posts == [("/api/kill", origin), ("/api/resume", origin)]


def test_a_test_model_has_no_server_to_wait_for(tmp_path: Path) -> None:
    settings = L.Settings(root=tmp_path)
    settings.use_model, settings.viewer = False, False
    services = L.Services(settings, L.Ui(True), L.Installed(uv=Path("uv")))
    assert services.poll().model == "off"
    assert services.restart_model() is False  # this launcher did not start one


# -- the window ------------------------------------------------------------------------------------


def has_display() -> bool:
    if sys.platform == "win32" or os.environ.get("DISPLAY"):
        try:
            import tkinter

            tkinter.Tk().destroy()
        except Exception:
            return False
        return True
    return False


@pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")
def test_the_window_builds_all_its_pages() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "launcher" / "starlight_launcher.py"), "--gui-selftest"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "gui ok" in result.stdout


@pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")
def test_the_model_cards_keep_one_size_and_never_cut_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_cleanup_launcher import load_gui
    from tests.test_lmstudio import starlight_root  # (they import this module)

    gui = load_gui()
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    root = starlight_root(tmp_path)
    monkeypatch.setenv("UV_CACHE_DIR", str(root / ".uv-cache"))
    app = gui.App(L, L.build_parser().parse_args(["--root", str(root)]))
    try:
        app.root.deiconify()
        app.open_setup(False)

        def settle() -> None:
            for _ in range(8):
                app.root.update()
                time.sleep(0.03)

        def check() -> int:
            settle()
            cards = [card for card in app.cards.values() if card["frame"].winfo_manager()]
            heights = {card["frame"].winfo_height() for card in cards}
            assert len(heights) == 1  # badges or a long name never make one card taller
            for card in cards:
                for label in (card["name"], card["desc"], card["meta"]):
                    assert label.winfo_reqwidth() <= label.winfo_width() + 1, label.cget("text")
            edges = {card["side"].winfo_x() + card["side"].winfo_width() for card in cards}
            assert len(edges) == 1  # the right-hand column lines up
            texts = {card["desc"].winfo_x() for card in cards}
            assert len(texts) == 1  # and so do the descriptions
            return heights.pop()

        assert check() > 0
        app.on_gpu(L.Gpu("AMD Radeon RX 9060 XT", 16 * L.GB))  # badges appear
        for url in L.MODEL_PRESETS.values():
            app.on_size(url, 9_000_000_000)
        app.on_lmstudio_found(L.LMStudioState(True, "a-very-long-model-name-" * 4, 32768))
        wide = check()
        width, height = app.root.minsize()
        app.root.geometry(f"{width}x{height}")  # the narrowest window: text wraps, not cut
        assert check() >= wide
        for label in app.wrapping:
            if label.winfo_ismapped():
                assert label.winfo_reqwidth() <= app.scroller.winfo_width()
        app.root.geometry(f"{width + 400}x{height}")
        assert check() == wide  # and back on one line when there is room again
        assert "…" in app.cards[gui.LMSTUDIO]["meta"].cget("text")  # long names are shortened
    finally:
        app.root.destroy()


@pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")
def test_the_model_list_folds_tags_flags_and_frees_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_cleanup_launcher import load_gui
    from tests.test_lmstudio import starlight_root  # (they import this module)

    gui = load_gui()
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    root = starlight_root(tmp_path)
    monkeypatch.setenv("UV_CACHE_DIR", str(root / ".uv-cache"))
    app = gui.App(L, L.build_parser().parse_args(["--root", str(root)]))
    downloaded = app._model_file_of("qwen7b")
    downloaded.parent.mkdir(parents=True, exist_ok=True)
    downloaded.write_bytes(b"gguf")
    try:
        app.open_setup(False)

        def shown(key: str) -> bool:
            return bool(app.cards[key]["frame"].winfo_manager())

        def badges(key: str) -> list[str]:
            return [w.cget("text") for w in app.cards[key]["badges"].winfo_children()]

        # tags tell the models apart
        tags = [w.cget("text") for w in app.cards["qwen3-coder-30b"]["tags"].winfo_children()]
        assert tags == ["quick", "code"]
        # without a known graphics card every model shows; with one, the too-big ones fold
        assert shown("qwen3-32b") and not app.show_big_check.winfo_manager()
        app.on_gpu(L.Gpu("AMD Radeon RX 9060 XT", 16 * L.GB))
        assert not shown("qwen3-32b") and not shown("dolphin-mistral-24b")
        assert shown("qwen14b-uncensored") and shown("gpt-oss-20b")  # 14 GB fits 16 GB
        large_heading = app.sections[1][0]
        assert large_heading is not None and not large_heading.winfo_manager()
        assert "12 models too big" in app.show_big_check.text
        app.show_big_var.set(True)
        app._apply_visibility()
        assert shown("qwen3-32b") and large_heading.winfo_manager()
        assert "Too big for your graphics card" in badges("qwen3-32b")
        app.show_big_var.set(False)
        app._apply_visibility()

        # Up and Down choose the model above or below among the shown ones
        app.select_model("qwen14b-uncensored", touched=True)
        assert app._move_selection(1) == "break" and app.model_var.get() == "qwen14b"
        app._move_selection(-1)
        app._move_selection(-1)  # the first stays chosen
        assert app.model_var.get() == "qwen14b-uncensored"
        keys = app._shown_keys()
        assert "qwen3-32b" not in keys and keys[-1] == gui.LMSTUDIO

        # a link that answers "not found" is flagged and cannot be installed
        app.on_missing(L.MODEL_PRESETS["mistral-nemo"])
        assert "Download link not found" in badges("mistral-nemo")
        app.select_model("mistral-nemo", touched=True)
        assert "download link does not work" in app.summary.cget("text")
        assert app.setup_go.instate(["disabled"])
        app.select_model("qwen14b", touched=True)
        assert not app.setup_go.instate(["disabled"])

        # the thinking option shows only for the models that think first
        app.select_model("qwen3-14b", touched=True)
        assert app.think_check.winfo_manager()
        app.think_var.set(False)
        app.select_model("qwen14b", touched=True)
        assert not app.think_check.winfo_manager()

        # a downloaded model that is not chosen can be removed to free its space
        assert badges("qwen7b") == ["Downloaded", "Remove"]
        monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: False)
        app.remove_model("qwen7b")
        assert downloaded.exists()  # "No" keeps it
        monkeypatch.setattr(gui.messagebox, "askyesno", lambda *a, **k: True)
        app.select_model("qwen7b", touched=True)
        assert "Remove" not in badges("qwen7b")  # not the chosen one
        app.remove_model("qwen7b")
        assert downloaded.exists()
        app.select_model("qwen14b", touched=True)
        app.remove_model("qwen7b")
        assert not downloaded.exists() and "Downloaded" not in badges("qwen7b")
    finally:
        app.root.destroy()


@pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")
def test_the_install_button_stays_in_view_and_pages_open_at_their_top(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_cleanup_launcher import load_gui
    from tests.test_lmstudio import starlight_root  # (they import this module)

    gui = load_gui()
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    root = starlight_root(tmp_path, {"model_server": "lmstudio"})
    monkeypatch.setenv("UV_CACHE_DIR", str(root / ".uv-cache"))
    app = gui.App(L, L.build_parser().parse_args(["--root", str(root)]))
    try:
        app.root.deiconify()
        app.open_setup(False)

        def settle() -> None:
            for _ in range(6):
                app.root.update()
                time.sleep(0.03)

        settle()
        # the bar with "Install and start" is outside the scrolled list: always in view
        assert app.setup_bar.winfo_manager() == "pack" and app.setup_go.winfo_ismapped()
        app.scroller.yview_moveto(1.0)
        settle()
        assert app.scroller.yview()[0] > 0
        app.show("running")
        settle()
        assert app.scroller.yview()[0] == 0  # the next page opens at its top
        assert not app.setup_bar.winfo_manager()  # and the bar is only for the setup page

        # the check boxes: a click toggles them, not while disabled
        app.show("setup")
        settle()
        before = app.voice_var.get()
        voice = next(row for row in app.check_rows if row.variable is app.voice_var)
        voice.label.event_generate("<Button-1>")
        assert app.voice_var.get() is not before
        app.gpu_check.state(["disabled"])
        assert app.gpu_check.instate(["disabled"])
        on = app.gpu_var.get()
        app.gpu_check.event_generate("<Button-1>")
        assert app.gpu_var.get() == on

        # with LM Studio, the model can be reloaded from the status page
        app.services = object()
        app.on_status(L.Status("ready", "qwen3-14b (LM Studio)", True, True, False, 30.0))
        assert not app.btn_restart.instate(["disabled"])
        assert app.btn_restart.cget("text") == "Reload the model"
    finally:
        app.root.destroy()


@pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")
def test_tags_filter_the_list_explain_themselves_and_the_title_follows_the_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_cleanup_launcher import load_gui
    from tests.test_lmstudio import starlight_root  # (they import this module)

    gui = load_gui()
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    root = starlight_root(tmp_path)
    monkeypatch.setenv("UV_CACHE_DIR", str(root / ".uv-cache"))
    app = gui.App(L, L.build_parser().parse_args(["--root", str(root)]))
    try:
        app.open_setup(False)
        assert app.root.title() == "Starlight · setup"

        def shown() -> set[str]:
            return {k for k in L.MODEL_PRESETS if app.cards[k]["frame"].winfo_manager()}

        everything = shown()
        app._filter_by("code")
        chosen = app.model_var.get()
        assert shown() == {k for k in L.MODEL_PRESETS if "code" in L.MODEL_TAGS.get(k, ())} | {
            chosen
        }
        assert app.cards[gui.LMSTUDIO]["frame"].winfo_manager()  # your own models stay
        assert app.chips["code"].cget("bg") == app.c.accent
        app._filter_by("code")  # a second click shows all again
        assert shown() == everything and app.chips[""].cget("bg") == app.c.accent

        # a tag explains itself when pointed at
        tag = app.cards["qwen3-14b"]["tags"].winfo_children()[0]
        tip = gui.Tooltip(app, tag, L.TAG_INFO["thinks first"])
        tip._show()
        assert tip.window is not None and tip.window.winfo_exists()
        tip._hide()
        assert tip.window is None

        # the window title says how Starlight is doing (and so does the taskbar)
        app.show("running")
        app.on_status(L.Status("loading", "", False, True, None, 5.0))
        assert app.root.title() == "Starlight · starting"
        app.on_status(L.Status("ready", "", True, True, False, 50.0))
        assert app.root.title() == "Starlight · ready"
        app.on_status(L.Status("ready", "", True, True, True, 60.0))
        assert app.root.title() == "Starlight · kill switch on"
        assert app.r_footer.cget("text") == f"Folder: {app.settings.root}"
    finally:
        app.root.destroy()


@pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")
def test_the_window_shows_what_is_new_after_an_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_cleanup_launcher import load_gui
    from tests.test_lmstudio import starlight_root

    gui = load_gui()
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    root = starlight_root(tmp_path)
    (root / "docs").mkdir(exist_ok=True)
    notes = root / "docs" / "release-notes.md"
    notes.write_text("## Starlight V2 1.00.00\n\nFirst.\n", encoding="utf-8")
    L.whats_new(root)  # the installed version, seen once
    notes.write_text("## Starlight V2 1.01.00\n\n- Hey Starlight\n", encoding="utf-8")

    class Started:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def start(self) -> None:
            pass

    monkeypatch.setattr(L, "setup", lambda settings, ui: L.Installed(uv=Path("uv")))
    monkeypatch.setattr(L, "Services", Started)
    app = gui.App(L, L.build_parser().parse_args(["--root", str(root)]))
    try:
        app._work()  # what the window's worker thread runs
        events = []
        while not app.events.empty():
            events.append(app.events.get())
        assert ("running",) in events
        notes_shown = [e for e in events if e[0] == "note"]
        assert notes_shown and "Starlight V2 1.01.00" in notes_shown[0][1]
        assert "- Hey Starlight" in notes_shown[0][1]
    finally:
        app.root.destroy()
