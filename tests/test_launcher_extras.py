"""The launcher's extras: how much context fits the graphics card (read from the model
file), answer styles, Ollama and a model server on another PC, a models folder of your own,
pausing a download, starting with Windows, Hugging Face search and the disk overview."""

from __future__ import annotations

import json
import os
import struct
import sys
import threading
import time
import types
from pathlib import Path
from typing import Any

import pytest

from tests.test_launcher import Files, L, QuietUi, server  # noqa: F401  (a fixture)
from tests.test_lmstudio import settings_for, starlight_root

GB = 1024**3


def gguf(path: Path, entries: list[tuple[str, int, Any]], size: int = 0) -> Path:
    """A GGUF file with these (key, type, value) entries in its header (and padded to
    ``size`` bytes, as a sparse file, to stand for the weights)."""

    def text(value: str) -> bytes:
        data = value.encode()
        return struct.pack("<Q", len(data)) + data

    body = b"GGUF" + struct.pack("<IQQ", 3, 0, len(entries))
    for key, kind, value in entries:
        body += text(key) + struct.pack("<I", kind)
        if kind == 8:
            body += text(value)
        elif kind == 9:
            item, items = value
            body += struct.pack("<IQ", item, len(items))
            for one in items:
                body += text(one) if item == 8 else struct.pack("<" + L.GGUF_TYPES[item], one)
        else:
            body += struct.pack("<" + L.GGUF_TYPES[kind], value)
    path.write_bytes(body)
    if size:
        with path.open("r+b") as handle:
            handle.truncate(size)
    return path


QWEN_LIKE = [
    ("general.architecture", 8, "qwen3"),
    ("general.name", 8, "A test model"),
    ("qwen3.context_length", 4, 131072),
    ("qwen3.block_count", 4, 40),
    ("qwen3.embedding_length", 4, 5120),
    ("qwen3.attention.head_count", 4, 40),
    ("qwen3.attention.head_count_kv", 4, 8),
    ("tokenizer.ggml.tokens", 9, (8, ["a", "b", "<|im_end|>"])),
]


def test_the_header_says_how_big_one_token_of_context_is(tmp_path: Path) -> None:
    model = gguf(tmp_path / "m.gguf", QWEN_LIKE)
    info = L.gguf_info(model)
    assert info["architecture"] == "qwen3" and info["block_count"] == 40
    assert info["attention.head_count_kv"] == 8 and info["context_length"] == 131072
    # keys and values, 40 layers, 8 heads of 128 (5120 / 40), 8-bit cache
    assert L.kv_bytes_per_token(info) == int(2 * 40 * 8 * 128 * 34 / 32)
    assert L.kv_bytes_per_token(info, "f16") == 2 * 40 * 8 * 128 * 2
    per_layer = [*QWEN_LIKE[:-2], ("qwen3.attention.head_count_kv", 9, (4, [8, 8, 4]))]
    assert L.gguf_info(gguf(tmp_path / "p.gguf", per_layer))["attention.head_count_kv"] == [8, 8, 4]
    (tmp_path / "x.gguf").write_bytes(b"not a model")
    assert L.gguf_info(tmp_path / "x.gguf") == {} and L.kv_bytes_per_token({}) == 0


def test_the_context_that_fits(tmp_path: Path) -> None:
    model = gguf(tmp_path / "m.gguf", QWEN_LIKE, size=9 * GB)
    # 16 GB: 15.2 usable, 9 for the model, 0.8 on top: about 5.4 GB for ~87 KB per token
    assert L.context_fit(model, 16 * GB) == 49152
    assert L.context_fit(model, 32 * GB) == 131072
    assert L.context_fit(model, 10 * GB) == 8192  # barely: the smallest step
    short = gguf(tmp_path / "s.gguf", [*QWEN_LIKE[:2], ("qwen3.context_length", 4, 40960),
                                       *QWEN_LIKE[3:]], size=9 * GB)  # fmt: skip
    assert L.context_fit(short, 32 * GB) == 32768  # never more than it was trained for
    assert L.context_fit(model, 0) == 0


def test_auto_context_uses_the_graphics_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = starlight_root(tmp_path, {"context": 0})
    settings = settings_for(root, monkeypatch)
    model = gguf(tmp_path / "m.gguf", QWEN_LIKE, size=9 * GB)
    monkeypatch.setattr(L, "detect_gpu", lambda: L.Gpu("Radeon RX 9060 XT", 16 * GB))
    assert settings.context == 0 and L.choose_context(settings, model) == 49152
    monkeypatch.setattr(L, "detect_gpu", lambda: None)
    assert L.choose_context(settings, model) == L.DEFAULT_CONTEXT
    settings.context = 20000
    assert L.choose_context(settings, model) == 20000  # your choice stays


