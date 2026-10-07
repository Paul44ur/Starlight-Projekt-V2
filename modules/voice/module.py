"""Talk to Starlight and hear its answers - all on this PC, nothing is sent anywhere.

Speech to text: whisper.cpp's `whisper-server`, started the first time it is needed and
kept running (on 127.0.0.1 only) so the speech model stays loaded. Text to speech: Piper,
with a voice per language. Starlight.exe --voice installs both into tools/; the web chat
uses them for its microphone and speaker buttons, the terminal for /talk.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.safepaths import PathGuard, expand

RATE = 16000  # whisper wants 16 kHz mono
MAX_SPEAK_CHARS = 4000


class Config(BaseModel):
    whisper_server: str = ""
    whisper_model: str = ""
    whisper_port: int = Field(default=8178, ge=1024, le=65535)
    whisper_threads: int = Field(default=4, ge=1)
    language: str = "auto"
    piper: str = ""
    voices: dict[str, str] = Field(default_factory=dict)
    default_voice: str = "de"
    record_seconds: float = Field(default=6, gt=0, le=120)


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def wav_bytes(samples: bytes, rate: int = RATE) -> bytes:
    """16-bit mono PCM as a WAV file."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples)
    return buffer.getvalue()


class VoiceModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.tools_dir = ctx.paths.root / "tools"
        self._server: subprocess.Popen[bytes] | None = None
        self._starting = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=5))
        ctx.kill_switch.add_cleanup(self._stop_playback, owner=ctx.name)
        self._player: subprocess.Popen[bytes] | None = None

    async def shutdown(self) -> None:
        await self._client.aclose()
        if self._server is not None and self._server.poll() is None:
            self._server.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self._server.wait(5)
        self._stop_playback()

    def _stop_playback(self) -> None:
        if self._player is not None and self._player.poll() is None:
            self._player.kill()
        if sys.platform == "win32":
            with contextlib.suppress(Exception):
                import winsound

                winsound.PlaySound(None, 0)

    # -- finding the programs -----------------------------------------------------------

    def _find(self, configured: str, *candidates: Path, name: str) -> Path | None:
        if configured:
            path = expand(configured)
            return path if path.is_file() else None
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        found = shutil.which(name)
        return Path(found) if found else None

    def whisper_binary(self) -> Path | None:
        return self._find(
            self.config.whisper_server,
            self.tools_dir / "whisper" / _exe("whisper-server"),
            name="whisper-server",
        )

    def whisper_model(self) -> Path | None:
        if self.config.whisper_model:
            path = expand(self.config.whisper_model)
            return path if path.is_file() else None
        models = sorted((self.tools_dir / "whisper" / "models").glob("ggml-*.bin"))
        return models[0] if models else None

    def piper_binary(self) -> Path | None:
        return self._find(self.config.piper, self.tools_dir / "piper" / _exe("piper"), name="piper")

    def voices(self) -> dict[str, Path]:
        found: dict[str, Path] = {}
        for path in sorted((self.tools_dir / "piper" / "voices").glob("*.onnx")):
            found.setdefault(path.name[:2].lower(), path)  # de_DE-thorsten -> de
        for language, configured in self.config.voices.items():
            path = expand(configured)
            if path.is_file():
                found[language.lower()] = path
        return found

    # -- speech to text --------------------------------------------------------------------

    @property
    def _url(self) -> str:
        return f"http://127.0.0.1:{self.config.whisper_port}"

    async def _ensure_server(self) -> None:
        async with self._starting:
            if self._server is not None and self._server.poll() is None:
                return
            command = self._server_command()
            flags = 0x08000000 if sys.platform == "win32" else 0  # no console window
            log = (self.ctx.paths.logs / "whisper-server.log").open("ab")
            try:
                self._server = subprocess.Popen(
                    command, stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
                    cwd=str(Path(command[0]).parent),
                )  # fmt: skip
            finally:
                log.close()
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if self._server.poll() is not None:
                    raise ToolError("the speech server stopped; see logs/whisper-server.log")
                with contextlib.suppress(httpx.HTTPError):
                    if (await self._client.get(self._url + "/")).status_code < 500:
                        return
                await asyncio.sleep(0.5)
            raise ToolError("the speech server did not start in time")

    def _server_command(self) -> list[str]:
        binary, model = self.whisper_binary(), self.whisper_model()
        if binary is None or model is None:
            raise ToolError(
                "speech recognition is not installed: start Starlight.exe with --voice, "
                "or set whisper_server and whisper_model in config/modules/voice.yaml"
            )
        return [
            str(binary), "-m", str(model), "--host", "127.0.0.1",
            "--port", str(self.config.whisper_port), "-t", str(self.config.whisper_threads),
        ]  # fmt: skip

    async def transcribe_audio(self, audio: bytes, language: str = "") -> str:
        """Speech in a WAV file (16 kHz mono works best) -> text."""
        if not audio:
            raise ToolError("no audio")
        self.ctx.kill_switch.check()
        await self._ensure_server()
        response = await self._client.post(
            self._url + "/inference",
            files={"file": ("speech.wav", audio, "audio/wav")},
            data={
                "response_format": "json",
                "language": language or self.config.language,
                "temperature": "0.0",
            },
        )
        if response.status_code != 200:
            raise ToolError(f"speech recognition failed ({response.status_code})")
        return str(response.json().get("text", "")).strip()

    @tool
    async def transcribe(self, path: str, language: str = "") -> dict[str, Any]:
        """Turn a recording (a .wav file) into text. `language` like "de" or "en" (default:
        detect it)."""
        file = PathGuard(self.ctx.paths.root, self.ctx.output_dir).resolve(path).path
        if not file.is_file():
            raise ToolError(f"{file} does not exist")
        if file.stat().st_size > 200 * 1024**2:
            raise ToolError("that recording is too large (over 200 MB)")
        text = await self.transcribe_audio(file.read_bytes(), language)
        return {"file": str(file), "text": text}

    def record(self, seconds: float) -> bytes:
        """Record from the default microphone (needs the 'voice' extra: sounddevice)."""
        try:
            import sounddevice
        except Exception as exc:  # not installed, or no PortAudio library on this system
            raise ToolError(
                "recording needs the 'voice' extra (sounddevice) and a microphone"
            ) from exc
        self.ctx.kill_switch.check()
        frames = sounddevice.rec(int(seconds * RATE), samplerate=RATE, channels=1, dtype="int16")
        sounddevice.wait()
        return wav_bytes(frames.tobytes())

    @tool
    async def listen(self, seconds: float = 0) -> dict[str, Any]:
        """Record from this PC's microphone for a few seconds and return what was said."""
        duration = min(seconds or self.config.record_seconds, 120)
        audio = await asyncio.to_thread(self.record, duration)
        return {"seconds": duration, "text": await self.transcribe_audio(audio)}

    # -- text to speech --------------------------------------------------------------------

    def _voice(self, language: str) -> Path:
        voices = self.voices()
        wanted = (language or self.config.default_voice).lower()[:2]
        voice = (
            voices.get(wanted)
            or voices.get(self.config.default_voice[:2])
            or next(iter(voices.values()), None)
        )
        if voice is None:
            raise ToolError(
                "no voice is installed: start Starlight.exe with --voice, or add a Piper "
                "voice (.onnx) under voices in config/modules/voice.yaml"
            )
        return voice

    def _piper_command(self, voice: Path, out: Path) -> list[str]:
        piper = self.piper_binary()
        if piper is None:
            raise ToolError(
                "speech output is not installed: start Starlight.exe with --voice, or set "
                "piper in config/modules/voice.yaml"
            )
        return [str(piper), "--model", str(voice), "--output_file", str(out)]

    async def synthesize_audio(self, text: str, language: str = "") -> bytes:
        """Text -> speech as WAV bytes."""
        clean = " ".join(text.split())[:MAX_SPEAK_CHARS]
        if not clean:
            raise ToolError("nothing to say")
        self.ctx.kill_switch.check()
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "speech.wav"
            command = self._piper_command(self._voice(language), out)
            flags = 0x08000000 if sys.platform == "win32" else 0
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE, cwd=str(Path(command[0]).parent),
                creationflags=flags,
            )  # fmt: skip
            try:
                _, err = await asyncio.wait_for(process.communicate(clean.encode()), 120)
            except TimeoutError as exc:
                process.kill()
                raise ToolError("speaking took too long") from exc
            if process.returncode != 0 or not out.is_file():
                raise ToolError(f"speech output failed: {err.decode(errors='replace')[-300:]}")
            return out.read_bytes()

    def _play(self, audio: bytes) -> None:
        if sys.platform == "win32":
            import winsound

            winsound.PlaySound(audio, winsound.SND_MEMORY)
            return
        player = shutil.which("paplay") or shutil.which("aplay")
        if player is None:
            raise ToolError("no audio player found (install pulseaudio-utils or alsa-utils)")
        self._player = subprocess.Popen([player, "-"], stdin=subprocess.PIPE)
        assert self._player.stdin is not None
        self._player.stdin.write(audio)
        self._player.stdin.close()
        self._player.wait()

    @tool
    async def speak(self, text: str, language: str = "") -> dict[str, Any]:
        """Say something out loud on this PC's speakers. `language` picks the voice
        ("de", "en"; default: the default voice)."""
        audio = await self.synthesize_audio(text, language)
        await asyncio.to_thread(self._play, audio)
        return {"spoken": True, "characters": min(len(text), MAX_SPEAK_CHARS)}

    @tool
    def status(self) -> dict[str, Any]:
        """Whether speech input and output are installed, and which voices there are."""
        return {
            "speech_to_text": bool(self.whisper_binary() and self.whisper_model()),
            "speech_model": str(self.whisper_model() or "") or None,
            "text_to_speech": self.piper_binary() is not None,
            "voices": sorted(self.voices()),
            "running": self._server is not None and self._server.poll() is None,
        }
