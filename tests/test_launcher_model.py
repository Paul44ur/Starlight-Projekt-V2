"""The model chosen on the chat's Settings page, as the launcher sees it: the ``model_file`` key
in config/launcher.json, the heartbeat and the restart request (starlight/core/modelchoice.py
is the web side of the two files)."""

from __future__ import annotations

import json
import time
import types
from pathlib import Path

import pytest

from tests.test_launcher import L
from tests.test_launcher_tray import needs_display, run_events, with_tray
from tests.test_launcher_window import parse


def project(tmp_path: Path, saved: dict[str, object] | str | None = None) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "starlight"\n', encoding="utf-8")
    if saved is not None:
        (tmp_path / "config").mkdir()
        text = saved if isinstance(saved, str) else json.dumps(saved)
        (tmp_path / "config" / "launcher.json").write_text(text, encoding="utf-8")
    return tmp_path


def settings_of(root: Path, *argv: str) -> object:
    return L.load_settings(parse("--root", str(root), *argv))


def test_an_old_launcher_json_behaves_exactly_as_before(tmp_path: Path) -> None:
    root = project(tmp_path, {"model": "qwen14b-uncensored", "context": 16384})
    s = settings_of(root)
    assert s.model_choice == ""  # type: ignore[attr-defined]
    assert s.chosen_in_use is False  # type: ignore[attr-defined]
    assert s.model_url == L.preset_url("qwen14b-uncensored")  # type: ignore[attr-defined]
    assert s.model_file == root / "models" / L._file_name(L.preset_url("qwen14b-uncensored"))  # type: ignore[attr-defined]
    assert s.context == 16384  # type: ignore[attr-defined]
    (tmp_path / "x").mkdir()
    none = settings_of(project(tmp_path / "x"))
    assert none.model_file.name == L._file_name(L.DEFAULT_MODEL_URL)  # type: ignore[attr-defined]


def test_a_chosen_file_that_is_there_is_the_model(tmp_path: Path) -> None:
    root = project(tmp_path, {"model": "qwen14b-uncensored", "model_file": "mine.gguf"})
    (root / "models").mkdir()
    (root / "models" / "mine.gguf").write_bytes(b"x")
    s = settings_of(root)
    assert s.model_file == root / "models" / "mine.gguf"  # type: ignore[attr-defined]
    assert s.chosen_in_use is True  # type: ignore[attr-defined]
    assert L.model_label(s) == "mine"
    assert L.disk_report(s)[0]["in_use"] is True


def test_a_chosen_file_that_is_missing_falls_back_to_the_preset(tmp_path: Path) -> None:
    root = project(tmp_path, {"model": "qwen14b-uncensored", "model_file": "gone.gguf"})
    s = settings_of(root)
    assert s.chosen_in_use is False  # type: ignore[attr-defined]
    assert s.model_file.name == L._file_name(L.preset_url("qwen14b-uncensored"))  # type: ignore[attr-defined]
    assert L.model_label(s) != "gone"


@pytest.mark.parametrize("name", ["../evil.gguf", "sub/x.gguf", "x\\y.gguf", "notes.txt", 5, ""])
def test_a_chosen_name_that_is_not_a_plain_gguf_name_is_ignored(
    tmp_path: Path, name: object
) -> None:
    s = settings_of(project(tmp_path, {"model_file": name}))
    assert s.model_choice == ""  # type: ignore[attr-defined]


def test_a_model_on_the_command_line_wins_over_the_chosen_file(tmp_path: Path) -> None:
    root = project(tmp_path, {"model_file": "mine.gguf"})
    (root / "models").mkdir()
    (root / "models" / "mine.gguf").write_bytes(b"x")
    s = settings_of(root, "--model", "qwen14b-uncensored")
    assert s.model_file.name == L._file_name(L.preset_url("qwen14b-uncensored"))  # type: ignore[attr-defined]


