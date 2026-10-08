"""Model replies stay clean even when the model file is faulty: the reply ends at the
end-of-turn marker instead of inventing the user's next message, and tool calls written as
text (```json blocks, <tool_call> tags) are run instead of shown. Both cases come from a real
session with a Qwen2.5 14B "uncensored" GGUF on llama.cpp."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from starlight.core import Kernel, Paths
from starlight.core.config import ModelConfig
from starlight.core.model import Message, ToolCall, create_provider
from starlight.models.openai_compat import OpenAICompatProvider
from starlight.models.output import StreamFilter, cut_at_turn_end, recover_tool_calls

TOOLS = [
    {"name": "hello.greet", "description": "Greet", "parameters": {"type": "object"}},
    {"name": "weather.forecast", "description": "Forecast", "parameters": {"type": "object"}},
]
KNOWN = {t["name"] for t in TOOLS}

GREET_BLOCK = '```json\n{\n  "name": "hello__greet",\n  "arguments": {"name": "Paul"}\n}\n```'
RUNAWAY = (
    "\nNdrFcShort\nI want to know the weather for tomorrow.\n"
    '```json\n{"name": "weather__forecast", "arguments": {"place": "", "days": 1}}\n```'
)


def sse_text(*pieces: str) -> bytes:
    chunks = [{"choices": [{"delta": {"content": p}}]} for p in pieces]
    lines = [f"data: {json.dumps(c)}\n\n" for c in chunks] + ["data: [DONE]\n\n"]
    return "".join(lines).encode()


def provider(*pieces: str, seen: list[dict[str, Any]] | None = None) -> OpenAICompatProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        return httpx.Response(200, content=sse_text(*pieces))

    return OpenAICompatProvider(transport=httpx.MockTransport(handler))


# -- the end of the turn -------------------------------------------------------------------


async def test_reply_ends_where_the_model_starts_writing_the_users_part() -> None:
    seen: list[dict[str, Any]] = []
    p = provider(
        "Hello! I'm your local assistant.",
        " Let's get started!�",
        "\n",
        "user",
        "\nWhat's the weather like today? I'm planning a picnic.",
        " And remind me to buy sunscreen. And remind me to buy sunscreen.",
        seen=seen,
    )
    tokens: list[str] = []
    response = await p.chat([Message("user", "Hello")], TOOLS, on_token=tokens.append)
    await p.aclose()

    answer = "Hello! I'm your local assistant. Let's get started!"
    assert response.content == answer
    assert "".join(tokens) == answer  # the invented user turn was never shown
    assert "\nuser\n" in seen[0]["stop"] and "<|im_end|>" in seen[0]["stop"]


def test_stream_filter_holds_back_a_split_marker() -> None:
    f = StreamFilter()
    shown = [f.feed(p) for p in ("Done.", "\n", "us", "er", "\n", "Next question?")]
    assert "".join(shown) == "Done." and f.ended


async def test_a_turn_end_shown_as_a_stray_symbol_still_ends_the_reply() -> None:
    """From a real session: the leaked turn token came out as a symbol before "user", and
    the invented user message then repeated itself until the length limit."""
    loop = (
        "And one more thing, I want to make sure I have all the necessary documents for the"
        " meeting. Could you check if I've saved them in the right folder? "
    )
    p = provider(
        "Hello! How can I assist you today?",
        " Let's make this productive!",
        "\n",
        "\U0001f402",
        "user",
        "\nI want to know what the weather is like in Berlin tomorrow. ",
        *([loop] * 6),
    )
    tokens: list[str] = []
    response = await p.chat([Message("user", "Hello")], TOOLS, on_token=tokens.append)
    await p.aclose()
    answer = "Hello! How can I assist you today? Let's make this productive!"
    assert response.content == answer
    assert "".join(tokens) == answer


async def test_a_reply_that_repeats_itself_is_cut_after_the_first_copy() -> None:
    loop = (
        "And while we're at it, could you please remind me about the weather in Berlin "
        "tomorrow? I'm really looking forward to it! Thanks again! "
    )
    p = provider("Sure. ", *([loop] * 8))
    response = await p.chat([Message("user", "hi")], TOOLS, on_token=lambda _: None)
    await p.aclose()
    assert response.content.count("really looking forward") == 1
    assert response.content.startswith("Sure. And while")


def test_role_words_inside_text_are_not_a_turn_end() -> None:
    text = "The user\nwas added. Run it as user:\nsudo -u user ls\n```\nuser\n```"
    assert cut_at_turn_end("Fine.\n\u2022 user\nmore") == "Fine."
    assert cut_at_turn_end("Before\n user \nafter") == "Before"
    assert cut_at_turn_end(text).startswith("The user\nwas added. Run it as user:")


def test_cut_at_turn_end() -> None:
    assert cut_at_turn_end("Hi there<|im_end|>\n<|im_start|>user\nmore") == "Hi there"
    assert cut_at_turn_end("No marker here.") == "No marker here."


async def test_normal_answers_still_stream_token_by_token() -> None:
    p = provider("Hel", "lo ", "world", "\n", "```python\nprint(1)\n```")
    tokens: list[str] = []
    response = await p.chat([Message("user", "hi")], TOOLS, on_token=tokens.append)
    await p.aclose()
    assert response.content == "Hello world\n```python\nprint(1)\n```"
    assert "".join(tokens) == response.content and tokens[0] == "Hel"


# -- tool calls written as text ------------------------------------------------------------


def test_recover_takes_the_first_call_and_drops_the_runaway() -> None:
    calls, before = recover_tool_calls(GREET_BLOCK + RUNAWAY, KNOWN)
    assert calls == [ToolCall("hello.greet", {"name": "Paul"}, id=calls[0].id)]
    assert before == ""  # the invented "weather" request after it is not run


def test_recover_tags_bare_json_and_parallel_calls() -> None:
    tagged = (
        "Let me check.\n<tool_call>\n"
        '{"name": "weather__forecast", "arguments": {"days": 2}}\n</tool_call>'
    )
    calls, before = recover_tool_calls(tagged, KNOWN)
    assert [c.name for c in calls] == ["weather.forecast"] and calls[0].arguments == {"days": 2}
    assert before == "Let me check."

    bare = '{"name": "hello.greet", "arguments": {"name": "A"}}'
    assert [c.name for c in recover_tool_calls(bare, KNOWN)[0]] == ["hello.greet"]

    two = GREET_BLOCK + "\n" + GREET_BLOCK.replace("Paul", "Sue")
    assert [c.arguments["name"] for c in recover_tool_calls(two, KNOWN)[0]] == ["Paul", "Sue"]


def test_unknown_tools_and_plain_json_stay_text() -> None:
    text = '```json\n{"name": "rm__everything", "arguments": {}}\n```'
    assert recover_tool_calls(text, KNOWN) == ([], text)
    data = '```json\n{"city": "Vienna", "temp": 12}\n```'
    assert recover_tool_calls(data, KNOWN) == ([], data)


async def test_streamed_text_tool_call_is_run_not_shown() -> None:
    p = provider(
        "```json\n{\n",
        '  "name": "hello__greet",\n',
        '  "arguments": {"name": "Paul"}\n}\n```',
        RUNAWAY,
    )
    tokens: list[str] = []
    response = await p.chat([Message("user", "greet Paul")], TOOLS, on_token=tokens.append)
    await p.aclose()
    assert [c.name for c in response.tool_calls] == ["hello.greet"]
    assert response.tool_calls[0].arguments == {"name": "Paul"}
    assert tokens == [] and response.content == ""  # no raw JSON in the chat


async def test_a_json_answer_that_is_no_tool_call_is_still_shown() -> None:
    reply = '```json\n{"city": "Vienna", "temp": 12}\n```'
    p = provider(reply[:9], reply[9:])
    tokens: list[str] = []
    response = await p.chat([Message("user", "json please")], TOOLS, on_token=tokens.append)
    await p.aclose()
    assert response.tool_calls == [] and response.content == reply
    assert "".join(tokens) == reply  # held while it might have been a call, then shown


async def test_non_streamed_reply_is_cleaned_too() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = GREET_BLOCK + RUNAWAY
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    p = OpenAICompatProvider(transport=httpx.MockTransport(handler))
    response = await p.chat([Message("user", "hi")], TOOLS)
    await p.aclose()
    assert [c.name for c in response.tool_calls] == ["hello.greet"] and response.content == ""


# -- sampling ------------------------------------------------------------------------------


async def test_llamacpp_sends_sampling_that_curbs_loops() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    transport = httpx.MockTransport(handler)
    p = create_provider(ModelConfig(provider="llamacpp", options={"transport": transport}))
    await p.chat([Message("user", "hi")], [])
    custom = create_provider(
        ModelConfig(provider="llamacpp", options={"transport": transport, "extra": {"top_k": 50}})
    )
    await custom.chat([Message("user", "hi")], [])
    assert seen[0]["top_k"] == 20 and seen[0]["top_p"] == 0.8 and seen[0]["repeat_penalty"] == 1.05
    assert seen[1]["top_k"] == 50  # the user's own setting wins


# -- through the whole agent ---------------------------------------------------------------


async def test_agent_runs_a_text_tool_call_then_answers(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    """The second screenshot, end to end: the call is run (once), the invented follow-up
    request is not, and the chat only ever shows the final answer."""
    use_module("hello")
    replies = iter([GREET_BLOCK + RUNAWAY, "Done - I greeted Paul."])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):  # the start-up check
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, content=sse_text(next(replies)))

    model = OpenAICompatProvider(transport=httpx.MockTransport(handler))
    tokens: list[str] = []
    async with Kernel(paths, provider=model) as k:
        result = await k.ask("greet Paul", on_token=tokens.append)
    assert [r.tool for r in result.tool_results] == ["hello.greet"]
    assert result.tool_results[0].ok and "Paul" in str(result.tool_results[0].output)
    assert result.answer == "Done - I greeted Paul."
    assert "".join(tokens) == "Done - I greeted Paul."  # no raw JSON, no invented request
