"""Step 4: model providers behind one interface, long conversations, routing, benchmark."""

from __future__ import annotations

import json
from typing import Any

import httpx

from starlight.core import Kernel, Paths
from starlight.core.config import ModelConfig
from starlight.core.context import SUMMARY_PREFIX, ContextManager
from starlight.core.model import Message, ModelResponse, ToolCall, create_provider
from starlight.core.stats import Stats
from starlight.models import OllamaProvider, OpenAICompatProvider, RouterProvider
from starlight.models.bench import benchmark
from starlight.models.serve import serve_command
from tests.conftest import ScriptedProvider

TOOLS = [{"name": "hello.greet", "description": "Greet", "parameters": {"type": "object"}}]


def sse(*chunks: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


async def test_openai_compat_tool_calls_and_timings() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "hello__greet",
                                        "arguments": '{"name": "Paul"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 30, "completion_tokens": 5},
                "timings": {"predicted_per_second": 61.5, "prompt_per_second": 900.0},
            },
        )

    provider = OpenAICompatProvider(model="m", transport=httpx.MockTransport(handler))
    png = b"\x89PNG\r\n\x1a\nxx"
    response = await provider.chat([Message("user", "hi", images=[png])], TOOLS)
    await provider.aclose()

    assert response.tool_calls[0].name == "hello.greet"
    assert response.tool_calls[0].arguments == {"name": "Paul"}
    assert response.usage["tokens_per_second"] == 61.5
    body = seen[0]
    assert body["tools"][0]["function"]["name"] == "hello__greet"  # dots not allowed
    assert body["cache_prompt"] is True  # reuse the cached system prompt + tools prefix
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


async def test_openai_compat_streaming() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(
            200,
            content=sse(
                {"choices": [{"delta": {"content": "Hel"}}]},
                {"choices": [{"delta": {"content": "lo"}}]},
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "a",
                                        "function": {"name": "hello__greet", "arguments": '{"na'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [{"index": 0, "function": {"arguments": 'me": "X"}'}}]
                            }
                        }
                    ]
                },
                {
                    "choices": [],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                    "timings": {"predicted_per_second": 40},
                },
            ),
        )

    provider = OpenAICompatProvider(transport=httpx.MockTransport(handler))
    tokens: list[str] = []
    response = await provider.chat([Message("user", "hi")], TOOLS, on_token=tokens.append)
    await provider.aclose()
    assert tokens == ["Hel", "lo"] and response.content == "Hello"
    assert response.tool_calls == [ToolCall("hello.greet", {"name": "X"}, id="a")]
    assert response.usage["completion_tokens"] == 2


async def test_ollama_keeps_model_on_gpu_and_reports_vram() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [{"name": "qwen", "size": 9_000_000_000, "size_vram": 9_000_000_000}]
                },
            )
        bodies.append(json.loads(request.content))
        lines = [
            {"message": {"content": "Hi "}, "done": False},
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "hello__greet", "arguments": {"name": "P"}}}
                    ],
                },
                "done": False,
            },
            {
                "message": {"content": "there"},
                "done": True,
                "eval_count": 100,
                "eval_duration": 2_000_000_000,
                "prompt_eval_count": 50,
                "prompt_eval_duration": 100_000_000,
            },
        ]
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    provider = OllamaProvider(model="qwen", transport=httpx.MockTransport(handler))
    tokens: list[str] = []
    response = await provider.chat([Message("user", "hi")], TOOLS, on_token=tokens.append)
    vram = await provider.vram()
    await provider.aclose()

    assert response.content == "Hi there" and tokens == ["Hi ", "there"]
    assert response.tool_calls[0].name == "hello.greet"
    assert response.usage["tokens_per_second"] == 50.0
    assert response.usage["prompt_tokens_per_second"] == 500.0
    assert bodies[0]["keep_alive"] == -1  # stay loaded between requests
    assert bodies[0]["options"]["num_gpu"] == 999  # every layer on the GPU
    assert bodies[0]["options"]["num_ctx"] == 32768
    assert vram == {"model_bytes": 9_000_000_000, "vram_bytes": 9_000_000_000, "gpu_fraction": 1.0}


def test_provider_registry() -> None:
    provider = create_provider(ModelConfig(provider="ollama", options={"model": "x"}))
    assert isinstance(provider, OllamaProvider) and provider.model == "x"
    provider = create_provider(ModelConfig(provider="llamacpp"))
    assert isinstance(provider, OpenAICompatProvider)
    assert provider.base_url == "http://127.0.0.1:8080/v1"
    router = create_provider(
        ModelConfig(
            provider="router",
            options={
                "fast": {"provider": "ollama", "options": {"model": "small"}},
                "main": {"provider": "ollama", "options": {"model": "big"}},
            },
        )
    )
    assert isinstance(router, RouterProvider)


