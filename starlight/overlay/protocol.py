"""The messages between the core and the overlay process (``docs/plan-overlay.md`` 4.1).

One JSON object per line, UTF-8, ending in ``\\n``, at most 64 KB a line. Every message has
a ``"type"``. The core's first message is ``hello``; the overlay answers ``ready``.

``encode(msg)`` turns a message into one line. It cuts text that is too long and raises
``ValueError`` for anything else that is not allowed, so the core never sends a bad line.
``decode(line)`` reads one line and returns ``None`` for anything it does not accept: bad
JSON, an unknown type, a missing field, a wrong type, a number out of range, a box with no
size, too many choices or text over 2,000 characters. Callers log and skip ``None``; a bad
line is never fatal. Fields a message does not know are ignored, so a newer peer can add
some.

The strings are plain text, never markup: the overlay draws them with ``drawText``.
"""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

log = logging.getLogger(__name__)

VERSION = 1
MAX_LINE = 64 * 1024  # bytes, with the "\n"
MAX_TEXT = 2000  # characters in any string field
MAX_SECONDS = 3600.0  # for "seconds" and "timeout"
MAX_ORB_MONITOR = 16
MAX_RATE = 1000.0  # speech envelope values per second
MAX_SCREENS = 16

OrbMode = Literal["always", "awake"]
Language = Literal["de", "en"]
Confidence = Literal["sure", "maybe"]
State = Literal["asleep", "idle", "listening", "thinking", "speaking"]
Who = Literal["you", "starlight"]

ORB_MODES: tuple[OrbMode, ...] = ("always", "awake")
LANGUAGES: tuple[Language, ...] = ("de", "en")
CONFIDENCES: tuple[Confidence, ...] = ("sure", "maybe")
STATES: tuple[State, ...] = ("asleep", "idle", "listening", "thinking", "speaking")
WHOS: tuple[Who, ...] = ("you", "starlight")

_ACCENT = re.compile(r"#[0-9A-Fa-f]{6}")

Box = tuple[int, int, int, int]  # x, y, w, h in physical virtual-desktop pixels


class _Invalid(Exception):
    """A field that is not allowed; the message says which and why."""


# --- Core to overlay ---------------------------------------------------------------------


@dataclass(frozen=True)
class Hello:
    accent: str
    orb_monitor: int
    orb: OrbMode
    reduce_motion: bool
    language: Language
    v: int = VERSION
    type: ClassVar[str] = "hello"


@dataclass(frozen=True)
class Config:
    """A settings change: only the fields that are not None changed."""

    accent: str | None = None
    orb_monitor: int | None = None
    orb: OrbMode | None = None
    reduce_motion: bool | None = None
    language: Language | None = None
    type: ClassVar[str] = "config"


@dataclass(frozen=True)
class Point:
    id: str
    box: Box
    label: str
    confidence: Confidence
    seconds: float = 6.0
    type: ClassVar[str] = "point"


@dataclass(frozen=True)
class Clear:
    """Removes the line, the frame and the caption (not the Yes/No card)."""

    type: ClassVar[str] = "clear"


@dataclass(frozen=True)
class StateMsg:
    state: State
    type: ClassVar[str] = "state"


@dataclass(frozen=True)
class Level:
    level: float  # the mic level, 0..1, while listening
    type: ClassVar[str] = "level"


@dataclass(frozen=True)
class Speech:
    envelope: tuple[float, ...]  # each 0..1
    rate: float = 20.0  # values per second
    type: ClassVar[str] = "speech"


@dataclass(frozen=True)
class Caption:
    text: str
    who: Who
    seconds: float = 4.0
    type: ClassVar[str] = "caption"


@dataclass(frozen=True)
class Choice:
    id: str
    label: str


@dataclass(frozen=True)
class Ask:
    id: str
    question: str
    choices: tuple[Choice, ...]  # 2 or 3, with different ids
    timeout: float
    type: ClassVar[str] = "ask"


@dataclass(frozen=True)
class CancelAsk:
    id: str
    type: ClassVar[str] = "cancel_ask"


@dataclass(frozen=True)
class Quit:
    type: ClassVar[str] = "quit"


# --- Overlay to core ---------------------------------------------------------------------


@dataclass(frozen=True)
class Screen:
    """One screen as Qt sees it: logical pixels and the device pixel ratio."""

    name: str
    x: int
    y: int
    w: int
    h: int
    dpr: float


@dataclass(frozen=True)
class Ready:
    screens: tuple[Screen, ...]
    exclude_from_capture: bool = False  # true when every window is hidden from screenshots
    v: int = VERSION
    type: ClassVar[str] = "ready"


