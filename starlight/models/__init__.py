"""Model providers (Step 4). Registered by name for ``model.provider`` in the config:

* ``ollama``         Ollama's native API (keep-alive, GPU layers, VRAM report)
* ``llamacpp``       llama.cpp's ``llama-server`` (OpenAI-compatible API)
* ``lmstudio``       LM Studio's local server (it loads and runs the model itself)
* ``openai_compat``  any other local OpenAI-compatible server (vLLM ...)
* ``router``         a small fast model for simple requests, a large one for reasoning
"""

from __future__ import annotations

from typing import Any

from starlight.core.model import ModelProvider, register_provider
from starlight.models.lmstudio import LMStudioProvider
from starlight.models.ollama import OllamaProvider
from starlight.models.openai_compat import OpenAICompatProvider
from starlight.models.router import RouterProvider

# llama-server's own sampling extensions. Qwen's recommended values: they keep a model from
# drifting into other languages or repeating itself. model.options.extra overrides them.
LLAMACPP_SAMPLING: dict[str, Any] = {"top_p": 0.8, "top_k": 20, "repeat_penalty": 1.05}


def _llamacpp(options: dict[str, Any]) -> ModelProvider:
    options = {"base_url": "http://127.0.0.1:8080/v1", **options}
    options["extra"] = {**LLAMACPP_SAMPLING, **(options.get("extra") or {})}
    return OpenAICompatProvider(**options)


def _lmstudio(options: dict[str, Any]) -> ModelProvider:
    options = dict(options)
    # LM Studio takes the same sampling settings (top_k and repeat_penalty included).
    options["extra"] = {**LLAMACPP_SAMPLING, **(options.get("extra") or {})}
    return LMStudioProvider(**options)


register_provider("ollama", lambda options: OllamaProvider(**options), max_tokens=True)
register_provider("lmstudio", _lmstudio, max_tokens=True, context=True)
register_provider("llamacpp", _llamacpp, max_tokens=True)
register_provider(
    "openai_compat",
    lambda options: OpenAICompatProvider(**{"name": "openai_compat", **options}),
    max_tokens=True,
)
register_provider("router", RouterProvider.from_options)

__all__ = ["LMStudioProvider", "OllamaProvider", "OpenAICompatProvider", "RouterProvider"]
