"""Voice: the speech server is started on first use and kept running, recordings are sent
to it, Piper turns text into a WAV, and the web chat's microphone and speaker endpoints
use the same. The speech programs are small Python stand-ins, so this runs anywhere."""

from __future__ import annotations

import io
import shutil
import socket
import sys
import textwrap
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from tests.conftest import REPO

FAKE_WHISPER = textwrap.dedent(
    """
    import json, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    port = int(sys.argv[sys.argv.index("--port") + 1])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            heard = "a wav file" if b"RIFF" in body and b"WAVE" in body else "noise"
            language = "de" if b'name="language"\\r\\n\\r\\nde' in body else "auto"
            reply = json.dumps({"text": f" I heard {heard} ({language}) "}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
    """
)

FAKE_PIPER = textwrap.dedent(
    """
    import sys, wave
    text = sys.stdin.read()
    out = sys.argv[sys.argv.index("--output_file") + 1]
    voice = sys.argv[sys.argv.index("--model") + 1]
    with wave.open(out, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"\\x00\\x00" * len(text))
    print(voice, file=sys.stderr)
    """
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path], tmp_path: Path) -> Paths:
    use_module("voice")
    scripts = tmp_path / "fake"
    scripts.mkdir()
    (scripts / "whisper.py").write_text(FAKE_WHISPER)
    (scripts / "piper.py").write_text(FAKE_PIPER)
    voices = paths.root / "tools" / "piper" / "voices"
    voices.mkdir(parents=True)
    (voices / "de_DE-thorsten-medium.onnx").write_bytes(b"onnx")
    (voices / "en_US-lessac-medium.onnx").write_bytes(b"onnx")
    (paths.module_configs / "voice.yaml").write_text(f"whisper_port: {free_port()}\n")
    return paths


def fake(k: Kernel, scripts: Path) -> Any:
    voice = k.modules.records["voice"].instance
    assert voice is not None
    voice._server_command = lambda: [  # type: ignore[attr-defined]
        sys.executable,
        str(scripts / "whisper.py"),
        "--port",
        str(voice.config.whisper_port),
    ]
    voice._piper_command = lambda model, out: [  # type: ignore[attr-defined]
        sys.executable,
        str(scripts / "piper.py"),
        "--model",
        str(model),
        "--output_file",
        str(out),
    ]
    return voice


def recording() -> bytes:
    sys.path.insert(0, str(REPO / "modules"))
    try:
        from voice.module import wav_bytes  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    return bytes(wav_bytes(b"\x01\x00" * 1600))


async def test_speech_to_text_starts_the_server_once(project: Paths, tmp_path: Path) -> None:
    async with Kernel(project) as k:
        voice = fake(k, tmp_path / "fake")
        assert await voice.transcribe_audio(recording()) == "I heard a wav file (auto)"
        server = voice._server
        assert await voice.transcribe_audio(recording(), language="de") == (
            "I heard a wav file (de)"
        )
        assert voice._server is server  # kept running, model stays loaded
        status = await k.dispatcher.call("voice.status", {})
        assert status.output["running"] and status.output["voices"] == ["de", "en"]
    assert server.poll() is not None  # stopped with Starlight


async def test_text_to_speech_picks_the_voice_by_language(project: Paths, tmp_path: Path) -> None:
    async with Kernel(project) as k:
        voice = fake(k, tmp_path / "fake")
        audio = await voice.synthesize_audio("Hallo Welt", "de")
        with wave.open(io.BytesIO(audio)) as w:
            assert w.getnframes() == len("Hallo Welt")
        chosen: list[str] = []
        original = voice._piper_command
        voice._piper_command = lambda model, out: chosen.append(model.name) or original(model, out)
        await voice.synthesize_audio("Hello", "en")
        await voice.synthesize_audio("Hej", "sv")  # no Swedish voice: the default (German)
        assert chosen == ["en_US-lessac-medium.onnx", "de_DE-thorsten-medium.onnx"]


async def test_missing_programs_are_explained(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("voice")
    shutil.rmtree(paths.root / "tools", ignore_errors=True)
    async with Kernel(paths) as k:
        voice = k.modules.records["voice"].instance
        assert voice is not None
        voice._piper_command = lambda model, out: ["piper"]  # never reached
        with pytest.raises(Exception, match="--voice"):
            await voice.transcribe_audio(b"RIFF....WAVE")
        with pytest.raises(Exception, match="no voice is installed"):
            await voice.synthesize_audio("hello")


def test_microphone_and_speaker_in_the_web_chat(project: Paths, tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from starlight.web.app import create_app

    project.config_file.write_text("model:\n  provider: echo\nkill_switch:\n  hotkey: null\n")
    local = {"host": "127.0.0.1:8700"}
    with TestClient(
        create_app(project), base_url="http://127.0.0.1:8700", client=("127.0.0.1", 50000)
    ) as c:
        fake(c.app.state.kernel, tmp_path / "fake")
        assert c.post("/api/transcribe", headers=local, content=b"").status_code == 400
        heard = c.post("/api/transcribe", headers=local, content=recording())
        assert heard.json() == {"text": "I heard a wav file (auto)"}
        spoken = c.post("/api/speak", headers=local, json={"text": "Guten Tag"})
        assert spoken.headers["content-type"] == "audio/wav" and spoken.content[:4] == b"RIFF"
        stranger = {"host": "127.0.0.1:8700", "origin": "https://evil.example"}
        assert c.post("/api/speak", headers=stranger, json={"text": "x"}).status_code == 403