@dataclass(frozen=True)
class Answer:
    id: str
    choice: str | None  # the choice's id, or None on a timeout
    type: ClassVar[str] = "answer"


@dataclass(frozen=True)
class Error:
    text: str
    type: ClassVar[str] = "error"


Message = (
    Hello
    | Config
    | Point
    | Clear
    | StateMsg
    | Level
    | Speech
    | Caption
    | Ask
    | CancelAsk
    | Quit
    | Ready
    | Answer
    | Error
)


# --- Field checks (shared by encode and decode) ------------------------------------------


def _str(value: object, name: str, *, empty: bool = True) -> str:
    if not isinstance(value, str):
        raise _Invalid(f"{name}: not a string")
    if len(value) > MAX_TEXT:
        raise _Invalid(f"{name}: over {MAX_TEXT} characters")
    if not empty and not value:
        raise _Invalid(f"{name}: empty")
    return value


def _int(value: object, name: str, low: int | None = None, high: int | None = None) -> int:
    # bool is an int in Python, but true is not a number here.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _Invalid(f"{name}: not a whole number")
    if (low is not None and value < low) or (high is not None and value > high):
        raise _Invalid(f"{name}: {value} out of range")
    return value


def _float(
    value: object,
    name: str,
    low: float,
    high: float,
    *,
    above_low: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _Invalid(f"{name}: not a number")
    number = float(value)
    if not math.isfinite(number):
        raise _Invalid(f"{name}: not finite")
    if number < low or number > high or (above_low and number == low):
        raise _Invalid(f"{name}: {number} out of range")
    return number


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise _Invalid(f"{name}: not true or false")
    return value


def _choice[T: str](value: object, name: str, allowed: tuple[T, ...]) -> T:
    for option in allowed:
        if value == option:
            return option
    raise _Invalid(f"{name}: not one of {', '.join(allowed)}")


def _accent(value: object) -> str:
    if not isinstance(value, str) or not _ACCENT.fullmatch(value):
        raise _Invalid("accent: not #RRGGBB")
    return value


def _version(value: object) -> int:
    if _int(value, "v") != VERSION:
        raise _Invalid(f"v: {value} is not {VERSION}")
    return VERSION


def _box(value: object) -> Box:
    if not isinstance(value, list | tuple) or len(value) != 4:
        raise _Invalid("box: not [x, y, w, h]")
    x, y, w, h = (_int(v, "box") for v in value)
    if w <= 0 or h <= 0:
        raise _Invalid("box: no size")
    return (x, y, w, h)


def _seconds(value: object, name: str) -> float:
    return _float(value, name, 0.0, MAX_SECONDS, above_low=True)


def _choices(value: object) -> tuple[Choice, ...]:
    if not isinstance(value, list | tuple) or not 2 <= len(value) <= 3:
        raise _Invalid("choices: not 2 or 3")
    out: list[Choice] = []
    for item in value:
        if isinstance(item, Choice):
            out.append(Choice(_str(item.id, "choice id", empty=False), _str(item.label, "label")))
        elif isinstance(item, Mapping):
            out.append(
                Choice(
                    _str(item.get("id"), "choice id", empty=False),
                    _str(item.get("label"), "label"),
                )
            )
        else:
            raise _Invalid("choices: not {id, label}")
    if len({c.id for c in out}) != len(out):
        raise _Invalid("choices: the same id twice")
    return tuple(out)


def _screens(value: object) -> tuple[Screen, ...]:
    if not isinstance(value, list | tuple) or len(value) > MAX_SCREENS:
        raise _Invalid("screens: not a list (of at most 16)")
    out: list[Screen] = []
    for item in value:
        if isinstance(item, Screen):
            item = {k: getattr(item, k) for k in ("name", "x", "y", "w", "h", "dpr")}
        if not isinstance(item, Mapping):
            raise _Invalid("screens: not {name, x, y, w, h, dpr}")
        out.append(
            Screen(
                name=_str(item.get("name"), "screen name"),
                x=_int(item.get("x"), "screen x"),
                y=_int(item.get("y"), "screen y"),
                w=_int(item.get("w"), "screen w", low=1),
                h=_int(item.get("h"), "screen h", low=1),
                dpr=_float(item.get("dpr"), "screen dpr", 0.0, 10.0, above_low=True),
            )
        )
    return tuple(out)


# --- Reading -----------------------------------------------------------------------------

_MISSING = object()


class _Fields:
    """A received object's fields: a missing one is an error unless it has a default."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = data

    def __getitem__(self, name: str) -> Any:
        if name not in self._data:
            raise _Invalid(f"{name}: missing")
        return self._data[name]

    def get(self, name: str, default: Any = _MISSING) -> Any:
        value = self._data.get(name, default)
        return None if value is _MISSING else value

    def has(self, name: str) -> bool:
        return self._data.get(name) is not None


def _read_config(f: _Fields) -> Config:
    return Config(
        accent=_accent(f.get("accent")) if f.has("accent") else None,
        orb_monitor=(
            _int(f.get("orb_monitor"), "orb_monitor", 1, MAX_ORB_MONITOR)
            if f.has("orb_monitor")
            else None
        ),
        orb=_choice(f.get("orb"), "orb", ORB_MODES) if f.has("orb") else None,
        reduce_motion=(
            _bool(f.get("reduce_motion"), "reduce_motion") if f.has("reduce_motion") else None
        ),
        language=_choice(f.get("language"), "language", LANGUAGES) if f.has("language") else None,
    )


def _read_speech(f: _Fields) -> Speech:
    envelope = f["envelope"]
    if not isinstance(envelope, list | tuple):
        raise _Invalid("envelope: not a list")
    return Speech(
        envelope=tuple(_float(v, "envelope", 0.0, 1.0) for v in envelope),
        rate=_float(f.get("rate", 20.0), "rate", 0.0, MAX_RATE, above_low=True),
    )


def _read_answer(f: _Fields) -> Answer:
    choice = f["choice"]
    return Answer(
        id=_str(f["id"], "id", empty=False),
        choice=None if choice is None else _str(choice, "choice", empty=False),
    )


_READERS: dict[str, Callable[[_Fields], Message]] = {
    "hello": lambda f: Hello(
        v=_version(f["v"]),
        accent=_accent(f["accent"]),
        orb_monitor=_int(f["orb_monitor"], "orb_monitor", 1, MAX_ORB_MONITOR),
        orb=_choice(f["orb"], "orb", ORB_MODES),
        reduce_motion=_bool(f["reduce_motion"], "reduce_motion"),
        language=_choice(f["language"], "language", LANGUAGES),
    ),
    "config": _read_config,
    "point": lambda f: Point(
        id=_str(f["id"], "id", empty=False),
        box=_box(f["box"]),
        label=_str(f["label"], "label"),
        confidence=_choice(f["confidence"], "confidence", CONFIDENCES),
        seconds=_seconds(f.get("seconds", 6.0), "seconds"),
    ),
    "clear": lambda f: Clear(),
    "state": lambda f: StateMsg(state=_choice(f["state"], "state", STATES)),
    "level": lambda f: Level(level=_float(f["level"], "level", 0.0, 1.0)),
    "speech": _read_speech,
    "caption": lambda f: Caption(
        text=_str(f["text"], "text"),
        who=_choice(f["who"], "who", WHOS),
        seconds=_seconds(f.get("seconds", 4.0), "seconds"),
    ),
    "ask": lambda f: Ask(
        id=_str(f["id"], "id", empty=False),
        question=_str(f["question"], "question"),
        choices=_choices(f["choices"]),
        timeout=_seconds(f["timeout"], "timeout"),
    ),
    "cancel_ask": lambda f: CancelAsk(id=_str(f["id"], "id", empty=False)),
    "quit": lambda f: Quit(),
    "ready": lambda f: Ready(
        v=_version(f["v"]),
        screens=_screens(f["screens"]),
        exclude_from_capture=_bool(f.get("exclude_from_capture", False), "exclude_from_capture"),
    ),
    "answer": _read_answer,
    "error": lambda f: Error(text=_str(f["text"], "text")),
}

TYPES: frozenset[str] = frozenset(_READERS)


def _no_constant(name: str) -> object:
    raise _Invalid(f"{name} is not a number")


def decode(line: bytes | str) -> Message | None:
    """The message in one line, or None if the line is not a valid message (logged)."""
    raw = line.encode("utf-8") if isinstance(line, str) else line
    if len(raw) > MAX_LINE:
        log.warning("overlay message skipped: line over %d bytes", MAX_LINE)
        return None
    try:
        data = json.loads(raw.decode("utf-8"), parse_constant=_no_constant)
    except (UnicodeDecodeError, ValueError, RecursionError, _Invalid):
        log.warning("overlay message skipped: not JSON")
        return None
    if not isinstance(data, dict):
        log.warning("overlay message skipped: not an object")
        return None
    kind = data.get("type")
    reader = _READERS.get(kind) if isinstance(kind, str) else None
    if reader is None:
        log.info("overlay message ignored: unknown type %r", str(kind)[:40])
        return None
    try:
        return reader(_Fields(data))
    except _Invalid as exc:
        log.warning("overlay message %s skipped: %s", kind, exc)
        return None


# --- Writing -----------------------------------------------------------------------------


def _cut(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _fields(msg: Message) -> dict[str, Any]:
    """The message as JSON fields, with long text cut and everything else checked."""
    match msg:
        case Hello():
            return {
                "v": _version(msg.v),
                "accent": _accent(msg.accent),
                "orb_monitor": _int(msg.orb_monitor, "orb_monitor", 1, MAX_ORB_MONITOR),
                "orb": _choice(msg.orb, "orb", ORB_MODES),
                "reduce_motion": _bool(msg.reduce_motion, "reduce_motion"),
                "language": _choice(msg.language, "language", LANGUAGES),
            }
        case Config():
            out: dict[str, Any] = {}
            if msg.accent is not None:
                out["accent"] = _accent(msg.accent)
            if msg.orb_monitor is not None:
                out["orb_monitor"] = _int(msg.orb_monitor, "orb_monitor", 1, MAX_ORB_MONITOR)
            if msg.orb is not None:
                out["orb"] = _choice(msg.orb, "orb", ORB_MODES)
            if msg.reduce_motion is not None:
                out["reduce_motion"] = _bool(msg.reduce_motion, "reduce_motion")
            if msg.language is not None:
                out["language"] = _choice(msg.language, "language", LANGUAGES)
            return out
        case Point():
            return {
                "id": _str(_cut(msg.id), "id", empty=False),
                "box": list(_box(msg.box)),
                "label": _cut(_str_any(msg.label, "label")),
                "confidence": _choice(msg.confidence, "confidence", CONFIDENCES),
                "seconds": _seconds(msg.seconds, "seconds"),
            }
        case Clear() | Quit():
            return {}
        case StateMsg():
            return {"state": _choice(msg.state, "state", STATES)}
        case Level():
            return {"level": _float(msg.level, "level", 0.0, 1.0)}
        case Speech():
            return {
                "envelope": [_float(v, "envelope", 0.0, 1.0) for v in msg.envelope],
                "rate": _float(msg.rate, "rate", 0.0, MAX_RATE, above_low=True),
            }
        case Caption():
            return {
                "text": _cut(_str_any(msg.text, "text")),
                "who": _choice(msg.who, "who", WHOS),
                "seconds": _seconds(msg.seconds, "seconds"),
            }
        case Ask():
            choices = _choices(
                [
                    Choice(_cut(_str_any(c.id, "choice id")), _cut(_str_any(c.label, "label")))
                    for c in msg.choices
                ]
            )
            return {
                "id": _str(_cut(msg.id), "id", empty=False),
                "question": _cut(_str_any(msg.question, "question")),
                "choices": [{"id": c.id, "label": c.label} for c in choices],
                "timeout": _seconds(msg.timeout, "timeout"),
            }
        case CancelAsk():
            return {"id": _str(_cut(msg.id), "id", empty=False)}
        case Ready():
            cut = [
                Screen(_cut(_str_any(s.name, "screen name")), s.x, s.y, s.w, s.h, s.dpr)
                for s in msg.screens
            ]
            return {
                "v": _version(msg.v),
                "screens": [
                    {"name": s.name, "x": s.x, "y": s.y, "w": s.w, "h": s.h, "dpr": s.dpr}
                    for s in _screens(cut)
                ],
                "exclude_from_capture": _bool(msg.exclude_from_capture, "exclude_from_capture"),
            }
        case Answer():
            return {
                "id": _str(_cut(msg.id), "id", empty=False),
                "choice": None if msg.choice is None else _str(msg.choice, "choice", empty=False),
            }
        case Error():
            return {"text": _cut(_str_any(msg.text, "text"))}
    raise ValueError(f"not an overlay message: {type(msg).__name__}")


def _str_any(value: object, name: str) -> str:
    """A string of any length (encode cuts it afterwards)."""
    if not isinstance(value, str):
        raise _Invalid(f"{name}: not a string")
    return value


def encode(msg: Message) -> bytes:
    """One line for the message: compact JSON, UTF-8, ending in a newline.

    Text over 2,000 characters is cut (ending in "…"). Anything else that ``decode`` would
    refuse raises ``ValueError``, as does a line over 64 KB.
    """
    try:
        fields = _fields(msg)
    except _Invalid as exc:
        raise ValueError(f"overlay message {msg.type}: {exc}") from exc
    payload = {"type": msg.type, **fields}
    line = (
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(line) > MAX_LINE:
        raise ValueError(f"overlay message {msg.type}: line over {MAX_LINE} bytes")
    return line
