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

    threading.Thread(target=server.serve_forever, daemon=True).start()
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
