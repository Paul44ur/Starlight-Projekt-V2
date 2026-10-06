"""Route each request to a small fast model or a large one.

The fast model first labels the request SIMPLE or COMPLEX (a few tokens, cheap). SIMPLE
requests (greetings, short facts) are answered by the fast model; everything else goes to
the large model. Every later step of the same request stays on the model chosen first.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from starlight.core.config import ModelConfig
from starlight.core.model import Message, ModelProvider, ModelResponse, TokenCallback

CLASSIFY_PROMPT = (
    "Classify the user's request. Answer with one word: SIMPLE if it is small talk or a "
    "short factual question that needs no tools and no multi-step reasoning, otherwise "
    "COMPLEX.\n\nRequest: {request}"
)


# The choice for the request being handled now. Each request (terminal, WhatsApp ...) runs
# in its own task and so has its own value: two requests at once never swap models.
_choice: ContextVar[ModelProvider | None] = ContextVar("router_choice", default=None)


class RouterProvider:
    name = "router"

    def __init__(self, fast: ModelProvider, main: ModelProvider, max_simple_chars: int = 400):
        self.fast = fast
        self.main = main
        self.max_simple_chars = max_simple_chars
        self.supports_streaming = getattr(main, "supports_streaming", False) and getattr(
            fast, "supports_streaming", False
        )

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> RouterProvider:
        from starlight.core.model import create_provider

        fast = create_provider(ModelConfig.model_validate(options["fast"]))
        main = create_provider(ModelConfig.model_validate(options["main"]))
        return cls(fast, main, int(options.get("max_simple_chars", 400)))

    async def classify(self, request: str) -> ModelProvider:
        if len(request) > self.max_simple_chars:
            return self.main
        try:
            response = await self.fast.chat(
                [Message("user", CLASSIFY_PROMPT.format(request=request))], []
            )
        except Exception:
            return self.main
        return self.fast if "SIMPLE" in response.content.upper() else self.main

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        on_token: TokenCallback | None = None,
    ) -> ModelResponse:
        provider = _choice.get()
        if provider is None or (messages and messages[-1].role == "user"):
            provider = await self.classify(messages[-1].content) if messages else self.main
            _choice.set(provider)  # later steps of this request stay on it
        if on_token is not None and getattr(provider, "supports_streaming", False):
            response = await provider.chat(messages, tools, on_token=on_token)  # type: ignore[call-arg]
        else:
            response = await provider.chat(messages, tools)
        response.usage["routed_to"] = "fast" if provider is self.fast else "main"
        return response

    @property
    def summarizer(self) -> ModelProvider:
        """Summaries are cheap work: the context manager uses the fast model."""
        return self.fast

    async def warmup(self) -> None:
        for provider in (self.fast, self.main):
            warmup = getattr(provider, "warmup", None)
            if warmup is not None:
                await warmup()

    async def aclose(self) -> None:
        for provider in (self.fast, self.main):
            close = getattr(provider, "aclose", None)
            if close is not None:
                await close()
