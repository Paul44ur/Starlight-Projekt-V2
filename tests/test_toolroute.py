"""Only the tools a request needs go to the model; tools.find brings in more; long tool
results are cut before the model sees them; module code is compiled once, not on every
start."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest

from starlight.core import Kernel, Paths, ToolCall
from starlight.core.model import Message, ModelResponse
from starlight.core.toolroute import stem, words
from tests.conftest import ModuleWriter

MODULES = ("weather", "reminders", "todo", "calc", "apps", "recon", "media", "home", "mail")


class RecordingProvider:
    """Plays back responses and records the tool names it was offered each time."""

    name = "recording"

    def __init__(self, *responses: ModelResponse) -> None:
        self.responses = list(responses)
        self.offered: list[set[str]] = []
        self.sent: list[list[Message]] = []

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        self.offered.append({t["name"] for t in tools})
        self.sent.append(list(messages))
        return self.responses.pop(0) if self.responses else ModelResponse(content="done")


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in MODULES:
        use_module(name)
    return paths


def test_words_are_stemmed_and_stop_words_dropped() -> None:
    assert stem("reminders") == stem("reminder") == stem("remind")
    assert stem("scanning") == stem("scan")
    assert words("What's the weather in Vienna?") == ["weath", "vienna"]
    assert "bitte" not in words("Wie wird das Wetter bitte")


@pytest.mark.parametrize(
    ("request_text", "module"),
    [
        ("what's the weather in Vienna", "weather"),
        ("wie wird das Wetter morgen", "weather"),
        ("remind me tomorrow at 9 to call mum", "reminders"),
        ("Erinnere mich morgen an den Arzttermin", "reminders"),
        ("add milk to my shopping list", "todo"),
        ("what's 17% of 2340", "calc"),
        ("open spotify", "apps"),
        ("scan 192.168.1.10", "recon"),
        ("play some music", "media"),
        ("turn off the living room light", "home"),
        ("check my email", "mail"),
    ],
)
async def test_the_fitting_module_is_chosen(project: Paths, request_text: str, module: str) -> None:
    async with Kernel(project) as k:
        selection = await k.router.select(request_text)
        assert module in selection.modules[:2], selection.modules
        assert any(n.startswith(module + ".") for n in selection.names)
        assert len(selection.names) <= k.config.agent.max_tools
        all_tools = {t.qualified_name for t in k.dispatcher.tools()}
        assert len(selection.names) < len(all_tools) / 2
        for always in ("memory.recall", "memory.remember", "tools.find"):
            assert always in selection.names


async def test_small_talk_gets_almost_no_tools(project: Paths) -> None:
    async with Kernel(project) as k:
        selection = await k.router.select("hi, how are you?")
        assert selection.modules == []
        assert set(selection.names) <= {"memory.recall", "memory.remember", "tools.find"}


async def test_the_model_sees_only_the_selected_tools(project: Paths) -> None:
    provider = RecordingProvider()
    async with Kernel(project, provider=provider) as k:
        await k.ask("what's the weather in Vienna")
        offered = provider.offered[0]
        assert "weather.now" in offered and "recon.scan" not in offered
        assert offered == set(sorted(offered))  # sorted by name: prompt prefix caching


async def test_tools_find_adds_tools_for_the_rest_of_the_request(project: Paths) -> None:
    provider = RecordingProvider(
        ModelResponse(tool_calls=[ToolCall("tools.find", {"need": "port scan of a host"})]),
        ModelResponse(content="found it"),
    )
    async with Kernel(project, provider=provider) as k:
        find = k.dispatcher.get("tools.find")
        assert find is not None and "recon" in find.json_schema()["description"]
        result = await k.ask("hi")
        assert "recon.scan" not in provider.offered[0]
        assert "recon.scan" in provider.offered[1]  # offered after tools.find
        found = result.tool_results[0].output
        assert any(item["tool"] == "recon.scan" for item in found)
        await k.ask("hi again")
        assert "recon.scan" not in provider.offered[2]  # a new request starts fresh


async def test_tools_used_earlier_in_the_conversation_stay(project: Paths) -> None:
    provider = RecordingProvider(
        ModelResponse(tool_calls=[ToolCall("calc.calculate", {"expression": "2+2"})]),
        ModelResponse(content="4"),
        ModelResponse(content="ok"),
    )
    async with Kernel(project, provider=provider) as k:
        await k.ask("what's 2+2", keep_history=True)
        await k.ask("and that times three?", keep_history=True)
        assert "calc.calculate" in provider.offered[-1]


async def test_named_tools_and_routing_off(project: Paths) -> None:
    async with Kernel(project) as k:  # the echo model calls !tool.name directly
        result = await k.ask('!calc.calculate {"expression": "6*7"}')
        assert "42" in result.answer
    project.config_file.write_text(
        project.config_file.read_text() + "agent:\n  tool_routing: false\n"
    )
    provider = RecordingProvider()
    async with Kernel(project, provider=provider) as k:
        await k.ask("hi")
        assert provider.offered[0] == {t.qualified_name for t in k.dispatcher.tools()}


BIG = """
    from starlight.core import Module, tool

    class Mod(Module):
        @tool
        def dump(self) -> str:
            "A very long result."
            return "x" * 50_000
"""


async def test_long_tool_results_are_cut_for_the_model(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module("big", BIG)
    paths.config_file.write_text(
        paths.config_file.read_text() + "tools:\n  max_result_chars: 1000\n"
    )
    provider = RecordingProvider(
        ModelResponse(tool_calls=[ToolCall("big.dump", {})]), ModelResponse(content="ok")
    )
    async with Kernel(paths, provider=provider) as k:
        result = await k.ask("dump it")
        assert len(result.tool_results[0].output) == 50_000  # kept whole
        seen = provider.sent[1][-1]
        assert seen.role == "tool"
        assert seen.content.startswith("x" * 1000)
        assert "49000 more characters" in seen.content
        assert len(seen.content) < 1200


async def test_compiled_module_code_is_kept_and_checked_by_hash(
    paths: Paths, write_module: ModuleWriter
) -> None:
    folder = write_module("big", BIG)
    async with Kernel(paths) as k:
        assert k.modules.records["big"].instance is not None
        cached = Path(importlib.util.cache_from_source(str(folder / "module.py")))
        header = cached.read_bytes()[:8]
        assert int.from_bytes(header[4:8], "little") == 0b11  # hash-based, checked
        (folder / "module.py").write_text(dedent(BIG.replace("50_000", "10_000")))
        await k.modules.reload("big")
        assert len((await k.dispatcher.call("big.dump")).output) == 10_000
