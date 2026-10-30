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
SUMMARY = (
    "Summarize this transcript of a recording in the language it is in. Start with two or "
    "three sentences on what it was about, then the main points as a list, then "
    "'Decisions' and 'To-dos' (who does what, by when) if there are any. Do not invent "
    "anything that was not said."
)
CHUNK = 12000  # characters of transcript the model reads at once
PIECE_SECONDS = 300  # of a recording, sent to speech recognition at once
MAX_SPEAK_CHARS = 4000


async def summarize_long(model: Any, text: str) -> str:
    """A summary of a transcript of any length: a long one in parts first, then those."""
    from starlight.core.model import Message

    async def once(part: str) -> str:
        reply = await model.chat([Message("system", SUMMARY), Message("user", part)], [])
        return str(reply.content).strip()

    if len(text) <= CHUNK:
        return await once(text)
    parts = [text[i : i + CHUNK] for i in range(0, len(text), CHUNK)]
    notes = [await once(part) for part in parts]
    return await once("Summaries of consecutive parts of one recording:\n\n" + "\n\n".join(notes))


def free_output(folder: Path, name: str) -> Path:
    """``folder/name``, or ``name (2)`` ... when that is taken: nothing is overwritten."""
    target = folder / name
    n = 2
    while target.exists():
        target = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    return target


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
        self._ready = False  # the speech server answered; a started process is not enough
        self.start_timeout = 90.0  # seconds a big model may take to load
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

    async def _answers(self) -> bool:
        with contextlib.suppress(httpx.HTTPError):
            return (await self._client.get(self._url + "/")).status_code < 500
        return False

    async def _ensure_server(self) -> None:
        async with self._starting:
            running = self._server is not None and self._server.poll() is None
            if self._ready and (running or self._server is None):
                return
            if not running and await self._answers():  # another Starlight process started it
                self._ready = True
                return
            if not running:
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
            # A process that is still loading its model is waited for again by the next call.
            assert self._server is not None
            deadline = time.monotonic() + self.start_timeout
            while time.monotonic() < deadline:
                if self._server.poll() is not None:
                    raise ToolError("the speech server stopped; see logs/whisper-server.log")
                if await self._answers():
                    self._ready = True
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
        try:
            response = await self._client.post(
                self._url + "/inference",
                files={"file": ("speech.wav", audio, "audio/wav")},
                data={
                    "response_format": "json",
                    "language": language or self.config.language,
                    "temperature": "0.0",
                },
            )
            heard = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:  # server gone, timeout, not JSON
            self._ready = False  # check again whether it is there next time
            raise ToolError(f"speech recognition failed: {exc!r}") from exc
        if response.status_code != 200:
            raise ToolError(f"speech recognition failed ({response.status_code})")
        return str(heard.get("text", "")).strip() if isinstance(heard, dict) else ""

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

    def _pieces(self, file: Path) -> list[bytes]:
        """The recording as WAV pieces of at most PIECE_SECONDS each (one request each,
        so a long meeting never runs into a time limit): a .wav as it is, anything else
        (mp3, m4a, ogg, a video ...) through ffmpeg when it is installed."""
        if file.suffix.lower() == ".wav":
            try:
                with wave.open(str(file), "rb") as source:
                    params = source.getparams()
                    step = source.getframerate() * PIECE_SECONDS
                    pieces = []
                    while frames := source.readframes(step):
                        buffer = io.BytesIO()
                        with wave.open(buffer, "wb") as piece:
                            piece.setparams(params)
                            piece.writeframes(frames)
                        pieces.append(buffer.getvalue())
                    return pieces
            except (wave.Error, EOFError):  # a WAV this module cannot read: send it whole
                return [file.read_bytes()]
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise ToolError(
                f"{file.suffix or 'this'} files need ffmpeg to be read (on Windows: "
                "winget install ffmpeg); a .wav file works without it"
            )
        done = subprocess.run(
            [ffmpeg, "-nostdin", "-loglevel", "error", "-i", str(file),
             "-ar", str(RATE), "-ac", "1", "-f", "s16le", "pipe:1"],
            capture_output=True, timeout=1800, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )  # fmt: skip
        if done.returncode != 0 or not done.stdout:
            detail = done.stderr.decode("utf-8", "replace").strip()[-200:]
            raise ToolError(f"ffmpeg could not read {file.name}: {detail or 'no sound found'}")
        step = RATE * 2 * PIECE_SECONDS  # 16-bit mono
        return [wav_bytes(done.stdout[i : i + step]) for i in range(0, len(done.stdout), step)]

    @tool(own_timeout=True)
    async def summarize_recording(self, path: str, language: str = "") -> dict[str, Any]:
        """Transcribe a recording (a meeting, a voice memo, a lecture; .wav, or mp3/m4a/
        ogg/video with ffmpeg installed) and summarize it: the main points, decisions and
        to-dos. Both are saved as a Markdown file in your Starlight folder."""
        file = PathGuard(self.ctx.paths.root, self.ctx.output_dir).resolve(path).path
        if not file.is_file():
            raise ToolError(f"{file} does not exist")
        if file.stat().st_size > 2 * 1024**3:
            raise ToolError("that recording is too large (over 2 GB)")
        pieces = await asyncio.to_thread(self._pieces, file)
        heard = [await self.transcribe_audio(piece, language) for piece in pieces]
        text = " ".join(part for part in heard if part)
        if not text:
            raise ToolError("nothing was understood in that recording")
        model = self.ctx.model_for("summary")
        if model is None:
            raise ToolError("no model is configured")
        summary = await summarize_long(model, text)
        target = free_output(self.ctx.output_dir / "Recordings", f"{file.stem} summary.md")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            f"# {file.stem}\n\n## Summary\n\n{summary}\n\n## Transcript\n\n{text}\n",
            encoding="utf-8",
        )
        return {"summary": summary, "saved": str(target), "transcript_chars": len(text)}

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