def test_the_launcher_config_names_the_chosen_file(tmp_path: Path) -> None:
    root = project(tmp_path, {"model_file": "mine.gguf", "model_server": "llamacpp"})
    (root / "models").mkdir()
    (root / "models" / "mine.gguf").write_bytes(b"x")
    s = settings_of(root)
    s.context = 8192  # type: ignore[attr-defined]
    server = root / "llama-server"
    written = json.loads(
        L.write_launcher_config(s, server, s.model_file).read_text().split("\n", 2)[2]  # type: ignore[attr-defined]
    )
    assert written["model"]["server"]["model_path"] == str(root / "models" / "mine.gguf")
    assert written["model"]["options"]["model"] == "mine"


def test_the_heartbeat_and_the_restart_request_files(tmp_path: Path) -> None:
    assert L.restart_requested(tmp_path) is False
    L.write_alive(tmp_path)
    data = json.loads((tmp_path / "data" / "launcher.alive.json").read_text())
    assert abs(data["time"] - time.time()) < 5
    (tmp_path / "data" / "launcher.restart.json").write_text("{}")
    assert L.restart_requested(tmp_path) is True
    L.clear_restart_request(tmp_path)
    L.clear_restart_request(tmp_path)  # (twice: nothing to remove is fine)
    assert L.restart_requested(tmp_path) is False
    L.clear_alive(tmp_path)
    assert not (tmp_path / "data" / "launcher.alive.json").exists()


@needs_display
def test_the_window_restarts_when_the_settings_page_asks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, _backend = with_tray(monkeypatch, tmp_path)
    app.services = types.SimpleNamespace(poll=lambda: None)
    restarts: list[str] = []
    reread: list[int] = []
    monkeypatch.setattr(app, "_stop_then_begin", lambda: restarts.append("restart"))
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "mine.gguf").write_bytes(b"x")
    chosen = L.Settings(root=tmp_path, model_choice="mine.gguf", context=16384)
    monkeypatch.setattr(L, "load_settings", lambda args: (reread.append(1), chosen)[1])
    try:
        app.on_running()
        app._poll_once()
        assert (tmp_path / "data" / "launcher.alive.json").is_file()  # the heartbeat
        run_events(app)
        assert restarts == []
        (tmp_path / "data" / "launcher.restart.json").write_text("{}")
        app._poll_once()
        run_events(app)
        time.sleep(0.2)  # (the restart runs in a thread)
        assert restarts == ["restart"]
        assert reread == [1]  # the settings were read again first
        assert not (tmp_path / "data" / "launcher.restart.json").exists()
        assert app.page == "progress"
        # the window's own Settings page shows what was chosen, so saving there keeps it
        assert app.context_var.get() == app.context_names[16384]
        assert app.model_var.get() == "custom"
    finally:
        app.services = None
        app.on_exit()


@needs_display
def test_a_request_while_not_running_is_dropped_not_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, _backend = with_tray(monkeypatch, tmp_path)
    restarts: list[str] = []
    monkeypatch.setattr(app, "_stop_then_begin", lambda: restarts.append("restart"))
    try:
        (tmp_path / "data").mkdir(exist_ok=True)
        (tmp_path / "data" / "launcher.restart.json").write_text("{}")
        app.on_restart_request()  # (no services: nothing is running)
        assert restarts == []
        assert not (tmp_path / "data" / "launcher.restart.json").exists()
    finally:
        app.on_exit()


@needs_display
def test_saving_the_settings_keeps_the_file_chosen_on_the_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, _backend = with_tray(monkeypatch, tmp_path)
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "mine.gguf").write_bytes(b"x")
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "launcher.json").write_text(
        json.dumps({"model": "qwen14b-uncensored", "model_file": "mine.gguf"})
    )
    app.settings.model_choice = "mine.gguf"
    app.pages["setup"].destroy()
    app._build_setup()  # (as the window does after a restart request)
    try:
        app.open_setup(settings_mode=True)
        assert app._current_model_key() == "custom"
        app.begin = lambda: None  # type: ignore[method-assign]
        app.on_setup_go()
        saved = json.loads((tmp_path / "config" / "launcher.json").read_text())
        assert saved["model_file"] == "mine.gguf"  # (untouched: still the chosen file)
    finally:
        app.on_exit()
