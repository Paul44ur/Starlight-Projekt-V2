"""LM Studio's local server (Developer tab, "Start server"; port 1234 by default).

LM Studio loads and runs the model itself; Starlight only talks to it. It speaks the OpenAI
chat API, so this is the OpenAI-compatible provider with LM Studio's defaults: its address,
no llama-server extensions (``cache_prompt``), at most four stop strings (the OpenAI limit),
and the model:

* named in the config (``model.options.model``): that one, loaded first if LM Studio has it
  but it is not loaded;
* ``auto`` (the default): the language model loaded in LM Studio; when none is loaded and
  LM Studio has exactly one, that one is loaded.

When LM Studio asks for an API token ("Require Authentication", LM Studio 0.4), the token
comes from the environment variable LM_API_TOKEN, or Starlight's vault (lmstudio.token).

A model Starlight loads gets the context length Starlight plans with (model.context_window);
LM Studio's own default is often only 4096 tokens. What LM Studio has is read from its REST
API: ``/api/v1/models`` (current), ``/api/v0/models`` (older versions), else the OpenAI model
list, which only says what is downloaded.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from starlight.core.model import Message, ModelResponse, TokenCallback
from starlight.models.openai_compat import OpenAICompatProvider, check_response

DEFAULT_URL = "http://127.0.0.1:1234/v1"
# The turn ends of the common model families (Qwen, Llama 3, Gemma). Starlight also cuts
# an answer at any other turn marker itself (output.cut_at_turn_end).
STOP = ["<|im_end|>", "<|endoftext|>", "<|eot_id|>", "<end_of_turn>"]
AUTO = ("", "auto")  # model.options.model: use whatever LM Studio has loaded
LANGUAGE = ("llm", "vlm")  # model types that chat ("vlm": vision models in the v0 API)
NO_MODEL = "load one in LM Studio, or set model.options.model to its name"
TOKEN_ENV = "LM_API_TOKEN"  # where LM Studio's own tools look for its API token
TOKEN_KEY = "lmstudio.token"  # the token's name in Starlight's vault
NEEDS_TOKEN = (
    "LM Studio asks for an API token (Require Authentication is on in its Developer tab): "
    f"set the environment variable {TOKEN_ENV} to it (Windows: setx {TOKEN_ENV} <token>), "
    f"or run `starlight vault set {TOKEN_KEY}`, then start Starlight again; or turn the "
    "setting off"
)


class LMStudioError(RuntimeError):
    """LM Studio answers, but there is no model Starlight could use."""


@asynccontextmanager
async def _refused_as_advice() -> AsyncIterator[None]:
    """LM Studio refusing for want of its API token says what to do about it."""
    try:
        yield
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise LMStudioError(NEEDS_TOKEN) from exc
        raise


@dataclass(frozen=True)
class Loaded:
    """A language model loaded in LM Studio."""

    id: str  # what the OpenAI API is asked for: the loaded instance
    context: int = 0  # the context length it is loaded with; 0: LM Studio did not say
    tools: bool | None = None  # trained for tool use; None: LM Studio did not say


@dataclass(frozen=True)
class Inventory:
    """The language models LM Studio has loaded, and all it could load (downloaded)."""

    loaded: tuple[Loaded, ...] = ()
    available: tuple[str, ...] = ()


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def parse_v1(data: Any) -> Inventory | None:
    """``GET /api/v1/models``: {"models": [{"key", "type", "capabilities",
    "loaded_instances": [{"id", "config": {"context_length"}}]}]}."""
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return None
    loaded: list[Loaded] = []
    available: list[str] = []
    for model in data["models"]:
        if not isinstance(model, dict) or model.get("type") not in LANGUAGE:
            continue
        key = str(model.get("key") or "")
        if key:
            available.append(key)
        capabilities = model.get("capabilities")
        tools = capabilities.get("trained_for_tool_use") if isinstance(capabilities, dict) else None
        for instance in model.get("loaded_instances") or []:
            if isinstance(instance, dict):
                config = instance.get("config")
                context = _int(config.get("context_length")) if isinstance(config, dict) else 0
                kind = tools if isinstance(tools, bool) else None
                loaded.append(Loaded(str(instance.get("id") or key), context, kind))
    return Inventory(tuple(loaded), tuple(available))


def parse_v0(data: Any) -> Inventory | None:
    """``GET /api/v0/models`` (older LM Studio): {"data": [{"id", "type", "state",
    "loaded_context_length"}]}."""
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        return None
    loaded: list[Loaded] = []
    available: list[str] = []
    for entry in data["data"]:
        if not isinstance(entry, dict) or entry.get("type") not in LANGUAGE:
            continue
        name = str(entry.get("id") or "")
        available.append(name)
        if entry.get("state") == "loaded":
            loaded.append(Loaded(name, _int(entry.get("loaded_context_length"))))
    return Inventory(tuple(loaded), tuple(available))


def describe(names: tuple[str, ...]) -> str:
    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    return f" (it has {shown})" if names else ""


class LMStudioProvider(OpenAICompatProvider):
    def __init__(
        self,
        model: str = "auto",
        base_url: str = DEFAULT_URL,
        context_window: int = 0,
        **options: Any,
    ) -> None:
        options.setdefault("name", "lmstudio")
        options.setdefault("stop", list(STOP))
        options.setdefault("cache_prompt", False)
        super().__init__(model=model, base_url=base_url, **options)
        self.wanted = model
        self.context_window = context_window  # for a model Starlight loads; 0: LM Studio's
        self._choosing = asyncio.Lock()  # two requests at once must not load a model twice

    @property
    def root(self) -> str:
        return self.base_url.removesuffix("/v1")

    async def inventory(self) -> Inventory | None:
        """What LM Studio has, from its REST API; None when it has none (a very old one)."""
        for path, parse in (("/api/v1/models", parse_v1), ("/api/v0/models", parse_v0)):
            try:
                response = await self._client.get(self.root + path)
                found = parse(response.json()) if response.status_code == 200 else None
            except (httpx.HTTPError, ValueError):
                found = None
            if found is not None:
                return found
        return None

    async def load(self, key: str) -> str:
        """Load a model in LM Studio with Starlight's context length; its instance id."""
        body: dict[str, Any] = {"model": key}
        if self.context_window:
            body["context_length"] = self.context_window
        response = await self._client.post(f"{self.root}/api/v1/models/load", json=body)
        await check_response(response)
        answer = response.json()
        return str(answer.get("instance_id") or key) if isinstance(answer, dict) else key

    async def loaded_model(self) -> str:
        """The model to ask (see the module docstring)."""
        found = await self.inventory()
        if self.wanted not in AUTO:
            if found is None:
                return self.wanted
            for model in found.loaded:
                if model.id == self.wanted or model.id.startswith(self.wanted + ":"):
                    return model.id
            return await self.load(self.wanted) if self.wanted in found.available else self.wanted
        if found is not None:
            if found.loaded:
                return found.loaded[0].id
            if len(found.available) == 1:
                return await self.load(found.available[0])
            raise LMStudioError(
                f"LM Studio has no language model loaded{describe(found.available)}: {NO_MODEL}"
            )
        response = await self._client.get(f"{self.base_url}/models")
        response.raise_for_status()
        listed = response.json().get("data") or []
        ids = [str(m["id"]) for m in listed if "embed" not in str(m.get("id", "")).lower()]
        if len(ids) == 1:
            return ids[0]
        if not ids:
            raise LMStudioError(f"LM Studio has no language model: {NO_MODEL}")
        raise LMStudioError(f"LM Studio lists {len(ids)} models and does not say which: {NO_MODEL}")

    async def _choose(self) -> None:
        async with self._choosing, _refused_as_advice():
            self.model = await self.loaded_model()

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        on_token: TokenCallback | None = None,
    ) -> ModelResponse:
        await self._choose()  # asked every time: you may switch models in LM Studio
        async with _refused_as_advice():
            return await super().chat(messages, tools, on_token)

    async def warmup(self) -> None:
        """LM Studio is up and has a model to use (loading one if it is clear which)."""
        await self._choose()
