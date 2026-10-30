"""The tool-use exam: a model that calls the tools right passes, one that guesses or
answers everything with text fails, and the result says so plainly."""

from __future__ import annotations

from typing import Any

import pytest

from starlight.cli import main
from starlight.core import Paths
from starlight.core.model import EchoProvider, Message, ModelResponse, ToolCall
from starlight.models.exam import ANSWERS, TASKS, TOOLS, exam, verdict

GOOD = {  # the request, and what a good model calls for it
    "Hamburg": [ToolCall("weather.forecast", {"place": "Hamburg", "days": 1})],
    "17.5": [ToolCall("calc.calculate", {"expression": "2480 * 17.5 / 100"})],
    "Mum": [ToolCall("reminders.add", {"text": "call Mum", "when": "in 2 hours"})],
    "Spotify": [ToolCall("apps.open", {"name": "Spotify"})],
    "volume": [ToolCall("media.volume", {"level": 30})],
    "Munich": [ToolCall("web.search", {"query": "Munich population"})],
    "Tokyo": [
        ToolCall("clock.time_in", {"place": "Tokyo"}),
        ToolCall("notes.note", {"text": "buy milk"}),
    ],
}


class Scripted:
    """Answers like a model that knows its tools (or, with ``good=False``, one that guesses
    every time)."""

    name = "scripted"

    def __init__(self, good: bool = True) -> None:
        self.good = good
        self.seen_tools: list[list[str]] = []

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        self.seen_tools.append([t["name"] for t in tools])
        last = messages[-1]
        if last.role == "tool":
            return ModelResponse(content=f"Here is what I found: {last.content}")
        if not self.good:
            return ModelResponse(content="I think it is about 400.")
        if "French" in last.content:
            return ModelResponse(content="Bonjour !")
        for word, calls in GOOD.items():
            if word in last.content:
                return ModelResponse(tool_calls=calls)
        return ModelResponse(content="?")


async def test_a_model_that_knows_its_tools_passes() -> None:
    model = Scripted()
    seen: list[str] = []
    report = await exam(model, on_result=lambda r: seen.append(r.task))
    failed = [t for t in report["tasks"] if not t["passed"]]
    assert failed == [] and report["score"] == report["of"] == len(TASKS)
    assert "reliably" in report["verdict"] and seen == [t.name for t in TASKS]
    assert all(names == [t["name"] for t in TOOLS] for names in model.seen_tools)


async def test_a_model_that_guesses_fails_and_is_told_why() -> None:
    report = await exam(Scripted(good=False))
    assert report["score"] == 0 and "Weak at tool use" in report["verdict"]
    notes = {t["task"]: t["note"] for t in report["tasks"]}
    assert notes["picks the right tool"] == "did not use weather.forecast (no tool)"
    assert notes["answers without a tool when none is needed"] == "no fitting answer"


async def test_wrong_arguments_and_unknown_tools_are_named() -> None:
    class Sloppy(Scripted):
        async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> Any:
            text = messages[-1].content
            if messages[-1].role == "user" and "volume" in text:
                return ModelResponse(tool_calls=[ToolCall("media.volume", {"level": "thirty"})])
            if messages[-1].role == "user" and "Hamburg" in text:
                return ModelResponse(tool_calls=[ToolCall("weather.get", {"city": "Hamburg"})])
            return await super().chat(messages, tools)

    report = await exam(Sloppy())
    notes = {t["task"]: t["note"] for t in report["tasks"]}
    assert notes["gives a number as a number"].startswith("media.volume with the wrong arguments")
    assert notes["picks the right tool"] == "called a tool that does not exist: weather.get"
    assert report["score"] == len(TASKS) - 2 and "Usable" in report["verdict"]


async def test_a_model_that_is_not_there_fails_every_task_without_crashing() -> None:
    class Down:
        name = "down"

        async def chat(self, *args: Any) -> ModelResponse:
            raise ConnectionError("connection refused")

    report = await exam(Down())
    assert report["score"] == 0
    assert all("connection refused" in t["note"] for t in report["tasks"])


def test_every_stand_in_tool_has_an_answer_and_the_verdicts_are_ordered() -> None:
    assert {t["name"] for t in TOOLS} == set(ANSWERS)
    assert "reliably" in verdict(8, 8) and "reliably" in verdict(7, 8)
    assert "Usable" in verdict(5, 8) and "Weak" in verdict(4, 8)


@pytest.mark.parametrize("provider", [EchoProvider()])
async def test_the_echo_model_is_weak_at_tools(provider: EchoProvider) -> None:
    assert (await exam(provider))["score"] == 0  # (it echoes; "Bonjour" never comes)


def test_starlight_model_exam(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["--home", str(paths.root), "model", "exam"])  # the built-in echo model
    out = capsys.readouterr().out
    assert code == 1 and "Tool-use exam for echo (8 tasks)" in out
    assert "FAIL picks the right tool" in out and "0 of 8 passed. Weak at tool use" in out
