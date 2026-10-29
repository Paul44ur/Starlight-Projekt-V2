"""OpenAI-compatible chat API, as served by llama.cpp's ``llama-server``.

Start llama-server with every layer on the GPU, flash attention and a q8 KV cache
(``starlight model serve`` prints and runs this command for you):

    llama-server -m model.gguf -ngl 999 -fa on -c 32768 \\
        --cache-type-k q8_0 --cache-type-v q8_0 --jinja --port 8080

``--jinja`` enables tool calling. llama-server keeps the model loaded and reuses the
cached prompt prefix (system prompt + tool list) between requests.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from starlight.core.model import Message, ModelResponse, TokenCallback, ToolCall
from starlight.models.common import function_tools, openai_messages, tool_call
from starlight.models.output import (
    TURN_MARKERS,
    StreamFilter,
    cut_at_turn_end,
    recover_tool_calls,
)


class OpenAICompatProvider:
    supports_streaming = True

    def __init__(
        self,
        model: str = "local",
        base_url: str = "http://127.0.0.1:8080/v1",
        api_key: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        timeout: float = 600.0,
        idle_timeout: float = 90.0,
        stream: bool = True,
        extra: dict[str, Any] | None = None,
        name: str = "llamacpp",
        transport: httpx.AsyncBaseTransport | None = None,
        stop: list[str] | None = None,
        cache_prompt: bool = True,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.stream = stream
        # Once the answer has begun, this many seconds without a new piece mean the model
        # server is stuck (a driver hang, out of memory); what came so far is kept.
        self.idle_timeout = idle_timeout
        # End the reply at the end-of-turn marker even when the model file does not mark it
        # as a stop (the model would otherwise write the user's next message itself).
        self.stop = list(TURN_MARKERS) if stop is None else stop
        # cache_prompt: llama-server reuses the KV cache for the shared prompt prefix (other
        # servers, such as LM Studio, do not know it: the lmstudio provider leaves it out).
        self.extra = {**({"cache_prompt": True} if cache_prompt else {}), **(extra or {})}
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0), headers=headers, transport=transport
        )

    def _body(
        self, messages: list[Message], tools: list[dict[str, Any]], stream: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": openai_messages(messages),
            "temperature": self.temperature,
            "stream": stream,
            **self.extra,
        }
        if tools:
            body["tools"] = function_tools(tools)
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        if self.stop:
            body["stop"] = self.stop
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    async def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        on_token: TokenCallback | None = None,
    ) -> ModelResponse:
        started = time.perf_counter()
        stream = self.stream and on_token is not None
        url = f"{self.base_url}/chat/completions"
        body = self._body(messages, tools, stream)
        known = {t["name"] for t in tools}
        if not stream:
            response = await self._client.post(url, json=body)
            await check_response(response)
            result = self._parse(response.json())
            result.content = cut_at_turn_end(result.content)
            if not result.tool_calls and known:
                calls, text = recover_tool_calls(result.content, known)
                if calls:
                    result.tool_calls, result.content = calls, text
        else:
            result = await self._stream(url, body, on_token, known)
        result.usage.setdefault("duration_s", round(time.perf_counter() - started, 3))
        result.usage.setdefault("model", self.model)
        return result

    def _parse(self, data: dict[str, Any]) -> ModelResponse:
        choice = data["choices"][0]
        message = choice["message"]
        calls = [
            tool_call(c["function"]["name"], c["function"].get("arguments"), c.get("id"))
            for c in message.get("tool_calls") or []
        ]
        usage = _usage(data)
        if choice.get("finish_reason"):
            usage["finish_reason"] = choice["finish_reason"]
        return ModelResponse(message.get("content") or "", calls, usage)

    async def _stream(
        self,
        url: str,
        body: dict[str, Any],
        on_token: TokenCallback | None,
        known: set[str],
    ) -> ModelResponse:
        shown = StreamFilter()  # what of the reply may be shown while it streams
        calls: dict[int, dict[str, str]] = {}
        usage: dict[str, Any] = {}
        began = False  # the first piece of the answer arrived (before that: prompt reading)
        first_at = 0.0  # when it arrived: what came after is the writing speed
        stalled = False
        async with self._client.stream("POST", url, json=body) as response:
            await check_response(response)
            lines = response.aiter_lines().__aiter__()
            while True:
                try:
                    if began:
                        line = await asyncio.wait_for(lines.__anext__(), self.idle_timeout)
                    else:
                        line = await lines.__anext__()
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    stalled = True
                    break
                if not line.startswith("data:"):
                    continue
                if not began:
                    first_at = time.perf_counter()
                began = True
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                chunk = json.loads(payload)
                usage.update(_usage(chunk))
                for choice in chunk.get("choices") or []:
                    if choice.get("finish_reason"):
                        usage["finish_reason"] = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        visible = shown.feed(delta["content"])
                        if visible and on_token is not None:
                            on_token(visible)
                    for part in delta.get("tool_calls") or []:
                        slot = calls.setdefault(
                            part.get("index", 0), {"id": "", "name": "", "arguments": ""}
                        )
                        slot["id"] = part.get("id") or slot["id"]
                        function = part.get("function") or {}
                        slot["name"] += function.get("name") or ""
                        slot["arguments"] += function.get("arguments") or ""
                if shown.ended or shown.call_finished(known):
                    # The turn is over but the model keeps writing: stop reading. Closing the
                    # stream makes llama-server stop generating too.
                    usage["finish_reason"] = "stop"
                    break
        if stalled:
            usage["finish_reason"] = "stalled"
            calls = {}  # a half-written call must not run
        writing = time.perf_counter() - first_at
        if "tokens_per_second" not in usage and usage.get("completion_tokens") and writing > 0:
            # Only llama-server reports its speed (timings); for others (LM Studio, vLLM)
            # it is the tokens written over the time from the first piece to the last.
            usage["tokens_per_second"] = round(usage["completion_tokens"] / writing, 2)
        tool_calls: list[ToolCall] = [
            tool_call(c["name"], c["arguments"], c["id"] or None) for _, c in sorted(calls.items())
        ]
        content = shown.text
        if not tool_calls and known:
            recovered, before = recover_tool_calls(content, known)
            if recovered:
                return ModelResponse(before, recovered, usage)
        rest = shown.held()  # held back in case it was a tool call; it was not
        if rest and on_token is not None:
            on_token(rest)
        return ModelResponse(content, tool_calls, usage)

    async def warmup(self) -> None:
        """llama-server loads the model at start; just check it is up."""
        root = self.base_url.removesuffix("/v1")
        response = await self._client.get(f"{root}/health")
        if response.status_code == 404:  # no /health: LM Studio, vLLM ... ask for the models
            response = await self._client.get(f"{self.base_url}/models")
        response.raise_for_status()

    async def aclose(self) -> None:
        await self._client.aclose()


def _error_text(response: httpx.Response) -> str:
    """The server's own reason for a failed request, short ("" when it gave none). The
    OpenAI format is {"error": {"message": ...}}; some servers send {"error": "..."}."""
    try:
        data = response.json()
    except ValueError:
        return response.text.strip()[:300]
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        error = error.get("message")
    return str(error or "").strip()[:300]


async def check_response(response: httpx.Response) -> None:
    """Like raise_for_status, with the server's reason in the message (say, LM Studio's
    "no model loaded" or a prompt longer than the loaded context)."""
    if response.is_success:
        return
    await response.aread()
    reason = _error_text(response)
    message = f"the model server answered HTTP {response.status_code}"
    raise httpx.HTTPStatusError(
        message + (f": {reason}" if reason else ""), request=response.request, response=response
    )


def _usage(data: dict[str, Any]) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    if data.get("usage"):
        usage["prompt_tokens"] = data["usage"].get("prompt_tokens")
        usage["completion_tokens"] = data["usage"].get("completion_tokens")
    timings = data.get("timings")  # llama-server extension
    if timings:
        usage["tokens_per_second"] = round(timings.get("predicted_per_second") or 0, 2)
        usage["prompt_tokens_per_second"] = round(timings.get("prompt_per_second") or 0, 2)
        usage.setdefault("completion_tokens", timings.get("predicted_n"))
        usage.setdefault("prompt_tokens", timings.get("prompt_n"))
    return {k: v for k, v in usage.items() if v is not None}
