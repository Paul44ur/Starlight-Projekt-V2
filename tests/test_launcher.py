"""The launcher behind Starlight.exe: downloads, checksums, unpacking, the config it
writes, and (on Linux/macOS) a full setup-and-start against local stand-ins.
The Windows build is tested end to end on a clean machine in .github/workflows/launcher.yml.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from starlight.core import Paths, load_config
from starlight.models.serve import llama_server_command
from tests.conftest import REPO


def load_launcher() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "starlight_launcher", REPO / "launcher" / "starlight_launcher.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["starlight_launcher"] = module
    spec.loader.exec_module(module)
    return module


L = load_launcher()


class Files(BaseHTTPRequestHandler):
    """Serves ``files`` with HTTP range support; ``ignore_range`` paths always send all."""

    files: dict[str, bytes] = {}  # noqa: RUF012
    ignore_range: set[str] = set()  # noqa: RUF012
    redirects: dict[str, tuple[str, dict[str, str]]] = {}  # noqa: RUF012
    requests: list[tuple[str, str, str | None]] = []  # noqa: RUF012

    def log_message(self, *args: Any) -> None:
        pass

    def _serve(self, body: bool) -> None:
        type(self).requests.append((self.command, self.path, self.headers.get("Range")))
        if self.path in self.redirects:
            target, extra = self.redirects[self.path]
            self.send_response(302)
            self.send_header("Location", target)
            for key, value in extra.items():
                self.send_header(key, value)
            self.end_headers()
            return
        data = self.files.get(self.path)
        if data is None:
            self.send_response(404)
            self.end_headers()
            return
        start = 0
        header = self.headers.get("Range")
        if header and self.path not in self.ignore_range:
            start = int(header.removeprefix("bytes=").split("-")[0])
            if start >= len(data):
                self.send_response(416)
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        if body:
            self.wfile.write(data[start:])

    def do_GET(self) -> None:
        self._serve(True)

    def do_HEAD(self) -> None:
        self._serve(False)


@pytest.fixture
def server() -> Iterator[str]:
    Files.files, Files.ignore_range, Files.redirects, Files.requests = {}, set(), {}, []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Files)
    threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


class QuietUi(L.Ui):  # type: ignore[misc,name-defined]
    def __init__(self) -> None:
        super().__init__(assume_yes=True)
        self.lines: list[str] = []

    def say(self, text: str = "") -> None:
        self.lines.append(text)

    def progress(self, done: int, total: int | None, started: float) -> None:
        pass


def test_download_resumes_and_checks_the_checksum(server: str, tmp_path: Path) -> None:
    data = os.urandom(3 * 1024 * 1024 + 123)
    Files.files["/model.gguf"] = data
    dest = tmp_path / "model.gguf"
    (tmp_path / "model.gguf.part").write_bytes(data[:1_000_000])  # an interrupted download
    L.download(f"{server}/model.gguf", dest, QuietUi(), sha256=hashlib.sha256(data).hexdigest())
    assert dest.read_bytes() == data
    assert not (tmp_path / "model.gguf.part").exists()
    assert Files.requests[-1][2] == "bytes=1000000-"  # resumed, not restarted

    with pytest.raises(L.LauncherError, match="checksum mismatch"):
        L.download(f"{server}/model.gguf", tmp_path / "bad.gguf", QuietUi(), sha256="0" * 64)
    assert not (tmp_path / "bad.gguf.part").exists() and not (tmp_path / "bad.gguf").exists()


def test_download_restarts_when_the_server_ignores_ranges(server: str, tmp_path: Path) -> None:
    data = b"x" * 50_000
    Files.files["/f"] = data
    Files.ignore_range.add("/f")
    (tmp_path / "f.part").write_bytes(b"garbage")
    L.download(f"{server}/f", tmp_path / "f", QuietUi())
    assert (tmp_path / "f").read_bytes() == data


def test_download_reports_missing_files(server: str, tmp_path: Path) -> None:
    with pytest.raises(L.LauncherError, match="HTTP 404"):
        L.download(f"{server}/nope", tmp_path / "nope", QuietUi())


def test_probe_reads_hugging_face_size_and_checksum(server: str) -> None:
    sha = "ab" * 32
    Files.redirects["/resolve/model.gguf"] = (
        f"{server}/cdn/model.gguf",
        {"X-Linked-Size": "9000000000", "X-Linked-Etag": f'"{sha}"'},
    )
    remote = L.probe(f"{server}/resolve/model.gguf")
    assert remote.size == 9_000_000_000 and remote.sha256 == sha

    Files.files["/cdn/plain"] = b"12345"
    Files.redirects["/plain"] = (f"{server}/cdn/plain", {})
    assert L.probe(f"{server}/plain").size == 5


def test_a_model_link_that_no_longer_works_says_what_to_do(server: str, tmp_path: Path) -> None:
    with pytest.raises(L.MissingDownload, match=r"does not work \(HTTP 404\).*another model"):
        L.probe(f"{server}/moved.gguf")
    with pytest.raises(L.MissingDownload):
        L.download(f"{server}/moved.gguf", tmp_path / "moved.gguf", QuietUi())
    assert not (tmp_path / "moved.gguf").exists()


def test_every_preset_has_known_tags() -> None:
    known = {"thinks first", "quick", "code", "tools", "languages", "uncensored"}
    assert set(L.MODEL_TAGS) <= set(L.MODEL_PRESETS)
    assert all(set(tags) <= known and tags for tags in L.MODEL_TAGS.values())
    assert all("uncensored" in L.MODEL_TAGS[key] for key in L.MODIFIED)


def test_pick_llama_asset() -> None:
    release = {
        "assets": [
            {"name": "llama-b9000-bin-win-cuda-12.4-x64.zip", "browser_download_url": "u1"},
            {
                "name": "llama-b9000-bin-win-vulkan-x64.zip",
                "browser_download_url": "u2",
                "digest": "sha256:" + "cd" * 32,
            },
            {"name": "llama-b9000-bin-ubuntu-x64.tar.gz", "browser_download_url": "u3"},
            {"name": "llama-b9000-bin-ubuntu-vulkan-x64.zip", "browser_download_url": "u4"},
        ]
    }
    if os.name == "nt":
        assert L.pick_llama_asset(release, "vulkan") == ("u2", "cd" * 32)
    else:
        assert L.pick_llama_asset(release, "cpu") == ("u3", None)  # the new .tar.gz naming
        assert L.pick_llama_asset(release, "vulkan") == ("u4", None)  # the old .zip naming
    with pytest.raises(L.LauncherError, match="found: llama-b9000"):
        L.pick_llama_asset({"assets": release["assets"][:1]}, "vulkan")


def _settings(tmp_path: Path, release: str = "latest") -> Any:
    return L.Settings(root=tmp_path, variant="cpu", llama_release=release)


def test_resolve_llama_follows_nightly_tag(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """llama.cpp's "latest" release only holds nightly-tag.txt; the programs are in the
    b#### build release it names."""
    monkeypatch.setattr(L, "LLAMA_API", f"{server}/api")
    monkeypatch.setattr(L, "LLAMA_RELEASES", f"{server}/releases")
    name = f"llama-b7000-{L.llama_asset_names('cpu')[0]}"
    pointer = {"name": "nightly-tag.txt", "browser_download_url": f"{server}/files/tag.txt"}
    Files.files["/api/latest"] = json.dumps({"tag_name": "v1.0.0", "assets": [pointer]}).encode()
    Files.files["/files/tag.txt"] = b"b7000\n"
    Files.files["/api/tags/b7000"] = json.dumps(
        {"assets": [{"name": name, "browser_download_url": "u", "digest": "sha256:" + "e" * 64}]}
    ).encode()
    assert L.resolve_llama(_settings(tmp_path)) == ("u", "e" * 64)

    # A release that still has the programs itself needs no pointer.
    Files.files["/api/tags/b6000"] = json.dumps(
        {"assets": [{"name": name.replace("b7000", "b6000"), "browser_download_url": "v"}]}
    ).encode()
    assert L.resolve_llama(_settings(tmp_path, "b6000")) == ("v", None)

    # Neither programs nor a pointer: a clear message, not a crash.
    Files.files["/api/tags/v0.9.0"] = json.dumps({"assets": []}).encode()
    with pytest.raises(L.LauncherError, match=r"no llama\.cpp download"):
        L.resolve_llama(_settings(tmp_path, "v0.9.0"))


