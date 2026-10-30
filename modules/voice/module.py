"""Talk to Starlight and hear its answers - all on this PC, nothing is sent anywhere.

Speech to text: whisper.cpp's `whisper-server`, started the first time it is needed and
kept running (on 127.0.0.1 only) so the speech model stays loaded. Text to speech: Piper,
with a voice per language. Starlight.exe --voice installs both into tools/; the web chat
uses them for its microphone and speaker buttons, the terminal for /talk.

More voices (German and English, in several qualities) can be installed from a fixed list
of Piper's voices, and one chosen per language - from the chat or on the Settings page.

With ``wake_word: true`` it listens on this PC's microphone for "Hey Starlight" and
answers out loud (see listening.py): only short stretches of speech are written down, on
this PC, to look for the phrase; nothing is kept and nothing leaves the PC.
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
from starlight.core.config import write_yaml_config
from starlight.core.safepaths import PathGuard, expand

from .listening import FRAME_SECONDS, WAKE_PHRASES, Segmenter, wake_match

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
PIPER_VOICES = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
# Voices that can be installed from here (Piper's names: language-speaker-quality).
CATALOG = (
    "de_DE-thorsten-medium", "de_DE-thorsten-high", "de_DE-thorsten_emotional-medium",
    "de_DE-kerstin-low", "de_DE-ramona-low", "de_DE-eva_k-x_low", "de_DE-karlsson-low",
    "de_DE-pavoque-low", "de_DE-mls-medium",
    "en_US-lessac-medium", "en_US-lessac-high", "en_US-amy-medium", "en_US-ryan-high",
    "en_GB-alan-medium", "en_GB-jenny_dioco-medium", "en_GB-cori-high",
)  # fmt: skip
MAX_VOICE_BYTES = 200 * 1024**2


def voice_path(name: str) -> str:
    """de_DE-thorsten-medium -> de/de_DE/thorsten/medium/de_DE-thorsten-medium."""
    locale, speaker, quality = name.split("-")
    return f"{locale[:2]}/{locale}/{speaker}/{quality}/{name}"


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
    wake_word: bool = False  # listen for "Hey Starlight" on this PC's microphone
    wake_phrases: list[str] = Field(default_factory=lambda: list(WAKE_PHRASES))
    wake_reply: str = "Ja?"  # said after the phrase alone, before you say what you want


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
        self._listener: asyncio.Task[Any] | None = None
        self.speaking = False  # (the listener does not hear its own voice)
        if self.config.wake_word:
            self._listener = ctx.background(self._listen, "wake-word")

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

    def installed_voices(self) -> dict[str, Path]:
        """Every installed voice by its name (de_DE-thorsten-medium)."""
        found = {p.stem: p for p in sorted((self.tools_dir / "piper" / "voices").glob("*.onnx"))}
        for configured in self.config.voices.values():
            path = expand(configured)
            if path.is_file():
                found.setdefault(path.stem, path)
        return found

    def _voice(self, language: str, name: str = "") -> Path:
        if name:
            chosen = self.installed_voices().get(name)
            if chosen is None:
                raise ToolError(f"the voice {name} is not installed (voice.voices)")
            return chosen
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

    async def synthesize_audio(self, text: str, language: str = "", voice: str = "") -> bytes:
        """Text -> speech as WAV bytes (in ``voice``, by its name, if given)."""
        clean = " ".join(text.split())[:MAX_SPEAK_CHARS]
        if not clean:
            raise ToolError("nothing to say")
        self.ctx.kill_switch.check()
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "speech.wav"
            command = self._piper_command(self._voice(language, voice), out)
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
        self.speaking = True
        try:
            self._play_now(audio)
        finally:
            self.speaking = False

    def _play_now(self, audio: bytes) -> None:
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
    async def speak(self, text: str, language: str = "", voice: str = "") -> dict[str, Any]:
        """Say something out loud on this PC's speakers. `language` picks the voice
        ("de", "en"; default: the default voice), or `voice` one by its name."""
        audio = await self.synthesize_audio(text, language, voice)
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
            "wake_word": self._listener is not None and not self._listener.done(),
        }

    # -- choosing and installing voices ----------------------------------------------------

    @tool(name="voices")
    def voice_list(self) -> dict[str, Any]:
        """The installed voices (and which one speaks each language), and the voices that
        can be installed (German and English, low to high quality)."""
        installed = self.installed_voices()
        speaking = {lang: path.stem for lang, path in self.voices().items()}
        return {
            "installed": [
                {"name": name, "language": name[:2], "speaks": speaking.get(name[:2]) == name}
                for name in sorted(installed)
            ],
            "can_install": [
                {"name": name, "language": name[:2], "quality": name.rsplit("-", 1)[1]}
                for name in CATALOG
                if name not in installed
            ],
            "default_language": self.config.default_voice,
            "wake_word": self.config.wake_word,
        }

    @tool
    def choose_voice(self, name: str) -> dict[str, Any]:
        """Let an installed voice (by its name, see voice.voices) speak its language."""
        path = self.installed_voices().get(name.strip())
        if path is None:
            raise ToolError(f"the voice {name} is not installed; voice.install_voice adds it")
        self.config.voices[name.strip()[:2].lower()] = str(path)
        self._save_config()
        return {"speaks": name.strip()[:2].lower(), "voice": name.strip()}

    def _save_config(self) -> None:
        write_yaml_config(
            self.ctx.paths.module_configs / "voice.yaml",
            self.config.model_dump(),
            "# The voice module (modules/voice): speech recognition (whisper.cpp) and voices\n"
            "# (Piper), both on this PC. Changed from the chat or the Settings page.",
        )

    @tool(own_timeout=True)
    async def install_voice(self, name: str) -> dict[str, Any]:
        """Download one of Piper's voices (see voice.voices: can_install) into
        tools/piper/voices, about 20 to 120 MB. Choose it with voice.choose_voice."""
        name = name.strip()
        if name not in CATALOG:
            raise ToolError(f"{name} is not one of the voices offered here (voice.voices)")
        folder = self.tools_dir / "piper" / "voices"
        folder.mkdir(parents=True, exist_ok=True)
        for suffix in (".onnx.json", ".onnx"):  # the small one first: a typo fails fast
            target = folder / f"{name}{suffix}"
            if target.is_file():
                continue
            await self._download(f"{PIPER_VOICES}/{voice_path(name)}{suffix}", target)
        return {"installed": name, "size_mb": round((folder / f"{name}.onnx").stat().st_size / 1e6)}

    async def _download(self, url: str, target: Path) -> None:
        part = target.with_name(target.name + ".part")
        size = 0
        try:
            async with self._client.stream("GET", url, follow_redirects=True) as response:
                if response.status_code != 200:
                    raise ToolError(f"the download failed ({response.status_code})")
                with part.open("wb") as out:
                    async for chunk in response.aiter_bytes(1 << 16):
                        size += len(chunk)
                        if size > MAX_VOICE_BYTES:
                            raise ToolError("that download is far bigger than a voice")
                        self.ctx.kill_switch.check()
                        out.write(chunk)
        except httpx.HTTPError as exc:
            part.unlink(missing_ok=True)
            raise ToolError(f"the download failed: {exc!r}") from exc
        except BaseException:  # refused, too big, the kill switch: no half file is left
            part.unlink(missing_ok=True)
            raise
        part.replace(target)

    # -- the wake phrase -------------------------------------------------------------------

    @tool
    async def wake_word(self, on: bool) -> dict[str, Any]:  # (async: it starts a task)
        """Listen for "Hey Starlight" on this PC's microphone (on) or stop (off). Then say
        what you want, and the answer is spoken."""
        self.config.wake_word = on
        self._save_config()
        if on and (self._listener is None or self._listener.done()):
            self._listener = self.ctx.background(self._listen, "wake-word")
        elif not on and self._listener is not None:
            self._listener.cancel()
            self._listener = None
        return {"listening": on, "phrases": self.config.wake_phrases}

    async def _listen(self) -> None:
        """Read the microphone until stopped; act on each stretch of speech."""
        try:
            import sounddevice
        except Exception:  # not installed, or no PortAudio library on this system
            self.ctx.logger.warning("wake word: needs the 'voice' extra and a microphone")
            return
        frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)
        loop = asyncio.get_running_loop()

        def keep(frame: bytes) -> None:
            with contextlib.suppress(asyncio.QueueFull):
                frames.put_nowait(frame)

        def heard(data: Any, count: int, when: Any, status: Any) -> None:  # (audio thread)
            loop.call_soon_threadsafe(keep, bytes(data))

        try:
            stream = sounddevice.RawInputStream(
                samplerate=RATE, channels=1, dtype="int16",
                blocksize=int(RATE * FRAME_SECONDS), callback=heard,
            )  # fmt: skip
        except Exception as exc:  # no microphone (or it is in use)
            self.ctx.logger.warning("wake word: no microphone", error=str(exc))
            return
        with stream:
            segmenter = Segmenter()
            while True:
                frame = await frames.get()
                if self.speaking:
                    continue
                speech = segmenter.feed(frame)
                if speech is None or self.ctx.kill_switch.engaged:
                    continue
                try:
                    await self.on_speech(speech, lambda: self._next_speech(frames))
                except Exception:  # (speech recognition gone, the kill switch): listen on
                    self.ctx.logger.exception("wake word: could not answer")

    async def _next_speech(self, frames: asyncio.Queue[bytes], wait: float = 8.0) -> bytes | None:
        """The next stretch of speech, after the "yes?": up to ``wait`` seconds for it."""
        segmenter = Segmenter(longest=20)
        with contextlib.suppress(TimeoutError):
            async with asyncio.timeout(wait + 20):
                started = time.monotonic()
                while True:
                    speech = segmenter.feed(await frames.get())
                    if speech is not None:
                        return speech
                    if not segmenter.busy and time.monotonic() - started > wait:
                        return None
        return None

    async def on_speech(self, pcm: bytes, next_speech: Any) -> str | None:
        """A stretch of speech was heard: if it starts with the wake phrase, take the
        request (said with it, or right after the reply) and say the answer. Returns the
        request, or None when it was not for Starlight."""
        text = await self.transcribe_audio(wav_bytes(pcm))
        woke, request = wake_match(text, tuple(self.config.wake_phrases))
        if not woke:
            return None
        if not request:
            if self.config.wake_reply:
                await self._say(self.config.wake_reply)
            follow = await next_speech()
            if follow is None:
                return None
            request = (await self.transcribe_audio(wav_bytes(follow))).strip()
        if not request:
            return None
        self.ctx.logger.info("wake word", request=request[:200])
        answer = await self.ctx.run_request(request)
        await self._say(answer)
        return request

    async def _say(self, text: str) -> None:
        audio = await self.synthesize_audio(text)
        await asyncio.to_thread(self._play, audio)
