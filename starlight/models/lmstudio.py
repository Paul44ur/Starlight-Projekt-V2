"""LM Studio's local server (Developer tab, "Start server"; port 1234 by default).

LM Studio loads and runs the model itself; Starlight only talks to it. It speaks the OpenAI
chat API, so this is the OpenAI-compatible provider with LM Studio's defaults: its address,
no llama-server extensions (``cache_prompt``), at most four stop strings (the OpenAI limit),
and, unless the config names a model, the language model that is loaded in LM Studio.
"""

from __future__ import annotations

from typing import Any

import httpx

from starlight.core.model import Message, ModelResponse, TokenCallback
from starlight.models.openai_compat import OpenAICompatProvider

DEFAULT_URL = "http://127.0.0.1:1234/v1"
# The turn ends of the common model families (Qwen, Llama 3, Gemma). Starlight also cuts
# an answer at any other turn marker itself (output.cut_at_turn_end).
STOP = ["<|im_end|>", "<|endoftext|>", "<|eot_id|>", "<end_of_turn>"]
AUTO = ("", "auto")  # model.options.model: use whatever LM Studio has loaded
NO_MODEL = "load one in LM Studio, or set model.options.model to its name"


class LMStudioError(RuntimeError):
    """LM Studio answers, but there is no model Starlight could use."""


class LMStudioProvider(OpenAICompatProvider):
    def __init__(self, model: str = "auto", base_url: str = DEFAULT_URL, **options: Any) -> None:
        options.setdefault("name", "lmstudio")
        options.setdefault("stop", list(STOP))
        options.setdefault("cache_prompt", False)
        super().__init__(model=model, base_url=base_url, **options)
        self.wanted = model

    async def loaded_model(self) -> str:
        """The model to ask: the one named in the config, else the language model LM Studio
        has loaded. LM Studio's own API says which one is loaded; its OpenAI model list can
        also show models that are only downloaded, so it is used only when it names one."""
        if self.wanted not in AUTO:
            return self.wanted
        root = self.base_url.removesuffix("/v1")
        try:
            response = await self._client.get(f"{root}/api/v0/models")
            native = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            native = None
        if isinstance(native, dict):
            for entry in native.get("data") or []:
                if entry.get("state") == "loaded" and entry.get("type") in ("llm", "vlm"):
                    return str(entry["id"])
            raise LMStudioError(f"LM Studio has no language model loaded: {NO_MODEL}")
        response = await self._client.get(f"{self.base_url}/models")
        response.raise_for_status()
        listed = response.json().get("data") or []
        ids = [str(m["id"]) for m in listed if "embed" not in str(m.get("id", "")).lower()]
        if len(ids) == 1:
            return ids[0]
        if not ids:
            raise LMStudioError(f"LM Studio has no language model: {NO_MODEL}")
        raise LMStudioError(f"LM Studio lists {len(ids)} models and does not say which: {NO_MODEL}")

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        on_token: TokenCallback | None = None,
    ) -> ModelResponse:
        self.model = await self.loaded_model()  # asked every time: you may switch models
        return await super().chat(messages, tools, on_token)

    async def warmup(self) -> None:
        """LM Studio is up and has a model to use."""
        self.model = await self.loaded_model()