def test_resolve_llama_without_the_api(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub's API blocked or rate-limited: the release pages give the same answer."""
    monkeypatch.setattr(L, "LLAMA_API", f"{server}/api")  # nothing there: HTTP 404
    monkeypatch.setattr(L, "LLAMA_RELEASES", f"{server}/releases")
    suffix = L.llama_asset_names("cpu")[0]
    Files.files["/releases/latest/download/nightly-tag.txt"] = b"b7000\n"
    assert L.resolve_llama(_settings(tmp_path)) == (
        f"{server}/releases/download/b7000/llama-b7000-{suffix}",
        None,
    )
    # An older "latest" without nightly-tag.txt: its own tag, from the redirect.
    del Files.files["/releases/latest/download/nightly-tag.txt"]
    Files.redirects["/releases/latest"] = (f"{server}/releases/tag/b6000", {})
    Files.files["/releases/tag/b6000"] = b"<html>"
    assert L.resolve_llama(_settings(tmp_path)) == (
        f"{server}/releases/download/b6000/llama-b6000-{suffix}",
        None,
    )


def test_extract_member_flattens_folders(tmp_path: Path) -> None:
    archive = tmp_path / "llama.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("build/bin/llama-server", "#!/bin/sh\n")
        zf.writestr("build/bin/libggml.so", "lib")
        zf.writestr("README.md", "readme")
    binary = L.extract_member(archive, "llama-server", tmp_path / "out")
    assert binary == tmp_path / "out" / "llama-server"
    assert (tmp_path / "out" / "libggml.so").exists()

    tgz = tmp_path / "uv.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        payload = b"uv binary"
        info = tarfile.TarInfo("uv-x86_64-unknown-linux-gnu/uv")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    assert L.extract_member(tgz, "uv", tmp_path / "uv").read_bytes() == b"uv binary"

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../outside.txt", "x")
    with pytest.raises(L.LauncherError, match="unsafe path"):
        L.extract_member(evil, "outside.txt", tmp_path / "evil")


def test_launcher_config_is_loaded_by_starlight_and_yours_wins(tmp_path: Path) -> None:
    shutil.copytree(REPO / "config", tmp_path / "config")
    settings = L.Settings(root=tmp_path, context=16384, port=8099)
    server = Path(r"C:\Users\Paul O'Brien\Starlight\tools\llama.cpp\vulkan\llama-server.exe")
    model = Path(r"C:\Users\Paul O'Brien\Starlight\models\Qwen2.5-14B-Instruct-Q4_K_M.gguf")
    L.write_launcher_config(settings, server, model)
    config = load_config(Paths(tmp_path))
    assert config.model.provider == "llamacpp"
    assert config.model.server.binary == str(server)  # backslashes survive
    assert config.model.server.model_path == str(model)
    assert config.model.options["base_url"] == "http://127.0.0.1:8099/v1"
    assert config.model.context_window == 16384
    assert config.memory.top_k == 5  # the rest of starlight.yaml still applies

    (tmp_path / "config" / "starlight.local.yaml").write_text("model:\n  context_window: 8192\n")
    assert load_config(Paths(tmp_path)).model.context_window == 8192

    L.write_launcher_config(L.Settings(root=tmp_path, use_model=False), None, None)
    assert load_config(Paths(tmp_path)).model.provider == "echo"


def test_thinking_can_be_turned_off_for_the_models_that_think_first(tmp_path: Path) -> None:
    from starlight.core.model import create_provider

    shutil.copytree(REPO / "config", tmp_path / "config")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "starlight"\n')
    server, model = tmp_path / "llama-server.exe", tmp_path / "Qwen3-14B.Q4_K_M.gguf"
    L.write_launcher_config(L.Settings(root=tmp_path), server, model)
    provider = create_provider(load_config(Paths(tmp_path)).model)
    assert "chat_template_kwargs" not in provider.extra  # thinking is on by default

    (tmp_path / "config" / "launcher.json").write_text('{"think": false}')
    settings = L.load_settings(L.build_parser().parse_args(["--root", str(tmp_path)]))
    assert settings.think is False
    L.write_launcher_config(settings, server, model)
    provider = create_provider(load_config(Paths(tmp_path)).model)
    kwargs = provider.extra["chat_template_kwargs"]
    assert kwargs["enable_thinking"] is False and kwargs["reasoning_effort"] == "low"
    assert provider.extra["top_k"] == 20  # the sampling settings stay
    flags = ["--root", str(tmp_path), "--no-think"]
    (tmp_path / "config" / "launcher.json").write_text("{}")
    assert L.load_settings(L.build_parser().parse_args(flags)).think is False


def test_the_draft_model_is_gone_and_old_configs_still_load(tmp_path: Path) -> None:
    """The draft model (speculative decoding) was removed: no flags for llama-server, the
    old models/draft folder is deleted, and a config an older Starlight.exe wrote (with
    draft settings) still loads. Old shortcuts with --no-draft still start."""
    shutil.copytree(REPO / "config", tmp_path / "config")
    old = {
        "model": {
            "provider": "llamacpp",
            "server": {
                "binary": "llama-server",
                "model_path": "big.gguf",
                "draft_model_path": "models/draft/small.gguf",
                "draft_max": 16,
                "draft_min": 4,
                "gpu_layers_draft": 999,
            },
        }
    }
    (tmp_path / "config" / "starlight.launcher.yaml").write_text(json.dumps(old))
    config = load_config(Paths(tmp_path))
    assert config.model.server.model_path == "big.gguf"
    assert not any("draft" in part for part in llama_server_command(config.model))

    folder = tmp_path / "models" / "draft"
    folder.mkdir(parents=True)
    (folder / "small.gguf").write_bytes(b"x" * 1000)
    said: list[str] = []
    ui = L.Ui(assume_yes=True)
    ui.say = said.append  # type: ignore[method-assign]
    L.remove_old_draft(L.Settings(root=tmp_path), ui)
    assert not folder.exists() and "removed the old draft model" in said[0]
    L.remove_old_draft(L.Settings(root=tmp_path), ui)  # nothing left: nothing said
    assert len(said) == 1
    assert L.build_parser().parse_args(["--no-draft", "--draft-url", "x"]).no_draft


def test_find_root(tmp_path: Path) -> None:
    assert L.find_root(REPO / "launcher") == REPO
    with pytest.raises(L.LauncherError, match="No Starlight folder"):
        L.find_root(tmp_path)


def test_settings_from_file_and_flags(tmp_path: Path) -> None:
    root = tmp_path / "Starlight"
    (root / "config").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "starlight"\n')
    (root / "config" / "launcher.json").write_text(json.dumps({"variant": "cpu", "port": 9000}))
    args = L.build_parser().parse_args(["--root", str(root), "--port", "9100", "--no-viewer"])
    settings = L.load_settings(args)
    assert settings.variant == "cpu" and settings.port == 9100 and not settings.viewer


FAKE_LLAMA_SERVER = '''#!{python}
"""Stands in for llama-server: /health, and a chat endpoint that always says hello."""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_GET(self):
        self._json({{"status": "ok"}})
    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        self._json({{"choices": [{{"message": {{"content": "Hello from the GPU model"}}}}],
                    "timings": {{"predicted_per_second": 42.0}}}})

HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


@pytest.mark.skipif(os.name == "nt" or shutil.which("uv") is None, reason="POSIX with uv")
def test_full_setup_and_start(server: str, tmp_path: Path) -> None:
    """Everything the launcher does, against local stand-ins for the internet: uv comes
    from a local "GitHub", llama.cpp is a fake server that answers chat requests, the
    model is a small file. Starlight itself is set up for real (uv sync) and answers."""
    root = tmp_path / "Starlight"
    runtime = {".venv", ".git", "logs", "data", "memory", "tools", "models"}

    def skip(folder: str, names: list[str]) -> set[str]:  # runtime folders, top level only
        here = Path(folder) == REPO
        return {
            n
            for n in names
            if (here and n in runtime)
            or n == "__pycache__"
            or n.endswith((".local.yaml", ".launcher.yaml"))
        }

    shutil.copytree(REPO, root, ignore=skip)
    (root / "tools").mkdir()
    (root / "tools" / ".chromium-installed").write_text("skip in tests")  # no browser download

    uv_tar = io.BytesIO()
    with tarfile.open(fileobj=uv_tar, mode="w:gz") as tf:
        tf.add(str(shutil.which("uv")), arcname=f"{L.uv_asset().removesuffix('.tar.gz')}/uv")
    uv_bytes = uv_tar.getvalue()
    Files.files[f"/uv/{L.uv_asset()}"] = uv_bytes
    Files.files[f"/uv/{L.uv_asset()}.sha256"] = (
        f"{hashlib.sha256(uv_bytes).hexdigest()}  {L.uv_asset()}\n".encode()
    )
    llama_tar = io.BytesIO()
    with tarfile.open(fileobj=llama_tar, mode="w:gz") as tf:
        script = FAKE_LLAMA_SERVER.format(python=sys.executable).encode()
        info = tarfile.TarInfo("llama-b1/llama-server")
        info.size, info.mode = len(script), 0o755
        tf.addfile(info, io.BytesIO(script))
    llama_name = f"llama-b1-{L.llama_asset_names('cpu')[0]}"
    Files.files["/llama/latest"] = json.dumps(  # like llama.cpp today: only a pointer
        {"assets": [{"name": "nightly-tag.txt", "browser_download_url": f"{server}/llama/tag.txt"}]}
    ).encode()
    Files.files["/llama/tag.txt"] = b"b1\n"
    Files.files["/llama/tags/b1"] = json.dumps(
        {"assets": [{"name": llama_name, "browser_download_url": f"{server}/llama/{llama_name}"}]}
    ).encode()
    Files.files[f"/llama/{llama_name}"] = llama_tar.getvalue()
    model = os.urandom(200_000)
    Files.files["/model/tiny.gguf"] = model
    Files.redirects["/hf/tiny.gguf"] = (
        f"{server}/model/tiny.gguf",
        {"X-Linked-Size": str(len(model)), "X-Linked-Etag": hashlib.sha256(model).hexdigest()},
    )

    port = 18000 + os.getpid() % 1000
    code = f"""
import sys
sys.path.insert(0, {str(REPO / "launcher")!r})
import starlight_launcher as L
L.UV_DOWNLOAD = {server + "/uv"!r}
L.LLAMA_API = {server + "/llama"!r}
sys.exit(L.main(["--root", {str(root)!r}, "--yes", "--variant", "cpu", "--no-viewer",
                 "--port", "{port}", "--model-url", {server + "/hf/tiny.gguf"!r},
                 "--ask", "Say hello"]))
"""
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=600, env=env
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-3000:]
    assert "Hello from the GPU model" in output
    assert (root / "tools" / "uv" / "uv").exists()
    assert (root / "models" / "tiny.gguf").read_bytes() == model
    assert not (root / "models" / "draft").exists()
    config = load_config(Paths(root))
    assert config.model.provider == "llamacpp"
    assert config.model.server.binary == str(root / "tools" / "llama.cpp" / "cpu" / "llama-server")
    assert not L.port_in_use(port)  # the model server was stopped again


def test_load_settings_points_uv_cache_at_the_install_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """uv's cache goes inside the Starlight folder, so it shares a filesystem with .venv and
    uv can hardlink instead of copying (no 'Failed to hardlink files' warning). A user-set
    UV_CACHE_DIR is left alone."""
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "starlight"\n', encoding="utf-8")
    args = L.build_parser().parse_args(["--root", str(tmp_path)])

    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    settings = L.load_settings(args)
    assert os.environ["UV_CACHE_DIR"] == str(settings.root / ".uv-cache")

    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "chosen"))
    L.load_settings(args)
    assert os.environ["UV_CACHE_DIR"] == str(tmp_path / "chosen")  # user's choice is respected


def test_voice_is_installed_only_when_asked(
    server: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def archive(name: str) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(f"Release/{L.exe(name)}", "program")
            zf.writestr("Release/helper.dll", "library")
        return buffer.getvalue()

    Files.files["/whisper.zip"] = archive("whisper-server")
    Files.files["/ggml-small.bin"] = b"model"
    Files.files["/piper.zip"] = archive("piper")
    for voice in L.VOICES:
        Files.files[f"/voices/{voice}.onnx"] = b"onnx"
        Files.files[f"/voices/{voice}.onnx.json"] = b"{}"
    monkeypatch.setattr(L, "voice_downloads", lambda: True)
    monkeypatch.setattr(L, "PIPER_VOICES", f"{server}/voices")
    settings = L.Settings(root=tmp_path)
    settings.whisper_url = f"{server}/whisper.zip"
    settings.whisper_model_url = f"{server}/ggml-small.bin"
    settings.piper_url = f"{server}/piper.zip"

    L.ensure_voice(settings, QuietUi())  # not asked for: nothing happens
    assert not (tmp_path / "tools").exists()

    settings.voice = True
    L.ensure_voice(settings, QuietUi())
    assert (tmp_path / "tools" / "whisper" / L.exe("whisper-server")).is_file()
    assert (tmp_path / "tools" / "whisper" / "helper.dll").is_file()
    assert (tmp_path / "tools" / "whisper" / "models" / "ggml-small.bin").read_bytes() == b"model"
    assert (tmp_path / "tools" / "piper" / L.exe("piper")).is_file()
    voices = sorted(p.name for p in (tmp_path / "tools" / "piper" / "voices").iterdir())
    assert voices == [
        "de_DE-thorsten-medium.onnx",
        "de_DE-thorsten-medium.onnx.json",
        "en_US-lessac-medium.onnx",
        "en_US-lessac-medium.onnx.json",
    ]

    ui = QuietUi()
    settings.piper_url = f"{server}/missing.zip"
    settings.update = True
    L.ensure_voice(settings, ui)  # a failed download is reported, not fatal
    assert any("works without it" in line for line in ui.lines)
    assert L.build_parser().parse_args(["--voice"]).voice is True


def test_model_presets_from_file_and_flags(tmp_path: Path) -> None:
    """--model NAME (or "model" in launcher.json) picks a ready-made model; a model_url wins
    over a preset, and the default stays the uncensored 14B."""
    root = tmp_path / "Starlight"
    (root / "config").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "starlight"\n')
    saved = root / "config" / "launcher.json"

    def url(*flags: str) -> str:
        return L.load_settings(L.build_parser().parse_args(["--root", str(root), *flags])).model_url

    assert url() == L.DEFAULT_MODEL_URL == L.MODEL_PRESETS["qwen14b-uncensored"]
    saved.write_text(json.dumps({"model": "QWEN7B"}))
    assert url() == L.MODEL_PRESETS["qwen7b"]  # names are not case-sensitive in the file
    assert url("--model", "qwen14b") == L.MODEL_PRESETS["qwen14b"]  # the flag beats the file
    assert url("--model", "qwen14b", "--model-url", "https://x/m.gguf") == "https://x/m.gguf"
    saved.write_text(json.dumps({"model": "qwen7b", "model_url": "https://x/own.gguf"}))
    assert url() == "https://x/own.gguf"
    saved.write_text(json.dumps({"model": "llama-9000"}))
    with pytest.raises(L.LauncherError, match=r"unknown model 'llama-9000'.*qwen7b"):
        url()
    saved.write_text("[1, 2]")
    with pytest.raises(L.LauncherError, match="not valid JSON"):
        url()
    with pytest.raises(SystemExit):  # an unknown name on the command line is refused
        L.build_parser().parse_args(["--model", "nope"])
    assert all(u.endswith(".gguf") and u.startswith("https://") for u in L.MODEL_PRESETS.values())
