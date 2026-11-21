"""The overlay's JSON-lines messages: every message round-trips, every bad line gives None."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest

from starlight.overlay.protocol import (
    MAX_LINE,
    MAX_TEXT,
    Answer,
    Ask,
    CancelAsk,
    Caption,
    Choice,
    Clear,
    Config,
    Error,
    Hello,
    Level,
    Message,
    Point,
    Quit,
    Ready,
    Screen,
    Speech,
    StateMsg,
    decode,
    encode,
)

YES_NO = (Choice("yes", "Ja"), Choice("no", "Nein"))

MESSAGES: list[Message] = [
    Hello(accent="#4FC8FF", orb_monitor=1, orb="always", reduce_motion=False, language="de"),
    Hello(accent="#00aa11", orb_monitor=2, orb="awake", reduce_motion=True, language="en"),
    Config(),
    Config(accent="#FFFFFF"),
    Config(orb_monitor=2, orb="awake", reduce_motion=True, language="en"),
    Point(id="p1", box=(1920, 40, 120, 30), label="Einstellungen", confidence="sure"),
    Point(id="p2", box=(-1900, -50, 1, 1), label="", confidence="maybe", seconds=2.5),
    Clear(),
    *(StateMsg(state=s) for s in ("asleep", "idle", "listening", "thinking", "speaking")),
    Level(level=0.0),
    Level(level=0.42),
    Level(level=1.0),
    Speech(envelope=(0.0, 0.5, 1.0)),
    Speech(envelope=(), rate=50.0),
    Caption(text="Hier ist es: „Datei“ <b>", who="starlight"),
    Caption(text="wo ist das Menü?", who="you", seconds=1.0),
    Ask(id="a1", question="Klicken?", choices=YES_NO, timeout=10.0),
    Ask(id="a2", question="?", choices=(*YES_NO, Choice("later", "Später")), timeout=0.5),
    CancelAsk(id="a1"),
    Quit(),
    Ready(screens=(Screen("\\\\.\\DISPLAY1", 0, 0, 1920, 1080, 1.0),)),
    Ready(
        screens=(
            Screen("A", 0, 0, 1920, 1080, 1.0),
            Screen("B", 1920, 0, 2560, 1440, 1.5),
        ),
        exclude_from_capture=True,
    ),
    Ready(screens=()),
    Answer(id="a1", choice="yes"),
    Answer(id="a1", choice=None),
    Error(text="Qt: no screens"),
]


@pytest.mark.parametrize("msg", MESSAGES, ids=lambda m: type(m).__name__)
def test_round_trip(msg: Message) -> None:
    line = encode(msg)
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    assert json.loads(line)["type"] == msg.type
    assert decode(line) == msg
    assert decode(line.decode("utf-8")) == msg  # str works too
    assert decode(line.rstrip(b"\n")) == msg  # with or without the newline


def test_every_type_is_covered() -> None:
    from starlight.overlay.protocol import TYPES

    assert {m.type for m in MESSAGES} == TYPES
    assert len(TYPES) == 14


def test_encode_is_compact_utf8() -> None:
    line = encode(Caption(text="Menü", who="starlight"))
    assert line == '{"type":"caption","text":"Menü","who":"starlight","seconds":4.0}\n'.encode()


def test_config_leaves_out_unchanged_fields() -> None:
    assert json.loads(encode(Config(language="en"))) == {"type": "config", "language": "en"}
    # null on the wire means unchanged too
    assert decode(b'{"type":"config","accent":null,"orb":"awake"}') == Config(orb="awake")


def test_defaults_when_left_out() -> None:
    point = decode(b'{"type":"point","id":"p","box":[1,2,3,4],"label":"x","confidence":"sure"}')
    assert point == Point(id="p", box=(1, 2, 3, 4), label="x", confidence="sure", seconds=6.0)
    assert decode(b'{"type":"caption","text":"x","who":"you"}') == Caption("x", "you", 4.0)
    assert decode(b'{"type":"speech","envelope":[0.1]}') == Speech((0.1,), 20.0)
    assert decode(b'{"type":"ready","v":1,"screens":[]}') == Ready(screens=())


def test_ints_accepted_where_floats_are_expected() -> None:
    assert decode(b'{"type":"level","level":1}') == Level(1.0)
    msg = decode(b'{"type":"caption","text":"x","who":"you","seconds":3}')
    assert isinstance(msg, Caption) and msg.seconds == 3.0


def test_unknown_fields_are_ignored() -> None:
    assert decode(b'{"type":"clear","later":true}') == Clear()
    assert decode(b'{"type":"level","level":0.5,"peak":0.9}') == Level(0.5)


def test_long_text_is_cut_by_encode() -> None:
    msg = decode(encode(Caption(text="x" * 5000, who="starlight")))
    assert isinstance(msg, Caption)
    assert len(msg.text) == MAX_TEXT and msg.text.endswith("…")
    # exactly the limit is kept as it is
    msg = decode(encode(Caption(text="y" * MAX_TEXT, who="you")))
    assert isinstance(msg, Caption) and msg.text == "y" * MAX_TEXT


GOOD_HELLO: dict[str, Any] = {
    "type": "hello",
    "v": 1,
    "accent": "#4FC8FF",
    "orb_monitor": 1,
    "orb": "always",
    "reduce_motion": False,
    "language": "de",
}
GOOD_POINT: dict[str, Any] = {
    "type": "point",
    "id": "p",
    "box": [0, 0, 10, 10],
    "label": "x",
    "confidence": "sure",
}
GOOD_ASK: dict[str, Any] = {
    "type": "ask",
    "id": "a",
    "question": "?",
    "choices": [{"id": "yes", "label": "Ja"}, {"id": "no", "label": "Nein"}],
    "timeout": 5,
}
GOOD_READY: dict[str, Any] = {
    "type": "ready",
    "v": 1,
    "screens": [{"name": "A", "x": 0, "y": 0, "w": 1920, "h": 1080, "dpr": 1.0}],
    "exclude_from_capture": True,
}


def _with(base: dict[str, Any], **changes: Any) -> bytes:
    data = {**base, **changes}
    return json.dumps({k: v for k, v in data.items() if v is not _DROP}).encode()


_DROP = object()


def test_the_good_bases_are_good() -> None:
    for base in (GOOD_HELLO, GOOD_POINT, GOOD_ASK, GOOD_READY):
        assert decode(_with(base)) is not None


def _screen(**changes: Any) -> bytes:
    screen = {**GOOD_READY["screens"][0], **changes}
    return _with(GOOD_READY, screens=[screen])


INVALID: dict[str, bytes] = {
    # Not a message at all
    "empty": b"",
    "not json": b"{type: hello}",
    "half a line": b'{"type":"clear"',
    "not utf-8": b'{"type":"caption","text":"\xff","who":"you"}',
    "a list": b'[{"type":"clear"}]',
    "a string": b'"clear"',
    "no type": b'{"level":0.5}',
    "type not a string": b'{"type":1}',
    "unknown type": b'{"type":"explode"}',
    "NaN": b'{"type":"level","level":NaN}',
    "Infinity": b'{"type":"caption","text":"x","who":"you","seconds":Infinity}',
    "over 64 KB": b'{"type":"error","text":"' + b"x" * MAX_LINE + b'"}',
    # hello
    "hello missing language": _with(GOOD_HELLO, language=_DROP),
    "hello v 2": _with(GOOD_HELLO, v=2),
    "hello v string": _with(GOOD_HELLO, v="1"),
    "hello accent no hash": _with(GOOD_HELLO, accent="4FC8FF"),
    "hello accent short": _with(GOOD_HELLO, accent="#FFF"),
    "hello accent not hex": _with(GOOD_HELLO, accent="#GGGGGG"),
    "hello accent with markup": _with(GOOD_HELLO, accent="#4FC8FF<b>"),
    "hello orb_monitor 0": _with(GOOD_HELLO, orb_monitor=0),
    "hello orb_monitor float": _with(GOOD_HELLO, orb_monitor=1.0),
    "hello orb_monitor true": _with(GOOD_HELLO, orb_monitor=True),
    "hello orb_monitor 99": _with(GOOD_HELLO, orb_monitor=99),
    "hello orb unknown": _with(GOOD_HELLO, orb="never"),
    "hello reduce_motion 0": _with(GOOD_HELLO, reduce_motion=0),
    "hello language fr": _with(GOOD_HELLO, language="fr"),
    # config
    "config bad accent": b'{"type":"config","accent":"blue"}',
    "config orb_monitor negative": b'{"type":"config","orb_monitor":-1}',
    "config reduce_motion string": b'{"type":"config","reduce_motion":"yes"}',
    # point
    "point missing box": _with(GOOD_POINT, box=_DROP),
    "point box 3 numbers": _with(GOOD_POINT, box=[0, 0, 10]),
    "point box 5 numbers": _with(GOOD_POINT, box=[0, 0, 10, 10, 1]),
    "point box zero width": _with(GOOD_POINT, box=[0, 0, 0, 10]),
    "point box negative height": _with(GOOD_POINT, box=[0, 0, 10, -5]),
    "point box floats": _with(GOOD_POINT, box=[0.5, 0, 10, 10]),
    "point box strings": _with(GOOD_POINT, box=["0", "0", "10", "10"]),
    "point box object": _with(GOOD_POINT, box={"x": 0}),
    "point empty id": _with(GOOD_POINT, id=""),
    "point id number": _with(GOOD_POINT, id=7),
    "point label null": _with(GOOD_POINT, label=None),
    "point label too long": _with(GOOD_POINT, label="x" * (MAX_TEXT + 1)),
    "point confidence unknown": _with(GOOD_POINT, confidence="certain"),
    "point seconds 0": _with(GOOD_POINT, seconds=0),
    "point seconds negative": _with(GOOD_POINT, seconds=-1),
    "point seconds a day": _with(GOOD_POINT, seconds=86400),
    "point seconds string": _with(GOOD_POINT, seconds="6"),
    # state, level, speech
    "state unknown": b'{"type":"state","state":"dancing"}',
    "state missing": b'{"type":"state"}',
    "level above 1": b'{"type":"level","level":1.01}',
    "level below 0": b'{"type":"level","level":-0.1}',
    "level true": b'{"type":"level","level":true}',
    "speech value above 1": b'{"type":"speech","envelope":[0.5,2]}',
    "speech value string": b'{"type":"speech","envelope":["0.5"]}',
    "speech not a list": b'{"type":"speech","envelope":0.5}',
    "speech rate 0": b'{"type":"speech","envelope":[],"rate":0}',
    "speech missing envelope": b'{"type":"speech","rate":20}',
    # caption
    "caption text too long": json.dumps(
        {"type": "caption", "text": "x" * (MAX_TEXT + 1), "who": "you"}
    ).encode(),
    "caption who unknown": b'{"type":"caption","text":"x","who":"someone"}',
    "caption text not a string": b'{"type":"caption","text":["x"],"who":"you"}',
    # ask
    "ask one choice": _with(GOOD_ASK, choices=[{"id": "yes", "label": "Ja"}]),
    "ask four choices": _with(
        GOOD_ASK, choices=[{"id": str(i), "label": str(i)} for i in range(4)]
    ),
    "ask no choices": _with(GOOD_ASK, choices=[]),
    "ask choices not a list": _with(GOOD_ASK, choices="yes,no"),
    "ask choice not an object": _with(GOOD_ASK, choices=["yes", "no"]),
    "ask choice without id": _with(GOOD_ASK, choices=[{"label": "Ja"}, {"id": "no", "label": "N"}]),
    "ask choice empty id": _with(
        GOOD_ASK, choices=[{"id": "", "label": "J"}, {"id": "n", "label": "N"}]
    ),
    "ask same id twice": _with(
        GOOD_ASK, choices=[{"id": "y", "label": "J"}, {"id": "y", "label": "N"}]
    ),
    "ask label too long": _with(
        GOOD_ASK, choices=[{"id": "y", "label": "x" * (MAX_TEXT + 1)}, {"id": "n", "label": "N"}]
    ),
    "ask question too long": _with(GOOD_ASK, question="x" * (MAX_TEXT + 1)),
    "ask missing timeout": _with(GOOD_ASK, timeout=_DROP),
    "ask timeout 0": _with(GOOD_ASK, timeout=0),
    "cancel_ask missing id": b'{"type":"cancel_ask"}',
    # ready
    "ready v 0": _with(GOOD_READY, v=0),
    "ready missing v": _with(GOOD_READY, v=_DROP),
    "ready screens not a list": _with(GOOD_READY, screens={"A": 1}),
    "ready screen zero width": _screen(w=0),
    "ready screen dpr 0": _screen(dpr=0),
    "ready screen dpr string": _screen(dpr="1.0"),
    "ready screen x float": _screen(x=0.5),
    "ready screen missing name": _screen(name=None),
    "ready screen not an object": _with(GOOD_READY, screens=[[0, 0, 1920, 1080]]),
    "ready 17 screens": _with(GOOD_READY, screens=GOOD_READY["screens"] * 17),
    "ready exclude_from_capture string": _with(GOOD_READY, exclude_from_capture="true"),
    # answer, error
    "answer missing choice": b'{"type":"answer","id":"a"}',
    "answer choice number": b'{"type":"answer","id":"a","choice":1}',
    "answer empty id": b'{"type":"answer","id":"","choice":"yes"}',
    "error text not a string": b'{"type":"error","text":null}',
    "error text too long": json.dumps({"type": "error", "text": "x" * (MAX_TEXT + 1)}).encode(),
}


@pytest.mark.parametrize("line", list(INVALID.values()), ids=list(INVALID))
def test_invalid_gives_none(line: bytes) -> None:
    assert decode(line) is None


BAD_TO_SEND: dict[str, Message] = {
    "box no size": Point(id="p", box=(0, 0, 0, 5), label="", confidence="sure"),
    "empty id": CancelAsk(id=""),
    "level above 1": Level(level=1.5),
    "NaN seconds": Caption(text="x", who="you", seconds=float("nan")),
    "four choices": Ask(id="a", question="?", choices=YES_NO + YES_NO, timeout=1.0),
    "bad accent": Hello(
        accent="red", orb_monitor=1, orb="always", reduce_motion=False, language="de"
    ),
    "over 64 KB": Speech(envelope=(0.5,) * 20000),
}


@pytest.mark.parametrize("msg", list(BAD_TO_SEND.values()), ids=list(BAD_TO_SEND))
def test_encode_refuses_what_decode_would(msg: Message) -> None:
    with pytest.raises(ValueError):
        encode(msg)


def test_no_qt_import() -> None:
    # Importing both modules in a fresh Python must not load any Qt binding.
    code = (
        "import sys, starlight.overlay.protocol, starlight.overlay.geometry; "
        "print([m for m in sys.modules if m.startswith(('PySide', 'PyQt', 'shiboken'))])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "[]"
