"""ModelProvider interface. The core never depends on one runtime: llama.cpp, Ollama or a
test double all sit behind ``chat()``. Step 4 registers the real GPU providers."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from starlight.core.config import ModelConfig

type Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    images: list[bytes] = field(default_factory=list)  # PNG/JPEG, for vision models


@dataclass
class ModelResponse:
    """``usage`` keys when known: prompt_tokens, completion_tokens, tokens_per_second,
    prompt_tokens_per_second, duration_s, model."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


type TokenCallback = Callable[[str], None]


@runtime_checkable
class ModelProvider(Protocol):
    """The core's only view of a model. Providers may also define:

    * ``supports_streaming = True`` and accept ``on_token=`` in ``chat()``
    * ``async warmup()`` to load the model into VRAM at start-up
    * ``async aclose()`` to release connections
    """

    name: str

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        """Return the next assistant turn: text, tool calls, or both."""
        ...


class EchoProvider:
    """Deterministic stand-in for a real model, used until Step 4 and in tests.

    * ``!hello.greet {"name": "Paul"}`` calls that tool with those arguments
    * after a tool result, it answers with the result
    * anything else is echoed back
    """

    name = "echo"

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        last = messages[-1]
        if last.role == "tool":
            return ModelResponse(content=last.content)
        text = last.content.strip()
        if text.startswith("!"):
            head, _, rest = text[1:].partition(" ")
            known = {t["name"] for t in tools}
            if head not in known:
                return ModelResponse(content=f"No tool named '{head}'.")
            try:
                args = json.loads(rest) if rest.strip() else {}
            except json.JSONDecodeError as exc:
                return ModelResponse(content=f"Arguments are not valid JSON: {exc}")
            return ModelResponse(tool_calls=[ToolCall(head, args)])
        return ModelResponse(content=f"You said: {text}")


type ProviderFactory = Callable[[dict[str, Any]], ModelProvider]

_PROVIDERS: dict[str, ProviderFactory] = {"echo": lambda options: EchoProvider()}


def register_provider(name: str, factory: ProviderFactory) -> None:
    _PROVIDERS[name] = factory


def create_provider(config: ModelConfig) -> ModelProvider:
    import starlight.models  # noqa: F401  (registers the built-in GPU providers)

    try:
        factory = _PROVIDERS[config.provider]
    except KeyError:
        known = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"unknown model provider '{config.provider}' (known: {known})") from None
    return factory(config.options)
