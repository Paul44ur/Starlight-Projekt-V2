"""Keep conversations inside the model's context window without cutting them off.

When the estimated prompt grows past ``context_window - reserve_tokens``, the oldest
turns (never the system prompt, never the most recent ``keep_recent`` messages, never
half a tool call) are summarized by the model. The summary replaces them in the prompt
and is published as ``conversation.summarized`` so the memory (Step 5) can keep it.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from starlight.core.config import ModelConfig
from starlight.core.logging import get_logger
from starlight.core.model import Message, ModelProvider

SUMMARY_PREFIX = "Summary of the earlier conversation:\n"
SUMMARIZE_PROMPT = (
    "Summarize this conversation for your own later use. Keep facts, decisions, names, "
    "numbers, open tasks and tool results that still matter. Be concise. Write plain text."
)

type SummaryListener = Callable[[str], Awaitable[None]]


def estimate_tokens(messages: list[Message], tools: list[dict[str, Any]] | None = None) -> int:
    """Rough count (about 3.5 characters per token, plus per-message overhead). Errs on the
    high side, so summarizing starts a little early rather than overflowing."""
    chars = 0
    for m in messages:
        chars += len(m.content) + 16
        for call in m.tool_calls:
            chars += len(call.name) + len(json.dumps(call.arguments))
        chars += 1500 * len(m.images)  # images cost roughly a fixed number of tokens
    if tools:
        chars += len(json.dumps(tools))
    return int(chars / 3.5) + 1


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

    async def fit(self, messages: list[Message], tools: list[dict[str, Any]]) -> list[Message]:
        """Return messages that fit the budget. Modifies nothing in place."""
        if estimate_tokens(messages, tools) <= self.budget:
            return messages
        cut = self._cut_index(messages)
        if cut <= 1:
            return messages  # nothing old enough to summarize
        head, old, recent = messages[:1], messages[1:cut], messages[cut:]
        summary = await self.summarize(old)
        self._log.info("summarized", messages=len(old), chars=len(summary))
        if self.on_summary is not None:
            await self.on_summary(summary)
        return [*head, Message("system", SUMMARY_PREFIX + summary), *recent]

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
