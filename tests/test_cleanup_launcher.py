"""Fixes in the launcher and its window: the window's event loop survives a failing handler,
a restart does not double the status polling, a retry after a settings error reads the
settings again, a launcher.json with a byte order mark loads, and small helpers.

The window's methods are run on an App built without Tk (``App.__new__``), so most of this
needs no screen; the retry test builds the real window and needs one."""

from __future__ import annotations

import importlib.util
import json
import queue
import subprocess
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_launcher import L, QuietUi
from tests.test_launcher_window import has_display


def load_gui() -> Any:
    """launcher/launcher_gui.py (it imports tkinter, so no tkinter: the test is skipped)."""
    pytest.importorskip("tkinter")
    if "launcher_gui" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "launcher_gui", REPO / "launcher" / "launcher_gui.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules["launcher_gui"] = module
        spec.loader.exec_module(module)
    return sys.modules["launcher_gui"]


class FakeRoot:
    """Just the timer part of a Tk root: what was scheduled and what was cancelled."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[str, int, Any]] = []
        self.cancelled: list[str] = []
        self.fired: list[str] = []

    def after(self, milliseconds: int, callback: Any) -> str:
        job = f"job{len(self.scheduled)}"
        self.scheduled.append((job, milliseconds, callback))
        return job

    def after_cancel(self, job: str) -> None:
        self.cancelled.append(job)

    def fire(self, number: int) -> None:
        job, _, callback = self.scheduled[number]
        self.fired.append(job)
        callback()

    def live(self) -> list[str]:
        """The timers that are still going to fire."""
        gone = self.cancelled + self.fired
        return [job for job, _, _ in self.scheduled if job not in gone]


def bare_app() -> tuple[Any, FakeRoot, list[str]]:
    """An App without a window: the event queue, the timers and the error page are stand-ins."""
    gui = load_gui()
    app = gui.App.__new__(gui.App)
    app.root = FakeRoot()
    app.events = queue.Queue()
    app.exited = app.closing = app.polling = app._poll_busy = False
    app._poll_job = None
    app.services = None
    app.tray = None  # (no icon next to the clock)
    app.settings = types.SimpleNamespace(tray=False)
    app.ui = types.SimpleNamespace(_write_log=lambda text: None)
    errors: list[str] = []
    app.on_error = lambda message, details="": errors.append(message)
    app.show = lambda name: None
    return app, app.root, errors


# -- the window's event loop -----------------------------------------------------------------------


def test_the_pump_keeps_running_when_a_handler_fails() -> None:
    app, root, errors = bare_app()
    seen: list[str] = []

    def broken() -> None:
        raise RuntimeError("handler broke")

    app.on_broken = broken
    app.on_note = seen.append
    app.events.put(("broken",))
    app.events.put(("note", "after the failure"))
    app._pump()
    assert seen == ["after the failure"]  # what came behind the failing event is still handled
    assert len(errors) == 1 and "handler broke" in errors[0]  # and the error page is shown
    assert [callback for _, _, callback in root.scheduled] == [app._pump]  # and it runs again

    app.events.put(("note", "later"))
    root.scheduled[0][2]()
    assert seen == ["after the failure", "later"]


def test_the_pump_ends_with_the_window() -> None:
    app, root, _ = bare_app()
    seen: list[str] = []
    app.on_exit = lambda: setattr(app, "exited", True)
    app.on_note = seen.append
    app.events.put(("exit",))
    app.events.put(("note", "too late: the window is gone"))
    app._pump()
    assert seen == [] and root.scheduled == []


def test_a_restart_does_not_start_a_second_polling_chain() -> None:
    app, root, _ = bare_app()
    app.on_running()
    assert root.live() == ["job0"]
    root.fire(0)  # the timer fires: the chain goes on with one new timer
    assert root.live() == ["job1"]
    app.on_running()  # "Save and restart" ended with the status page again
    assert root.live() == ["job2"]  # the old chain was cancelled, not left running beside it


def test_the_window_does_not_ask_what_the_setup_page_asked() -> None:
    events: queue.Queue[tuple[Any, ...]] = queue.Queue()
    ui = load_gui().GuiUi(events)
    assert ui.ask("Continue?", default=True) is True
    assert ui.ask("Install Tesseract?", default=False) is False
    assert events.empty()  # no dialog was queued


def test_the_light_theme_is_read_from_the_registry_path_windows_knows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []

    class Handle:
        def __enter__(self) -> Handle:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def open_key(hive: object, key: str) -> Handle:
        opened.append(key)
        return Handle()

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(), OpenKey=open_key, QueryValueEx=lambda handle, name: (1, 4)
    )
    gui = load_gui()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", fake)
    assert gui.system_uses_light_theme() is True
    assert opened == [r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"]


@pytest.mark.skipif(not has_display(), reason="needs tkinter and a display")
def test_try_again_after_a_settings_error_reads_the_settings_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gui = load_gui()
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)  # no network
    root = tmp_path / "Starlight"
    root.mkdir()
    app = gui.App(L, L.build_parser().parse_args(["--root", str(root)]))
    try:
        assert "No Starlight folder" in app.fatal
        app.try_again()  # still no Starlight folder: the error stays, nothing is installed
        assert "No Starlight folder" in app.fatal and app.page == "error"

        (root / "pyproject.toml").write_text('[project]\nname = "starlight"\n', encoding="utf-8")
        (root / "config").mkdir()
        saved = root / "config" / "launcher.json"
        saved.write_text("{ not json", encoding="utf-8")
        app.try_again()
        assert "not valid JSON" in app.fatal and app.page == "error"

        saved.write_text(json.dumps({"variant": "cpu", "voice": True}), encoding="utf-8")
        app.try_again()  # the fixed file is used (the first run: the setup page comes first)
        assert app.fatal == "" and app.settings.root == root.resolve()
        assert app.settings.variant == "cpu" and app.settings.assume_yes
        assert app.page == "setup" and app.voice_var.get() is True and not app.gpu_var.get()
        assert app.ui.log == root.resolve() / "logs" / "launcher.log"
    finally:
        app.root.destroy()


# -- the launcher ----------------------------------------------------------------------------------


def test_a_launcher_json_saved_with_a_byte_order_mark_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows Notepad can save UTF-8 with a BOM; the file is meant to be edited by hand."""
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "cache"))
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "starlight"\n', encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "launcher.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"port": 9000}).encode()
    )
    settings = L.load_settings(L.build_parser().parse_args(["--root", str(tmp_path)]))
    assert settings.port == 9000


