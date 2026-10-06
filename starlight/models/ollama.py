"""Ollama's native API. Compared with its OpenAI endpoint it adds keep-alive (the model
stays in VRAM between requests), per-request GPU and context options, and ``/api/ps``,
which reports how much of the model sits in VRAM.

Flash attention and the quantized KV cache are server settings. Start Ollama with
``OLLAMA_FLASH_ATTENTION=1`` and ``OLLAMA_KV_CACHE_TYPE=q8_0``
(``starlight model serve`` does this for you).
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from starlight.core.model import Message, ModelResponse, TokenCallback, ToolCall
from starlight.models.common import function_tools, ollama_messages, tool_call


class OllamaProvider:
    supports_streaming = True

    def __init__(
        self,
        model: str = "qwen2.5:14b-instruct-q4_K_M",
        base_url: str = "http://127.0.0.1:11434",
        keep_alive: str | int = -1,  # -1: never unload
        num_ctx: int = 32768,
        num_gpu: int = 999,  # layers on the GPU; 999 = all
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float = 600.0,
        stream: bool = True,
        options: dict[str, Any] | None = None,
        name: str = "ollama",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive
        self.stream = stream
        self.options: dict[str, Any] = {
            "num_ctx": num_ctx,
            "num_gpu": num_gpu,
            "temperature": temperature,
            **({"num_predict": max_tokens} if max_tokens else {}),
            **(options or {}),
        }
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0), transport=transport
        )

    def _body(
        self, messages: list[Message], tools: list[dict[str, Any]], stream: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": ollama_messages(messages),
            "stream": stream,
            "keep_alive": self.keep_alive,
            "options": self.options,
        }
        if tools:
            body["tools"] = function_tools(tools)
        return body

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        on_token: TokenCallback | None = None,
    ) -> ModelResponse:
        started = time.perf_counter()
        stream = self.stream and on_token is not None
        body = self._body(messages, tools, stream)
        url = f"{self.base_url}/api/chat"
        text: list[str] = []
        calls: list[ToolCall] = []
        final: dict[str, Any] = {}
        if not stream:
            response = await self._client.post(url, json=body)
            response.raise_for_status()
            chunks = [response.json()]
        else:
            chunks = []
            async with self._client.stream("POST", url, json=body) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    content = (chunk.get("message") or {}).get("content")
                    if content and on_token is not None:
                        on_token(content)
                    chunks.append(chunk)
        for chunk in chunks:
            if chunk.get("error"):
                raise RuntimeError(f"ollama: {chunk['error']}")
            message = chunk.get("message") or {}
            text.append(message.get("content") or "")
            calls += [
                tool_call(c["function"]["name"], c["function"].get("arguments"))
                for c in message.get("tool_calls") or []
            ]
            if chunk.get("done"):
                final = chunk
        usage = _usage(final)
        usage.setdefault("duration_s", round(time.perf_counter() - started, 3))
        usage["model"] = self.model
        return ModelResponse("".join(text), calls, usage)

    async def warmup(self) -> None:
        """Load the model into VRAM now rather than on the first request."""
        response = await self._client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [],
                "keep_alive": self.keep_alive,
                "options": self.options,
            },
        )
        response.raise_for_status()

    async def vram(self) -> dict[str, Any] | None:
        """How much of this model is in VRAM, from /api/ps."""
        response = await self._client.get(f"{self.base_url}/api/ps")
        response.raise_for_status()
        for entry in response.json().get("models") or []:
            if entry.get("name") == self.model or entry.get("model") == self.model:
                size, in_vram = entry.get("size") or 0, entry.get("size_vram") or 0
                return {
                    "model_bytes": size,
                    "vram_bytes": in_vram,
                    "gpu_fraction": round(in_vram / size, 3) if size else None,
                }
        return None

    async def aclose(self) -> None:
        await self._client.aclose()


def _usage(final: dict[str, Any]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    if "eval_count" in final:
        usage["completion_tokens"] = final["eval_count"]
        if final.get("eval_duration"):
            usage["tokens_per_second"] = round(
                final["eval_count"] / final["eval_duration"] * 1e9, 2
            )
    if "prompt_eval_count" in final:
        usage["prompt_tokens"] = final["prompt_eval_count"]
        if final.get("prompt_eval_duration"):
            usage["prompt_tokens_per_second"] = round(
                final["prompt_eval_count"] / final["prompt_eval_duration"] * 1e9, 2
            )
    return usage
