"""Keeping a model's reply clean, whatever the model file gets wrong.

Two problems seen with real GGUF files (merged or fine-tuned "uncensored" models in particular):

* the end-of-turn token is not marked as a stop, so the model keeps writing: it writes "user"
  and then invents the user's next message, often in a loop;
* tool calls are written as text (a ```json block or <tool_call> tags) instead of in the
  server's tool-call format, so they are shown in the chat instead of being run.

``StreamFilter`` decides what to show while a reply streams in: nothing from an end-of-turn
marker on, and nothing from where a tool call written as text begins (until the end of the
reply shows whether it was one). ``recover_tool_calls`` turns such text into real calls when
it names a known tool.
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
    """The reply up to the first end-of-turn marker; the rest was never meant to be said."""
    cut = _first(text, TURN_MARKERS)
    if cut >= 0:
        text = text[:cut].rstrip()
    return text.replace("�", "")


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

    def feed(self, piece: str) -> str:
        if self.ended:
            return ""
        self.text += piece.replace("�", "")
        cut = _first(self.text, TURN_MARKERS)
        if cut >= 0:
            self.text = self.text[:cut].rstrip()
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
        calls, _, end = _scan(self.text, known)
        if not calls:
            return False
        after = self.text[end:].lstrip()
        if not after:
            return False
        return not any(s.startswith(after) or after.startswith(s) for s in CALL_STARTS)


def _first(text: str, needles: tuple[str, ...]) -> int:
    found = [i for needle in needles if (i := text.find(needle)) >= 0]
    return min(found) if found else -1


def _partial_tail(text: str) -> int:
    """How many characters at the end could still become a marker."""
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
            except json.JSONDecodeError:
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
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("name"), str):
        return None
    name = decode_name(data["name"])
    if name not in known:
        return None
    return tool_call(name, data.get("arguments", data.get("parameters", {})))