async def test_router_sends_simple_requests_to_the_fast_model() -> None:
    fast = ScriptedProvider("SIMPLE", "hi from fast", "COMPLEX")
    main = ScriptedProvider(ModelResponse(tool_calls=[ToolCall("x.y", {})]), "main answer")
    router = RouterProvider(fast, main)  # type: ignore[arg-type]

    simple = await router.chat([Message("user", "hello")], [])
    assert simple.content == "hi from fast" and simple.usage["routed_to"] == "fast"

    first = await router.chat([Message("user", "plan my backups")], [])
    assert first.tool_calls and first.usage["routed_to"] == "main"
    # the tool result step of the same request stays on the main model
    second = await router.chat(
        [Message("user", "plan my backups"), Message("tool", "ok", name="x.y")], []
    )
    assert second.content == "main answer"


async def test_context_is_summarized_not_cut() -> None:
    summarizer = ScriptedProvider("They talked about backups and the NAS at 10.0.0.5.")
    summaries: list[str] = []

    async def keep(summary: str) -> None:
        summaries.append(summary)

    config = ModelConfig(context_window=1024, reserve_tokens=256, keep_recent=4)
    manager = ContextManager(summarizer, config, keep)  # type: ignore[arg-type]
    messages = [Message("system", "You are Starlight.")]
    for i in range(30):
        messages.append(Message("user", f"question {i} " + "x" * 120))
        messages.append(Message("assistant", "", tool_calls=[ToolCall("a.b", {"i": i})]))
        messages.append(Message("tool", "result " + "y" * 60, name="a.b"))
        messages.append(Message("assistant", f"answer {i}"))

    fitted = await manager.fit(messages, [])
    assert fitted[0].content == "You are Starlight."
    assert fitted[1].content.startswith(SUMMARY_PREFIX)
    assert "NAS at 10.0.0.5" in fitted[1].content
    assert fitted[-1].content == "answer 29"
    assert fitted[2].role != "tool"  # never a tool result without its call
    assert len(fitted) < len(messages)
    assert summaries and "question 0" in summarizer.calls[0][1].content  # type: ignore[attr-defined]


async def test_summary_goes_to_memory_and_agent_streams(paths: Paths) -> None:
    paths.config_file.write_text(
        paths.config_file.read_text()
        + "model:\n  context_window: 1024\n  reserve_tokens: 256\n  keep_recent: 2\n"
    )

    class Streaming(ScriptedProvider):
        supports_streaming = True

        async def chat(self, messages: Any, tools: Any, on_token: Any = None) -> Any:
            response = await super().chat(messages, tools)
            if on_token is not None:
                for word in response.content.split(" "):
                    on_token(word)
            return response

    provider = Streaming(*["reply " + "z" * 300] * 6, "short summary", "final")
    async with Kernel(paths, provider=provider) as kernel:
        tokens: list[str] = []
        for i in range(6):
            await kernel.ask(f"turn {i} " + "q" * 400, keep_history=True, on_token=tokens.append)
        assert tokens[:1] == ["reply"]
        summaries = list((paths.memory / "conversations").glob("*.md"))
        assert summaries, "summary should be written to memory/conversations"
        assert any(m.content.startswith(SUMMARY_PREFIX) for m in kernel.history)


def test_serve_commands(paths: Paths) -> None:
    config = ModelConfig(provider="llamacpp")
    config.server.binary = "llama-server"
    config.server.model_path = "C:/models/qwen2.5-14b-instruct-q4_k_m.gguf"
    cmd, env = serve_command(config)
    joined = " ".join(cmd)
    assert "--n-gpu-layers 999" in joined and "--flash-attn on" in joined
    assert "--cache-type-k q8_0 --cache-type-v q8_0" in joined
    assert "--ctx-size 32768" in joined and "--jinja" in joined and env == {}
    assert "--threads" in cmd  # all CPU cores by default (keeps the CPU build from crawling)

    threaded = ModelConfig(provider="llamacpp")
    threaded.server.binary = "llama-server"
    threaded.server.model_path = "m.gguf"
    threaded.server.threads = 6
    assert "--threads 6" in " ".join(serve_command(threaded)[0])

    ollama = ModelConfig(provider="ollama")
    ollama.server.binary = "ollama"
    cmd, env = serve_command(ollama)
    assert cmd[-1] == "serve"
    assert env["OLLAMA_FLASH_ATTENTION"] == "1" and env["OLLAMA_KV_CACHE_TYPE"] == "q8_0"
    assert env["OLLAMA_KEEP_ALIVE"] == "-1"


async def test_benchmark_records_stats() -> None:
    class Fake(ScriptedProvider):
        supports_streaming = True

        async def chat(self, messages: Any, tools: Any, on_token: Any = None) -> Any:
            if on_token:
                on_token("a")
            return ModelResponse("a b c", usage={"completion_tokens": 3, "tokens_per_second": 42.0})

        async def vram(self) -> dict[str, Any]:
            return {"gpu_fraction": 1.0}

    stats = Stats(None)
    report = await benchmark(Fake(), runs=2, stats=stats)  # type: ignore[arg-type]
    assert report["tokens_per_second"] == 42.0
    assert report["full_gpu_offload"] is True
    assert stats.query("model.bench.tokens_per_second")[0][1] == 42.0
    stats.close()


async def test_model_server_down_is_a_clear_answer(paths: Paths) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = OllamaProvider(model="qwen", transport=httpx.MockTransport(refuse))
    async with Kernel(paths, provider=provider) as kernel:
        result = await kernel.ask("hello?")
    assert "not available" in result.answer and "starlight model serve" in result.answer
