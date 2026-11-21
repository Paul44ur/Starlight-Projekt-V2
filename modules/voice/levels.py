"""How loud: the microphone's level while listening, and the envelope of a spoken answer,
both 0..1, for the overlay's orb (docs/plan-overlay.md, section 5). Pure functions."""

from __future__ import annotations

import array
import io
import itertools
import math
import sys
import wave

# The RMS of 16-bit samples that counts as 1.0: normal speech close to a microphone has an
# RMS of about 2,000 to 4,000, so it shows as about 0.5.
FULL_SCALE = 6000.0


def _rms(samples: array.array[int]) -> float:
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def _samples(data: bytes) -> array.array[int]:
    """16-bit little-endian samples (a trailing odd byte is dropped)."""
    samples = array.array("h", data[: len(data) - len(data) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def rms_level(frame: bytes) -> float:
    """The level of one frame of 16-bit mono PCM: 0 for silence, about 0.5 for normal
    speech, at most 1."""
    return min(1.0, _rms(_samples(frame)) / FULL_SCALE)


def envelope(wav: bytes, rate: int = 20) -> list[float]:
    """The loudness of a WAV file (16-bit PCM) in steps of 1/rate seconds, scaled so the
    loudest step is 1 (silence stays 0). Empty for anything that is not such a file."""
    if rate <= 0:
        raise ValueError("rate must be positive")
    try:
        with wave.open(io.BytesIO(wav), "rb") as source:
            if source.getsampwidth() != 2:
                return []
            channels, framerate = source.getnchannels(), source.getframerate()
            data = source.readframes(source.getnframes())
    except (wave.Error, EOFError):
        return []
    samples = _samples(data)
    frames = len(samples) // channels
    count = math.ceil(frames * rate / framerate) if framerate > 0 else 0
    # Step k covers the frames from k/rate to (k+1)/rate seconds (no rounding drift).
    edges = [min(frames, round(k * framerate / rate)) * channels for k in range(count + 1)]
    steps = [_rms(samples[a:b]) for a, b in itertools.pairwise(edges) if b > a]
    loudest = max(steps, default=0.0)
    if loudest <= 0:
        return [0.0] * len(steps)
    return [round(value / loudest, 3) for value in steps]
