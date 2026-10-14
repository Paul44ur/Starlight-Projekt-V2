"""Keeping a model's reply clean, whatever the model file gets wrong.

Problems seen with real GGUF files (merged or fine-tuned "uncensored" models in particular):

* the end-of-turn token is not marked as a stop, so the model keeps writing: it writes "user"
  (sometimes after a stray character or glitch text, which is how a broken special token
  shows) and then invents the user's next message, often in a loop;
* it repeats itself: the same passage again and again until the length limit;
* tool calls are written as text (a ```json block or <tool_call> tags) instead of in the
  server's tool-call format, so they are shown in the chat instead of being run.

``StreamFilter`` decides what to show while a reply streams in: nothing from an end-of-turn
marker on, nothing after a passage starts repeating, and nothing from where a tool call
written as text begins (until the end of the reply shows whether it was one).
``recover_tool_calls`` turns such text into real calls when it names a known tool.
"""

from __future__ import annotations

import json
import re

from starlight.core.model import ToolCall
from starlight.models.common import decode_name, tool_call

# What a model writes when its turn is over. Special tokens are usually not shown as text, so
# a leaked "<|im_end|><|im_start|>user" arrives as "\nuser\n".
TURN_MARKERS = (
    "<|im_end|>",
    "<|im_start|>",
    "<|endoftext|>",
    "<|eot_id|>",
    "<|start_header_id|>",
    "<|end|>",
    "<end_of_turn>",
    "\nuser\n",
)
# A line that is only a lowercase role name, maybe after a stray symbol or control
# character: a leaked "<|im_start|>user" whose special token came out as a symbol
# ("\n🐂user\n"). Never markdown or ASCII punctuation, so "## System", "- User" or
# "-- user" in an answer stay; leaked roles are lowercase, headings are not.
_STRAY = r"(?:[^\x00-\x7f\w\s]|[\x00-\x08\x0b\x0c\x0e-\x1f])"
_ROLE_LINE = re.compile(rf"\n{_STRAY}{{0,4}}[ \t]*(?:user|assistant|system)[ \t]*\n")
# A reply stuck in a loop repeats a passage this long, word for word, at least LOOPS
# times. Saying something twice (a function, then the whole file) is not a loop.
REPEAT = 200
LOOPS = 3

# A tool call that is still being written is looked at again after this many new characters
# (a very long call is otherwise scanned from its start at every piece).
SCAN_EVERY = 2000
CALL_ENDS = ("}", "```", "</tool_call>")  # what can end a call: look again at once

# How a tool call written as text begins.
CALL_STARTS = ("```json", "```tool_call", "```\n{", "<tool_call>", '{"name"')

_MARKERS = TURN_MARKERS + CALL_STARTS
_BLOCK = re.compile(
    r"<tool_call>\s*(?P<tag>\{.*?\})\s*</tool_call>"
    r"|```(?:json|tool_call)?\s*(?P<fence>\{.*?\})\s*```",
    re.DOTALL,
)
_DECODER = json.JSONDecoder()


def cut_at_turn_end(text: str) -> str:
    """The reply up to the first end-of-turn marker, and without a passage repeated over and
    over; the rest was never meant to be said."""
    cut = _turn_end(text)
    if cut >= 0:
        text = text[:cut].rstrip()
    looped = cut_repetition(text)
    if looped is not None:
        text = looped
    return text.replace("�", "")


def cut_repetition(text: str, size: int = REPEAT, loops: int = LOOPS) -> str | None:
    """The text up to the second turn of a loop - a passage of at least ``size`` characters
    said ``loops`` times in a row - or None when the text does not go round like that."""
    for start in range(0, len(text) - size * loops + 1, 20):
        if _loops_at(text, start, size, loops):
            return _cut_loop(text, start, size)
    return None


def _cut_loop(text: str, at: int, size: int) -> str:
    """The text up to the second turn of the loop that the passage at ``at`` is part of."""
    chunk = text[at : at + size]
    period = text.find(chunk, at + 1) - at  # one turn of the loop
    first = at  # move back to where the loop really began
    while first > 0 and text[first - 1] == text[first - 1 + period]:
        first -= 1
    return text[: first + period].rstrip()


