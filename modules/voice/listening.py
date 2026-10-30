"""Listening for the wake phrase ("Hey Starlight") on this PC's microphone.

The microphone is read in short frames; only stretches of speech (louder than the room's
own noise) are kept, and each short one is given to the local speech recognition to see
whether it starts with the wake phrase. Nothing is recorded to disk and nothing leaves the
PC. What follows the phrase - in the same breath, or after the "yes?" - is the request.
"""

from __future__ import annotations

import array
import math
import re
import unicodedata

FRAME_SECONDS = 0.03
WAKE_PHRASES = (
    "hey starlight",
    "hi starlight",
    "hallo starlight",
    "okay starlight",
    "ok starlight",
)


def loudness(frame: bytes) -> float:
    """The RMS of 16-bit mono samples, 0..32768."""
    samples = array.array("h", frame[: len(frame) - len(frame) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class Segmenter:
    """Cuts a stream of frames into stretches of speech. The noise floor follows the room
    (slowly while quiet), so a fan or a quiet radio does not count as speech."""

    def __init__(
        self,
        frame_seconds: float = FRAME_SECONDS,
        silence_after: float = 0.7,
        longest: float = 8.0,
        shortest: float = 0.3,
        ratio: float = 3.0,
        floor: float = 120.0,
    ) -> None:
        self.frame_seconds = frame_seconds
        self.silence_frames = max(1, round(silence_after / frame_seconds))
        self.longest_frames = max(1, round(longest / frame_seconds))
        self.shortest_frames = max(1, round(shortest / frame_seconds))
        self.ratio, self.noise = ratio, floor
        self._speech: list[bytes] = []
        self._quiet = 0
        self._voiced = 0
        self._before: list[bytes] = []  # a little audio from just before the speech began

    @property
    def busy(self) -> bool:
        """Someone is speaking right now (a stretch has begun and not ended)."""
        return bool(self._speech)

    def feed(self, frame: bytes) -> bytes | None:
        """One frame in; a finished stretch of speech (PCM bytes) out, or None."""
        level = loudness(frame)
        speaking = level > self.noise * self.ratio
        if not self._speech:
            if not speaking:
                self.noise = 0.95 * self.noise + 0.05 * max(level, 30.0)
                self._before = [*self._before[-9:], frame]
                return None
            self._speech = [*self._before, frame]
            self._before, self._quiet, self._voiced = [], 0, 1
            return None
        self._speech.append(frame)
        if speaking:
            self._quiet = 0
            self._voiced += 1
        else:
            self._quiet += 1
        if self._quiet < self.silence_frames and len(self._speech) < self.longest_frames:
            return None
        done, voiced = b"".join(self._speech), self._voiced
        self._speech, self._quiet, self._voiced = [], 0, 0
        return done if voiced >= self.shortest_frames else None  # a click or a cough: not speech


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower())
    letters = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", letters).split())


def wake_match(heard: str, phrases: tuple[str, ...] = WAKE_PHRASES) -> tuple[bool, str]:
    """(whether ``heard`` starts with a wake phrase, what was said after it). Speech
    recognition writes "Star light", "Starlight!" or "Hey, Starlight," alike."""
    parts = heard.split()
    letters = [_plain(part).replace(" ", "") for part in parts]
    for phrase in sorted(phrases, key=len, reverse=True):
        target, taken = phrase.replace(" ", ""), ""
        for index, word in enumerate(letters):
            taken += word
            if len(taken) >= len(target):
                if taken == target:
                    return True, " ".join(parts[index + 1 :]).lstrip(",.!?:; ").strip()
                break
    return False, ""
