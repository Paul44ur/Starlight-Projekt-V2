"""Keep conversations inside the model's context window without cutting them off.

When the estimated prompt grows past ``context_window - reserve_tokens``, the oldest
turns (never the system prompt, never the most recent ``keep_recent`` messages, never
half a tool call) are summarized by the model. The summary replaces them in the prompt
and is published as ``conversation.summarized`` so memory can keep it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from starlight.core.config import ModelConfig
from starlight.core.logging import get_logger
from starlight.core.model import Message, ModelProvider

SUMMARY_PREFIX = "Summary of the earlier conversation:\n"
CUT_NOTE = "\n[... {more} more characters cut: too long for the model's context window]"
SUMMARIZE_PROMPT = (
    "Summarize this conversation for your own later use. Keep facts, decisions, names, "
    "numbers, open tasks and tool results that still matter. Be concise. Write plain text."
)

type SummaryListener = Callable[[str], Awaitable[None]]


CHARS_PER_TOKEN = 3.5  # the rough ratio estimate_tokens counts with (and shorten undoes)


def estimate_tokens(messages: list[Message], tools: list[dict[str, Any]] | None = None) -> int:
    """Rough count (about CHARS_PER_TOKEN characters per token, plus per-message overhead).
    Errs on the high side, so summarizing starts a little early rather than overflowing."""
    chars = 0
    for m in messages:
        chars += len(m.content) + 16
        for call in m.tool_calls:
            chars += len(call.name) + len(json.dumps(call.arguments))
        chars += 1500 * len(m.images)  # images cost roughly a fixed number of tokens
    if tools:
        chars += len(json.dumps(tools))
    return int(chars / CHARS_PER_TOKEN) + 1


SUMMARY_ROOM = 800  # tokens a summary takes, at most, in what is left

# How `shorten` cuts a message, in characters:
MIN_CUTTABLE_CHARS = 2000  # a shorter message is never cut
MIN_KEPT_CHARS = 1000  # what is kept of a cut message, at least
CUT_MARGIN_CHARS = 500  # cut this much more than the overflow: room for CUT_NOTE, and slack


class ContextManager:
    def __init__(
        self,
        provider: ModelProvider,
        config: ModelConfig | None = None,
        on_summary: SummaryListener | None = None,
    ) -> None:
        self.provider = provider
        self.config = config or ModelConfig()
        self.on_summary = on_summary
        self._log = get_logger("context")

    @property
    def budget(self) -> int:
        return max(512, self.config.context_window - self.config.reserve_tokens)

    def _cut_index(self, messages: list[Message]) -> int:
        """Index of the first message to keep: at least keep_recent from the end, moved
        back so a tool result is never kept without the call that asked for it."""
        cut = max(1, len(messages) - self.config.keep_recent)
        while cut > 1 and messages[cut].role == "tool":
            cut -= 1
        return cut

    async def compact(self, messages: list[Message], tools: list[dict[str, Any]]) -> list[Message]:
        """Over the budget: the older turns as one summary, the recent ones as they are.
        This is what the conversation keeps from then on.

        Only ``messages[0]`` is protected. The agent's enrichers put this request's own
        context (date and time, recalled memories, skills) in system messages right after
        it, so those are summarized together with the old turns."""
        if estimate_tokens(messages, tools) <= self.budget:
            return messages
        cut = self._cut_index(messages)
        if cut <= 1:
            return messages
        head, old, recent = messages[:1], messages[1:cut], messages[cut:]
        if (
            estimate_tokens([*head, *recent], tools) + min(SUMMARY_ROOM, self.budget // 4)
            > self.budget
        ):
            # The recent messages alone (say, a huge pasted text) already fill the budget:
            # summarizing the older ones would not help, and would be asked again at every
            # tool step of the turn. `shorten` cuts the big message for this request instead.
            return messages
        summary = await self.summarize(old)
        self._log.info("summarized", messages=len(old), chars=len(summary))
        if self.on_summary is not None:
            await self.on_summary(summary)
        return [*head, Message("system", SUMMARY_PREFIX + summary), *recent]

    def shorten(self, messages: list[Message], tools: list[dict[str, Any]]) -> list[Message]:
        """Still too long (e.g. one huge pasted text or file): cut the longest messages,
        never the system prompt, until the prompt fits. Only for what is sent to the model
        this time - the conversation keeps your text whole."""
        over = estimate_tokens(messages, tools) - self.budget
        if over <= 0:
            return messages
        messages = list(messages)
        while over > 0:
            if len(messages) < 2:
                break  # only the system prompt, which is never cut
            index = max(range(1, len(messages)), key=lambda i: len(messages[i].content))
            if len(messages[index].content) < MIN_CUTTABLE_CHARS:
                break  # nothing big left to cut
            message = messages[index]
            overflow_chars = int(over * CHARS_PER_TOKEN)
            keep = max(MIN_KEPT_CHARS, len(message.content) - overflow_chars - CUT_MARGIN_CHARS)
            text = message.content[:keep] + CUT_NOTE.format(more=len(message.content) - keep)
            messages[index] = replace(message, content=text)
            self._log.warning("message cut to fit the context", role=message.role, kept=keep)
            over = estimate_tokens(messages, tools) - self.budget
        return messages

    async def summarize(self, messages: list[Message]) -> str:
        lines = []
        for m in messages:
            if m.content.startswith(SUMMARY_PREFIX):
                lines.append(m.content)
            elif m.role == "tool":
                lines.append(f"[tool {m.name}] {m.content[:2000]}")
            elif m.tool_calls:
                calls = ", ".join(f"{c.name}({json.dumps(c.arguments)})" for c in m.tool_calls)
                lines.append(f"assistant called {calls} {m.content}".strip())
            else:
                lines.append(f"{m.role}: {m.content}")
        transcript = "\n".join(lines)
        model = getattr(self.provider, "summarizer", self.provider)
        response = await model.chat(
            [Message("system", SUMMARIZE_PROMPT), Message("user", transcript)], []
        )
        return response.content.strip() or transcript[-4000:]