def test_the_speech_model_file_name_drops_a_query_from_the_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Hugging Face "copy link" ends in ?download=true: not part of a file name."""
    downloads: list[Path] = []
    monkeypatch.setattr(L, "voice_downloads", lambda: True)
    monkeypatch.setattr(L, "download", lambda url, dest, ui, **kw: downloads.append(dest))
    settings = L.Settings(root=tmp_path, voice=True)
    settings.whisper_model_url = "https://example.invalid/ggml-small.bin?download=true"
    for program in (tmp_path / "tools" / "whisper" / L.exe("whisper-server"),
                    tmp_path / "tools" / "piper" / L.exe("piper")):  # fmt: skip
        program.parent.mkdir(parents=True)
        program.write_text("program", encoding="utf-8")
    for voice in L.VOICES:
        for suffix in (".onnx", ".onnx.json"):
            target = tmp_path / "tools" / "piper" / "voices" / f"{voice.split('/')[-1]}{suffix}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("voice", encoding="utf-8")
    L.ensure_voice(settings, QuietUi())
    assert downloads == [tmp_path / "tools" / "whisper" / "models" / "ggml-small.bin"]


def test_a_failed_tesseract_install_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        ran.append(command)
        return subprocess.CompletedProcess(command, code)

    monkeypatch.setattr(L.subprocess, "run", fake_run)
    code = 1223  # the user said no to the administrator question
    ui = QuietUi()
    L.install_tesseract(ui)
    assert ran and ran[0][:2] == ["winget", "install"]
    assert any("1223" in line and "not installed" in line for line in ui.lines)

    code = 0
    ui = QuietUi()
    L.install_tesseract(ui)
    assert ui.lines == []


def test_the_chat_and_statistics_urls_follow_the_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    assert L.chat_url() == f"http://127.0.0.1:{L.WEB_PORT}"
    assert L.viewer_url() == f"http://127.0.0.1:{L.VIEWER_PORT}"
    monkeypatch.setattr(L, "WEB_PORT", 1234)
    monkeypatch.setattr(L, "VIEWER_PORT", 5678)
    assert L.chat_url() == "http://127.0.0.1:1234"
    assert L.viewer_url() == "http://127.0.0.1:5678"
