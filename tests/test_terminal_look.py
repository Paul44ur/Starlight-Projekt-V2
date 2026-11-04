"""The terminal chat's look (UI block U10): the web's colours at the terminal's depth (none
with NO_COLOR), answers in light Markdown while they stream, wrapped between words, a spinner
until the first word, and the modes in the prompt and a status line."""

from __future__ import annotations

import asyncio
import io
import random
import re

import pytest

from starlight import termstyle
from starlight.termstyle import PLAIN, MarkdownStream, palette, wants_light
from starlight.tui import ChatView

ANSWER = """## Your week
Here is **what I found** for *you*, with `a code span` and a sentence long enough to wrap.

- Monday: **rain** in the morning, and a note long enough to wrap under its own text
  - nested
1. first

> a quote

---

```python
print("hi")  # **not bold** and *not italic*
```

| Day | Rain |
| --- | ---: |
| Mon | 2 mm |
| Tuesday | 10 mm |

The end: 2*3, snake_case_name and \\*stars\\*."""
CODES = re.compile(r"\x1b\[[0-9;]*m")


def shown(text: str) -> list[str]:
    """The lines as they look on the screen (without the codes)."""
    return CODES.sub("", text).split("\n")


def test_markdown_is_drawn_while_it_streams() -> None:
    look = palette("truecolor")
    whole = MarkdownStream(look, 60, indent=2, column=2)
    once = whole.feed(ANSWER) + whole.close()
    pieces = MarkdownStream(look, 60, indent=2, column=2)
    streamed, rng, at = "", random.Random(7), 0
    while at < len(ANSWER):
        step = rng.randint(1, 6)
        streamed += pieces.feed(ANSWER[at : at + step])
        at += step
    streamed += pieces.close()
    assert streamed == once  # however the words arrive, the same picture
    lines = shown(once)
    assert lines[0] == "Your week"  # (after the label, at column 2)
    assert f"{look.bold}{look.accent}Your week" in once  # a heading: bold, in the accent
    assert f"{look.bold}what I found" in once and f"{look.italic}you" in once
    assert f"{look.code}a code span" in once
    assert "  • Monday: rain in the morning, and a note long enough to" in lines
    assert "    wrap under its own text" in lines  # under the text, not under the bullet
    assert "    • nested" in lines and "  1. first" in lines and "  │ a quote" in lines
    assert any(set(line.strip()) == {"─"} and len(line.strip()) > 40 for line in lines)  # rule
    assert '    print("hi")  # **not bold** and *not italic*' in lines  # code stays as written
    assert "  Day     │  Rain" in lines and "  Tuesday │ 10 mm" in lines  # lined up, right
    assert "  The end: 2*3, snake_case_name and *stars*." in lines
    assert "**" not in "".join(line for line in lines if "print" not in line)


def test_words_wrap_at_the_width_and_are_never_split() -> None:
    words = [w * random.Random(i).randint(1, 9) for i, w in enumerate("abcdefghij" * 12)]
    text = " ".join(words)
    stream = MarkdownStream(PLAIN, 40, indent=2, column=2)
    lines = (stream.feed(text) + stream.close()).split("\n")
    assert all(termstyle.width_of(line) <= 40 for line in lines)
    assert all(line.startswith("  ") for line in lines[1:])
    assert " ".join(" ".join(lines).split()) == text  # every word whole, in order


def test_the_colours_are_the_webs_at_the_terminals_depth() -> None:
    dark, light = palette("truecolor"), palette("truecolor", light=True)
    assert dark.accent == "\x1b[38;2;110;168;254m"  # --accent, dark (style.css)
    assert light.accent == "\x1b[38;2;37;99;235m"  # --accent, light
    assert palette("256").accent == "\x1b[38;5;75m"
    assert palette("standard").accent == "\x1b[94m"  # the basic 16: bright blue
    for none in (palette(None), palette("truecolor", no_color=True)):
        assert none.accent == none.bad == none.code == ""  # NO_COLOR: no colours ...
        assert none.bold == "\x1b[1m"  # ... but bold stays
    assert wants_light({"STARLIGHT_TERMINAL_THEME": "light"})
    assert wants_light({"COLORFGBG": "0;15"}) and not wants_light({"COLORFGBG": "15;0"})
    assert not wants_light({})  # most terminals are dark


def terminal(monkeypatch: pytest.MonkeyPatch) -> tuple[ChatView, io.StringIO]:
    """A chat view that believes it writes to a colour terminal (called in the test itself:
    pytest puts its own sys.stdout back when the test begins)."""
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    monkeypatch.setattr("starlight.tui._width", lambda: 60)
    view = ChatView()
    view.rich, view.palette = True, palette("truecolor")
    return view, out


async def test_a_spinner_shows_until_the_first_word(monkeypatch: pytest.MonkeyPatch) -> None:
    view, out = terminal(monkeypatch)
    emit = view.on_token()
    view.on_tool({"tool": "weather.today", "state": "running"})
    await asyncio.sleep(0.5)
    waited = out.getvalue()
    assert "Starlight is working…" in waited and "weather.today" in waited
    emit("Hello **there**, ")
    emit("friend.")
    await asyncio.sleep(0.3)
    view.end_answer(streamed=True)
    text = out.getvalue()
    after = text[len(waited) :]
    assert after.startswith("\r\x1b[K")  # the spinner went away before the answer
    assert "working" not in after  # and did not come back
    assert shown(after.removeprefix("\r\x1b[K")) == ["✦ Hello there, friend.", ""]
    assert view.palette.bold + "there" in after


async def test_a_question_moves_the_spinner_aside(monkeypatch: pytest.MonkeyPatch) -> None:
    view, out = terminal(monkeypatch)
    monkeypatch.setattr("starlight.console.interactive", lambda: True)
    asked: list[str] = []

    async def read(_reader: object, hint: str) -> str:
        asked.append(out.getvalue())
        return "y"

    monkeypatch.setattr("starlight.console.lines.read", read)
    view._console = __import__("rich.console").console.Console(file=out, width=60)
    view.on_token()
    await asyncio.sleep(0.4)
    assert await view.confirm("Delete the file?") is True
    assert asked[0].rstrip().endswith("╯")  # the question box, no spinner after it
    await asyncio.sleep(0.4)
    assert "working" in out.getvalue()[len(asked[0]) :]  # back while the answer is awaited
    view.end_answer(streamed=False)


def test_the_prompt_and_the_status_line_show_the_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    view, out = terminal(monkeypatch)
    assert CODES.sub("", view.prompt()) == "you › "  # noqa: RUF001
    view.model = "lmstudio · qwen3"
    view.modes = {"stopped": True, "practice": True, "private": True}
    assert CODES.sub("", view.prompt()) == "you · stopped · practice · private › "  # noqa: RUF001
    view.status()
    line = CODES.sub("", out.getvalue())
    assert "● lmstudio · qwen3" in line and "kill switch ON (/resume)" in line
    assert "practice on" in line and "private conversation" in line
    plain = ChatView()  # not a terminal: as before
    plain.modes = {"practice": True}
    assert plain.prompt() == "you> "