def _span(period: int, size: int, loops: int) -> int:
    """How long a loop of this period must run: ``loops`` copies of the passage that don't
    overlap, back to back."""
    step = period * -(-size // period)  # whole turns of the loop that hold one passage
    return (loops - 1) * step + size


def _periodic(text: str, start: int, end: int, period: int) -> bool:
    return text[start + period : end] == text[start : end - period]


def _loops_at(text: str, start: int, size: int, loops: int) -> bool:
    """Whether the text goes round in a loop from ``start``: the passage there comes again
    and again, each turn right after the last. The same passage said in different places
    (a function, then an explanation, then the whole file) is not a loop, and passages of a
    few repeated characters (a ===== rule) never count."""
    chunk = text[start : start + size]
    if len(set(chunk)) < 20:
        return False
    period = text.find(chunk, start + 1) - start
    if period <= 0:
        return False
    end = start + _span(period, size, loops)
    return end <= len(text) and _periodic(text, start, end, period)


def _tail_loop(text: str, size: int = REPEAT, loops: int = LOOPS) -> int:
    """Where a loop that the text ends in began (its last ``size`` characters came
    ``loops`` times in a row, without overlapping), or -1."""
    chunk = text[-size:]
    if len(chunk) < size or len(set(chunk)) < 20:
        return -1
    before = text.rfind(chunk, 0, len(text) - 1)  # the nearest earlier copy
    if before < 0:
        return -1
    period = len(text) - size - before
    start = len(text) - _span(period, size, loops)
    if start < 0 or not _periodic(text, start, len(text), period):
        return -1
    return start


def _turn_end(text: str) -> int:
    """Where the turn ends: the first marker or role line, or -1. A role line at the very
    start is not an end (nothing was said yet)."""
    marker = _first(text, TURN_MARKERS)
    role = _ROLE_LINE.search(text, 1)
    found = [i for i in (marker, role.start() if role else -1) if i >= 0]
    return min(found) if found else -1


def recover_tool_calls(text: str, known: set[str]) -> tuple[list[ToolCall], str]:
    """Tool calls written as text, as real calls, plus the text before them. Only blocks that
    name a known tool count; blocks right after each other are parallel calls; whatever the
    model wrote after them is dropped, because its turn should have ended there."""
    calls, start, _ = _scan(text, known)
    if not calls:
        return [], text
    return calls, text[:start].rstrip()


class StreamFilter:
    """Feed it the streamed text; it returns what can be shown now."""

    def __init__(self) -> None:
        self.text = ""  # the reply so far, cut at an end-of-turn marker
        self.shown = 0  # how much of it was shown
        self.ended = False  # an end-of-turn marker arrived: the turn is over
        self.hold_from: int | None = None  # where a tool call written as text may start
        self._checked = 0  # length of the text when it was last checked for a loop
        self._scanned = 0  # length of the text when it was last scanned for a finished call

    def feed(self, piece: str) -> str:
        if self.ended:
            return ""
        self.text += piece.replace("�", "")
        cut = _turn_end(self.text)
        if cut >= 0:
            self.text = self.text[:cut].rstrip()
            self.ended = True
        elif len(self.text) - self._checked >= 40 and len(self.text) >= LOOPS * REPEAT:
            self._checked = len(self.text)
            at = _tail_loop(self.text)
            if at >= 0:  # the model is going round in circles
                self.text = _cut_loop(self.text, at, REPEAT)
                self.ended = True
        if self.hold_from is None:
            start = _first(self.text, CALL_STARTS)
            if start >= 0:
                self.hold_from = start
        # keep back a few characters that may be the beginning of a marker
        limit = len(self.text) if self.ended else len(self.text) - _partial_tail(self.text)
        if self.hold_from is not None:
            limit = min(limit, self.hold_from)
        if limit <= self.shown:
            return ""
        out = self.text[self.shown : limit]
        self.shown = limit
        return out

    def held(self) -> str:
        """The text not shown yet: shown at the end when it was not a tool call after all."""
        out = self.text[self.shown :]
        self.shown = max(self.shown, len(self.text))
        return out

    def call_finished(self, known: set[str]) -> bool:
        """A tool call written as text is complete and the model went on with something else:
        its turn is over, so there is no need to read (or wait for) the rest."""
        if self.hold_from is None:
            return False
        if self._scanned and len(self.text) - self._scanned < SCAN_EVERY:
            tail = self.text[self._scanned :]
            if not any(end in tail for end in CALL_ENDS):
                return False  # nothing finished last time, and nothing that could end it since
        calls, _, end = _scan(self.text, known)
        if not calls:
            self._scanned = len(self.text)
            return False
        after = self.text[end:].lstrip()
        if not after:
            return False
        return not any(s.startswith(after) or after.startswith(s) for s in CALL_STARTS)


def _first(text: str, needles: tuple[str, ...]) -> int:
    found = [i for needle in needles if (i := text.find(needle)) >= 0]
    return min(found) if found else -1


# The start of a role line that is not finished yet ("\n", "\n🐂", "\n🐂us", ...).
_ROLE_START = re.compile(
    rf"\n{_STRAY}{{0,4}}[ \t]*"
    r"(?:u(?:s(?:e(?:r)?)?)?|a(?:s(?:s(?:i(?:s(?:t(?:a(?:n(?:t)?)?)?)?)?)?)?)?"
    r"|s(?:y(?:s(?:t(?:e(?:m)?)?)?)?)?)?[ \t]*"
)


def _partial_tail(text: str) -> int:
    """How many characters at the end could still become a marker or a role line."""
    line = text.rfind("\n")
    if line >= 0 and _ROLE_START.fullmatch(text, line):
        return len(text) - line
    longest = 0
    for marker in _MARKERS:
        for size in range(min(len(marker) - 1, len(text)), longest, -1):
            if text.endswith(marker[:size]):
                longest = size
                break
    return longest


def _candidates(text: str) -> list[tuple[int, int, str]]:
    """(start, end, json) for every fenced/tagged block and every bare {"name": ...} object."""
    spans = [
        (m.start(), m.end(), m.group("tag") or m.group("fence")) for m in _BLOCK.finditer(text)
    ]
    blocks = list(spans)
    i = text.find('{"name"')
    while i >= 0:
        if not any(s <= i < e for s, e, _ in blocks):
            try:
                _, end = _DECODER.raw_decode(text, i)
            except (json.JSONDecodeError, RecursionError):  # not JSON, or nested absurdly deep
                end = -1
            if end > i:
                spans.append((i, end, text[i:end]))
        i = text.find('{"name"', i + 1)
    return sorted(spans)


def _scan(text: str, known: set[str]) -> tuple[list[ToolCall], int, int]:
    """The first run of consecutive tool calls written as text: (calls, start, end)."""
    calls: list[ToolCall] = []
    start = end = -1
    for s, e, raw in _candidates(text):
        call = _as_call(raw, known)
        if not calls:
            if call is None:
                continue  # an ordinary code block before any call
            start = s
        elif call is None or text[end:s].strip():
            break  # something else came between: not part of this turn's calls
        calls.append(call)
        end = e
    return calls, start, end


def _as_call(raw: str, known: set[str]) -> ToolCall | None:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return None
    name = decode_name(data["name"])
    if name not in known:
        return None
    return tool_call(name, data.get("arguments", data.get("parameters", {})))