# -- model servers and styles -------------------------------------------------------------------


def written(settings: Any, binary: Path | None = None, model: Path | None = None) -> Any:
    path = L.write_launcher_config(settings, binary, model)
    text = path.read_text(encoding="utf-8")
    return json.loads(text[text.index("{") :])["model"]


def test_ollama_and_a_server_on_another_pc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = starlight_root(tmp_path)
    ollama = settings_for(root, monkeypatch, "--ollama-model", "qwen3:14b", "--context", "16384")
    assert ollama.uses_ollama and not ollama.runs_llama
    assert written(ollama) == {
        "provider": "ollama",
        "options": {"base_url": "http://127.0.0.1:11434", "model": "qwen3:14b", "num_ctx": 16384},
        "context_window": 16384,
    }
    remote = settings_for(root, monkeypatch, "--remote-url", "http://192.168.1.20:8080/v1/",
                          "--style", "creative")  # fmt: skip
    assert remote.uses_remote and remote.remote_url == "http://192.168.1.20:8080/v1"
    assert written(remote) == {
        "provider": "openai_compat",
        "options": {
            "base_url": "http://192.168.1.20:8080/v1",
            "model": "local",
            "temperature": 0.8,
            "extra": {"top_p": 0.95, "top_k": 40},
        },
        "context_window": L.DEFAULT_CONTEXT,
    }


def test_wrong_settings_are_explained(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = starlight_root(tmp_path, {"style": "wild"})
    with pytest.raises(L.LauncherError, match="precise, balanced, creative"):
        settings_for(root, monkeypatch)
    root = starlight_root(tmp_path / "b", {"model_server": "remote", "remote_url": "pc:8080"})
    with pytest.raises(L.LauncherError, match="the server's address"):
        settings_for(root, monkeypatch)


def test_a_precise_style_and_no_thinking_with_llama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = starlight_root(tmp_path, {"style": "precise", "think": False, "context": 8192})
    settings = settings_for(root, monkeypatch)
    model = written(settings, tmp_path / "llama-server", tmp_path / "qwen.gguf")
    assert model["options"]["temperature"] == 0.1
    assert model["options"]["extra"]["top_k"] == 20
    assert model["options"]["extra"]["chat_template_kwargs"]["enable_thinking"] is False
    assert model["context_window"] == 8192


def test_the_status_of_ollama_and_another_pc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = starlight_root(tmp_path)
    settings = settings_for(root, monkeypatch, "--ollama-model", "qwen3:14b")
    answers: dict[str, Any] = {}
    monkeypatch.setattr(L, "fetch_json", lambda url, **_: answers.get(url))
    assert L.other_server_state(settings) == ("waiting", L.OLLAMA_HINT)
    answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "llama3:8b"}]}
    state, detail = L.other_server_state(settings)
    assert state == "waiting" and "ollama pull qwen3:14b" in detail
    answers["http://127.0.0.1:11434/api/tags"] = {"models": [{"name": "qwen3:14b"}]}
    assert L.other_server_state(settings) == ("ready", "qwen3:14b (Ollama)")
    remote = settings_for(root, monkeypatch, "--remote-url", "http://192.168.1.20:8080/v1")
    assert L.other_server_state(remote)[0] == "waiting"
    answers["http://192.168.1.20:8080/v1/models"] = {"data": [{"id": "qwen3-14b"}]}
    assert L.other_server_state(remote) == ("ready", "qwen3-14b (at 192.168.1.20:8080)")


def test_a_models_folder_of_your_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    elsewhere = tmp_path / "D" / "Models"
    root = starlight_root(tmp_path, {"models_dir": str(elsewhere)})
    settings = settings_for(root, monkeypatch)
    assert settings.models == elsewhere and settings.model_file.parent == elsewhere


# -- pausing a download -------------------------------------------------------------------------


