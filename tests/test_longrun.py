"""A long, mixed session: about 200 operations (questions, tool calls, Regenerate, stop with
the kill switch, a failing model, new / renamed / deleted conversations, remembered facts,
restarts) in a random but repeatable order. Afterwards everything on disk must still be
whole: conversations readable and consistent, memory and its index in step, the audit log
intact, no temporary files left."""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.model import Message, ModelResponse, ToolCall

OPERATIONS = 200
SEEDS = (1, 2)


class SessionModel:
    """Answers by what the last message asks for."""

    name = "scripted"

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        self.calls += 1
        last = messages[-1]
        if last.role == "tool":
            return ModelResponse(content="Done with the tool.")
        if last.content.startswith("TOOL"):
            return ModelResponse(tool_calls=[ToolCall("hello.greet", {"name": "Paul"})])
        if last.content.startswith("SLOW"):
            await asyncio.sleep(30)  # until the kill switch ends it
        if last.content.startswith("FAIL"):
            raise RuntimeError("the model server went away")
        return ModelResponse(content=f"ok {self.calls}")


def whole(paths: Paths) -> list[str]:
    """What is wrong on disk (empty: nothing)."""
    problems: list[str] = []
    folder = paths.data / "conversations"
    current = (
        (folder / "current.txt").read_text().strip() if (folder / "current.txt").exists() else ""
    )
    names = [p.stem for p in folder.glob("*.json")]
    if current and current not in names:
        problems.append(f"the current conversation {current} does not exist")
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text("utf-8"))
        except ValueError as exc:
            problems.append(f"{path.name}: {exc}")
            continue
        wanted: set[str] = set()
        for message in data["messages"]:
            if message["role"] not in ("system", "user", "assistant", "tool"):
                problems.append(f"{path.name}: role {message['role']}")
            for call in message.get("tool_calls", []):
                wanted.add(call["id"])
            if message["role"] == "tool" and message.get("tool_call_id") not in wanted:
                problems.append(f"{path.name}: a tool result without its call")
    for leftover in [*paths.data.rglob("*.tmp"), *paths.memory.rglob("*.tmp")]:
        problems.append(f"temporary file left: {leftover.name}")
    audit = paths.logs / "audit.log"
    for number, line in (
        enumerate(audit.read_text("utf-8").splitlines(), 1) if audit.exists() else []
    ):
        try:
            json.loads(line)
        except ValueError:
            problems.append(f"audit.log line {number} is not JSON")
    return problems


async def run(paths: Paths, seed: int) -> dict[str, int]:
    rng = random.Random(seed)
    model = SessionModel()
    counts = dict.fromkeys(("ask", "tool", "regenerate", "stop", "fail", "other", "restart"), 0)
    kernel = Kernel(paths, provider=model)  # type: ignore[arg-type]
    await kernel.start()
    conversations = [kernel.new_conversation() for _ in range(3)]
    try:
        for step in range(OPERATIONS):
            name = rng.choice(conversations)
            roll = rng.random()
            if roll < 0.40:
                await kernel.ask(f"question {step}", keep_history=True, conversation=name)
                counts["ask"] += 1
            elif roll < 0.55:
                await kernel.ask(f"TOOL {step}", keep_history=True, conversation=name)
                counts["tool"] += 1
            elif roll < 0.70:
                asked = await kernel.take_back(name)
                if asked is None:
                    continue
                if rng.random() < 0.3:  # asking again was stopped: nothing new was kept
                    await kernel.put_back(name)
                else:
                    await kernel.ask(asked.content, keep_history=True, conversation=name)
                counts["regenerate"] += 1
            elif roll < 0.76:
                task = asyncio.ensure_future(
                    kernel.ask(f"SLOW {step}", keep_history=True, conversation=name)
                )
                await asyncio.sleep(0.05)
                kernel.kill_switch.trigger("long-run test", source="test")
                result = await asyncio.wait_for(task, 10)
                assert result.stopped or result.failed
                kernel.kill_switch.reset()
                counts["stop"] += 1
            elif roll < 0.82:
                result = await kernel.ask(f"FAIL {step}", keep_history=True, conversation=name)
                assert result.failed
                counts["fail"] += 1
            elif roll < 0.86:
                kernel.conversations.rename(name, f"talk {step}")
                counts["other"] += 1
            elif roll < 0.90:
                new = kernel.new_conversation()
                if len(conversations) > 4:
                    gone = conversations.pop(0)
                    kernel.forget_conversation(gone)
                conversations.append(new)
                counts["other"] += 1
            elif roll < 0.91:
                await kernel.memory.remember(
                    f"Fact number {step} about the test.", topic=f"t{step}"
                )
                counts["other"] += 1
            elif roll < 0.96:  # a restart now and then: everything must come back from disk
                await kernel.stop()
                kernel = Kernel(paths, provider=model)  # type: ignore[arg-type]
                await kernel.start()
                counts["restart"] += 1
    finally:
        await kernel.stop()
    return counts


@pytest.mark.parametrize("seed", SEEDS)
async def test_a_long_mixed_session_leaves_everything_whole(
    paths: Paths, use_module: Callable[[str], Path], seed: int
) -> None:
    use_module("hello")
    paths.config_file.write_text(
        "kill_switch:\n  hotkey: null\n  poll_interval: 0.02\n"
        "memory:\n  auto_extract: false\nskills:\n  enabled: false\n"
    )
    counts = await asyncio.wait_for(run(paths, seed), 240)
    assert counts["ask"] > 20 and counts["tool"] > 10 and counts["regenerate"] > 5
    assert counts["restart"] > 0
    assert counts["stop"] > 3 and counts["fail"] > 3
    assert whole(paths) == []
    # and a fresh start still reads it all
    async with Kernel(paths, provider=SessionModel()) as k:  # type: ignore[arg-type]
        for item in k.conversations.list():
            assert k.conversations.messages(item["id"]) is not None
        await k.memory.sync(force=True)
        assert len(k.memory.index) == len(k.memory.store.files())
