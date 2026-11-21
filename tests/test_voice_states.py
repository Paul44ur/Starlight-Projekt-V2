"""Voice states for the overlay (docs/plan-overlay.md, section 5, W5): a wake-word request
publishes listening -> caption(you) -> thinking -> caption(starlight) -> speech ->
speaking -> idle; the mic level at most 15 a second while listening; nothing while the wake
word is off; and what is said stays the same. Transcription, the model and playback are
stand-ins, so this runs anywhere."""

from __future__ import annotations

import array
import io
import math
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from modules.voice.levels import envelope, rms_level
from starlight.core import Kernel, Paths
from starlight.core.events import Event


def pcm(amplitude: int, seconds: float, rate: int = 16000, hz: float = 440.0) -> bytes:
    """A sine (or silence, amplitude 0) as 16-bit mono PCM."""
    n = int(rate * seconds)
    return array.array(
        "h", (round(amplitude * math.sin(2 * math.pi * hz * i / rate)) for i in range(n))
    ).tobytes()


def wav(samples: bytes, rate: int = 16000, channels: int = 1) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(samples)
    return buffer.getvalue()


# -- levels.py ---------------------------------------------------------------------------------


def test_rms_level_of_silence_speech_and_a_loud_sine() -> None:
    assert rms_level(b"") == 0.0
    assert rms_level(pcm(0, 0.03)) == 0.0
    # A sine of amplitude A has an RMS of A / sqrt(2): 4243 -> 3000 -> 0.5.
    assert rms_level(pcm(4243, 0.03)) == pytest.approx(0.5, abs=0.01)
    assert rms_level(pcm(32000, 0.03)) == 1.0  # clipped
    assert rms_level(pcm(4243, 0.03) + b"\x01") == pytest.approx(0.5, abs=0.01)  # odd byte


def test_envelope_in_50_ms_steps_scaled_to_the_loudest() -> None:
    # 0.5 s loud, 0.25 s silence, 0.25 s at a quarter: 10 + 5 + 5 steps of 50 ms.
    sound = wav(pcm(8000, 0.5) + pcm(0, 0.25) + pcm(2000, 0.25))
    steps = envelope(sound)
    assert len(steps) == 20
    assert steps[:10] == pytest.approx([1.0] * 10, abs=0.01)
    assert steps[10:15] == [0.0] * 5
    assert steps[15:] == pytest.approx([0.25] * 5, abs=0.01)
    assert len(envelope(sound, rate=10)) == 10


def test_envelope_of_silence_other_rates_and_bad_files() -> None:
    assert envelope(wav(pcm(0, 0.2))) == [0.0] * 4
    piper_like = wav(pcm(5000, 1.0, rate=22050), rate=22050)  # 1102.5 samples a step
    assert envelope(piper_like) == pytest.approx([1.0] * 20, abs=0.01)
    stereo = wav(pcm(5000, 0.5) * 2, channels=2)  # (two channels, interleaved: 0.5 s)
    assert len(envelope(stereo)) == 10
    assert envelope(b"not a wav") == []
    assert envelope(b"") == []


# -- the events --------------------------------------------------------------------------------