def test_a_paused_download_goes_on_where_it_stopped(server: str, tmp_path: Path) -> None:  # noqa: F811
    data = os.urandom(5 * 1024 * 1024 + 7)
    Files.files["/big.gguf"] = data
    ui = QuietUi()
    paused = L.DOWNLOADS_PAUSED
    paused.set()  # paused before the first megabyte is even written
    try:
        later = threading.Timer(0.6, paused.clear)
        later.start()
        started = time.monotonic()
        L.download(f"{server}/big.gguf", tmp_path / "big.gguf", ui)
    finally:
        paused.clear()
    assert (tmp_path / "big.gguf").read_bytes() == data
    assert time.monotonic() - started >= 0.5
    assert any("paused at" in line for line in ui.lines)
    assert any(line.strip() == "resuming the download ..." for line in ui.lines)
    ranges = [r for _, path, r in Files.requests if path == "/big.gguf"]
    assert ranges[0] is None and ranges[1] is not None and ranges[1].startswith("bytes=")


# -- starting with Windows ----------------------------------------------------------------------


def test_starting_with_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    values: dict[str, str] = {}

    class Key:
        def __enter__(self) -> Key:
            return self

        def __exit__(self, *args: Any) -> None:
            return None

    def query(key: Any, name: str) -> Any:
        if name not in values:
            raise FileNotFoundError(name)
        return values[name], 1

    def delete(key: Any, name: str) -> None:
        if name not in values:
            raise FileNotFoundError(name)
        del values[name]

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=1,
        OpenKey=lambda *args: Key(),
        QueryValueEx=query,
        SetValueEx=lambda key, name, reserved, kind, value: values.__setitem__(name, value),
        DeleteValue=delete,
    )  # fmt: skip
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(L.sys, "platform", "win32")
    assert not L.autostart_enabled()
    L.set_autostart(True, '"C:\\Starlight\\Starlight.exe"')
    assert L.autostart_enabled() and values["Starlight"] == '"C:\\Starlight\\Starlight.exe"'
    L.set_autostart(False, "")
    L.set_autostart(False, "")  # (already off: fine)
    assert not L.autostart_enabled()
    monkeypatch.setattr(L.sys, "platform", "linux")
    with pytest.raises(L.LauncherError, match="Windows only"):
        L.set_autostart(True, "x")


# -- Hugging Face ---------------------------------------------------------------------------------


def test_hugging_face_search_and_its_files(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str] = []

    def fake(url: str, timeout: float = 20) -> Any:
        asked.append(url)
        if "/api/models?" in url:
            return [{"id": "Qwen/Qwen3-8B-GGUF", "downloads": 1000, "likes": 50}, {"no": "id"}]
        return [
            {"type": "file", "path": "Qwen3-8B-Q8_0.gguf", "size": 1, "lfs": {"size": 8 * GB}},
            {"type": "file", "path": "Qwen3-8B-Q4_K_M.gguf", "lfs": {"size": 5 * GB}},
            {"type": "file", "path": "mmproj-F16.gguf", "lfs": {"size": GB}},
            {"type": "file", "path": "big/Qwen3-8B-BF16-00001-of-00002.gguf", "size": GB},
            {"type": "file", "path": "README.md", "size": 100},
        ]

    monkeypatch.setattr(L, "_get_json", fake)
    found = L.search_hf("qwen3 8b")
    assert found == [{"repo": "Qwen/Qwen3-8B-GGUF", "downloads": 1000, "likes": 50}]
    assert "search=qwen3%208b" in asked[0] and "filter=gguf" in asked[0]
    files = L.hf_files("Qwen/Qwen3-8B-GGUF")
    assert [f["file"] for f in files] == ["Qwen3-8B-Q4_K_M.gguf", "Qwen3-8B-Q8_0.gguf"]
    assert files[0]["url"] == (
        "https://huggingface.co/Qwen/Qwen3-8B-GGUF/resolve/main/Qwen3-8B-Q4_K_M.gguf"
    )
    assert L.search_hf("  ") == []
    with pytest.raises(L.LauncherError):
        L.hf_files("../etc")


# -- disk space -----------------------------------------------------------------------------------


def test_the_disk_overview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = starlight_root(tmp_path)
    settings = settings_for(root, monkeypatch)
    settings.models.mkdir(parents=True)
    (settings.models / settings.model_file.name).write_bytes(b"x" * 100)
    (settings.models / "old.gguf").write_bytes(b"x" * 50)
    (root / "logs").mkdir()
    (root / "logs" / "a.log").write_bytes(b"x" * 10)
    report = {item["name"]: item for item in L.disk_report(settings)}
    assert report[settings.model_file.name]["in_use"] and not report["old.gguf"]["in_use"]
    assert report["old.gguf"]["bytes"] == 50 and report["logs"]["bytes"] == 10