@pytest.fixture
def home(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("voice")
    with paths.config_file.open("a") as config:
        config.write("memory:\n  enabled: false\nskills:\n  enabled: false\n")
    return paths


ANSWER = "It is sunny in Kiel. Tomorrow it rains."


def stub(k: Kernel, heard: list[str], answer: str = ANSWER) -> tuple[Any, list[str], list[str]]:
    """The voice module with transcription, the model and playback replaced. Returns it,
    the events as short strings, and what was played (as the text that was synthesized)."""
    record = k.modules.records["voice"]
    voice = record.instance
    assert voice is not None, record.error
    events: list[str] = []
    played: list[str] = []
    texts: dict[bytes, str] = {}

    def seen(event: Event) -> None:
        data = event.data
        if event.name == "voice.state":
            events.append(data["state"])
        elif event.name == "voice.caption":
            events.append(f"caption({data['who']}): {data['text']}")
        elif event.name == "voice.speech":
            assert data["rate"] == 20 and data["envelope"] and max(data["envelope"]) == 1.0
            events.append("speech")
        elif event.name == "voice.level":
            events.append(f"level {data['level']}")

    k.bus.subscribe("voice.*", seen)
    queue = iter(heard)

    async def transcribe(audio: bytes, language: str = "") -> str:
        return next(queue)

    async def run_request(text: str) -> str:
        events.append(f"(model: {text})")
        return answer

    async def synthesize(text: str, language: str = "", voice_name: str = "") -> bytes:
        audio = wav(pcm(6000, 0.3) + bytes([len(texts) % 256, 0]))  # (each one different)
        texts[audio] = text
        return audio

    def play_now(audio: bytes) -> None:
        events.append("(playing)")
        played.append(texts[audio])

    voice.transcribe_audio, voice.synthesize_audio = transcribe, synthesize
    voice._play_now = play_now
    voice.ctx.run_request = run_request
    return voice, events, played


async def more() -> bytes:
    return b"\0\0" * 1600


async def test_a_wake_word_request_tells_each_step_in_order(home: Paths) -> None:
    async with Kernel(home) as k:
        voice, events, played = stub(k, ["Hey Starlight, how is the weather?"])
        voice.config.wake_word = True
        assert await voice.on_speech(b"\0\0" * 1600, more) == "how is the weather?"
        assert events == [
            "listening",
            "caption(you): how is the weather?",
            "thinking",
            "(model: how is the weather?)",
            "caption(starlight): It is sunny in Kiel.",
            "speech",
            "speaking",
            "(playing)",
            "idle",
        ]
        assert played == [ANSWER]  # the whole answer is said, as before


async def test_the_phrase_alone_says_yes_then_listens_again(home: Paths) -> None:
    async with Kernel(home) as k:
        voice, events, played = stub(k, ["Hey Starlight", "What time is it?"], "10 past 9.")
        voice.config.wake_word = True
        assert await voice.on_speech(b"\0\0" * 1600, more) == "What time is it?"
        assert events == [
            "listening",
            "speech", "speaking", "(playing)",  # "Ja?"
            "listening",
            "caption(you): What time is it?",
            "thinking",
            "(model: What time is it?)",
            "caption(starlight): 10 past 9.",
            "speech", "speaking", "(playing)",
            "idle",
        ]  # fmt: skip
        assert played == ["Ja?", "10 past 9."]


async def test_not_for_starlight_or_nothing_after_it(home: Paths) -> None:
    async with Kernel(home) as k:
        voice, events, _ = stub(k, ["Nice weather today", "Hey Starlight"])
        voice.config.wake_word, voice.config.wake_reply = True, ""

        async def nothing() -> None:
            return None

        assert await voice.on_speech(b"\0\0" * 1600, more) is None
        assert events == []  # not for Starlight: the orb does not move
        assert await voice.on_speech(b"\0\0" * 1600, nothing) is None
        assert events == ["listening", "idle"]


async def test_back_to_idle_when_the_answer_fails(home: Paths) -> None:
    async with Kernel(home) as k:
        voice, events, _ = stub(k, ["Hey Starlight, hello"])
        voice.config.wake_word = True

        async def broken(text: str) -> str:
            raise RuntimeError("model gone")

        voice.ctx.run_request = broken
        with pytest.raises(RuntimeError):
            await voice.on_speech(b"\0\0" * 1600, more)
        assert events == ["listening", "caption(you): hello", "thinking", "idle"]


async def test_long_captions_are_cut(home: Paths) -> None:
    async with Kernel(home) as k:
        long = "word " * 60
        voice, events, _ = stub(k, [f"Hey Starlight, {long}"], answer=long + ". Second.")
        voice.config.wake_word = True
        await voice.on_speech(b"\0\0" * 1600, more)
        captions = [e.split(": ", 1)[1] for e in events if e.startswith("caption")]
        assert [len(c) for c in captions] == [120, 120]
        assert all(c.endswith("…") and "\n" not in c for c in captions)


async def test_nothing_is_published_while_the_wake_word_is_off(home: Paths) -> None:
    async with Kernel(home) as k:
        voice, events, played = stub(k, ["Hey Starlight, how is the weather?"])
        assert not voice.config.wake_word
        assert await voice.on_speech(b"\0\0" * 1600, more) == "how is the weather?"
        await voice._mic_level(pcm(4243, 0.03))
        assert events == ["(model: how is the weather?)", "(playing)"]
        assert played == [ANSWER]


async def test_the_mic_level_while_listening_at_most_15_a_second(home: Paths) -> None:
    async with Kernel(home) as k:
        voice, events, _ = stub(k, [])
        voice.config.wake_word = True
        now = [0.0]
        voice._clock = lambda: now[0]
        frame = pcm(4243, 0.03)
        await voice._mic_level(frame)  # idle: no level
        assert events == []
        await voice._set_state("listening")
        for _ in range(100):  # a frame every 10 ms for one second
            await voice._mic_level(frame)
            now[0] += 0.01
        levels = [e for e in events if e.startswith("level")]
        assert 13 <= len(levels) <= 15
        assert all(float(e.split()[1]) == pytest.approx(0.5, abs=0.01) for e in levels)


async def test_the_level_follows_the_frames_awaited_after_the_phrase(home: Paths) -> None:
    import asyncio

    async with Kernel(home) as k:
        voice, events, _ = stub(k, [])
        voice.config.wake_word = True
        await voice._set_state("listening")
        frames: asyncio.Queue[bytes] = asyncio.Queue()
        frames.put_nowait(pcm(4243, 0.03))  # too short to be a request
        for _ in range(30):  # then 0.9 s of silence: nothing more is waited for
            frames.put_nowait(pcm(0, 0.03))
        ticks = iter(range(100))
        voice._clock = lambda: float(next(ticks))
        assert await voice._next_speech(frames, wait=0.0) is None
        levels = [float(e.split()[1]) for e in events if e.startswith("level")]
        assert levels[0] == pytest.approx(0.5, abs=0.01)
        assert len(levels) > 10 and set(levels[1:]) == {0.0}  # every frame it waited on


async def test_wake_word_off_says_asleep_and_on_waits_for_the_microphone(home: Paths) -> None:
    async with Kernel(home) as k:
        voice, events, _ = stub(k, [])
        started: list[str] = []

        async def listen() -> None:  # (no microphone here; the real one says "idle")
            started.append("listening")

        voice._listen = listen
        assert (await k.dispatcher.call("voice.wake_word", {"on": True})).ok
        assert events == [] and voice.state == "asleep"
        await voice._set_state("idle")
        assert (await k.dispatcher.call("voice.wake_word", {"on": False})).ok
        assert events == ["idle", "asleep"]
        await voice._set_state("idle")  # a request cancelled by "off" ends later
        assert events == ["idle", "asleep"] and voice.state == "asleep"
