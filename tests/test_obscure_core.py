"""Regression tests for the obscure-code pass over starlight/core: the two bugs it found
(a "did you mean" missing inside roles.<name>, and a request's own context summarized
away), and pins for code that was rewritten to be clearer but must behave the same."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

import pytest

from starlight.core import ConfigError, Kernel, Paths, load_config
from starlight.core.config import ModelConfig
from starlight.core.context import SUMMARY_PREFIX, ContextManager
from starlight.core.model import Message, ModelResponse
from tests.conftest import ModuleWriter

# -- bug: "did you mean" inside an entry of a dict of settings -----------------------------


def test_a_mistyped_setting_inside_a_role_suggests_the_right_one(paths: Paths) -> None:
    (paths.config / "starlight.yaml").write_text(
        "roles:\n  code:\n    provider: echo\n    contex_window: 8192\n", encoding="utf-8"
    )
    with pytest.raises(ConfigError) as caught:
        load_config(paths)
    text = str(caught.value)
    assert "roles.code.contex_window" in text
    assert "did you mean 'context_window'?" in text


def test_suggestions_elsewhere_are_unchanged() -> None:
    from starlight.core.config import StarlightConfig, _known_names

    assert _known_names(StarlightConfig, ("kill_switch",)) == [
        "hotkey",
        "stop_file",
        "poll_interval",
    ]
    assert "port" in _known_names(StarlightConfig, ("web",))
    assert "hosts" in _known_names(StarlightConfig, ("web", "remote"))
    assert _known_names(StarlightConfig, ("model", "options")) == []  # free-form mapping


# -- bug: the request's own context (enricher messages) was summarized away ---------------


class Summarizer:
    name = "summarizer"

    def __init__(self) -> None:
        self.transcripts: list[str] = []

    async def chat(self, messages: list[Message], tools: object) -> ModelResponse:
        self.transcripts.append(messages[-1].content)
        return ModelResponse(content="they talked about many things")


def long_chat(*head: Message) -> list[Message]:
    messages = list(head)
    for i in range(10):
        messages += [Message("user", f"q{i} " + "x" * 200), Message("assistant", "a" * 200)]
    return [*messages, Message("user", "what day is it?")]


async def test_the_requests_own_context_is_never_summarized_away() -> None:
    model = Summarizer()
    manager = ContextManager(
        model,  # type: ignore[arg-type]
        ModelConfig(context_window=1024, reserve_tokens=256, keep_recent=2),
    )
    situation = Message("system", "Now: Friday, 16 October 2026, 14:02.")
    recalled = Message("system", "Recalled: the user's NAS is at 10.0.0.5.")
    messages = long_chat(Message("system", "You are Starlight."), situation, recalled)
    compacted = await manager.compact(messages, [])
    assert compacted is not messages
    assert compacted[:3] == messages[:3]  # system prompt, then this request's context
    assert compacted[3].content.startswith(SUMMARY_PREFIX)
    assert compacted[-1].content == "what day is it?"
    assert "Now: Friday" not in model.transcripts[0]  # not folded into the summary


async def test_an_earlier_summary_is_summarized_again_not_kept_forever() -> None:
    model = Summarizer()
    manager = ContextManager(
        model,  # type: ignore[arg-type]
        ModelConfig(context_window=1024, reserve_tokens=256, keep_recent=2),
    )
    earlier = Message("system", SUMMARY_PREFIX + "they set up the NAS")
    messages = long_chat(Message("system", "You are Starlight."), earlier)
    compacted = await manager.compact(messages, [])
    summaries = [m for m in compacted if m.content.startswith(SUMMARY_PREFIX)]
    assert len(summaries) == 1 and summaries[0] is not earlier
    assert "they set up the NAS" in model.transcripts[0]


async def test_a_kernel_conversation_keeps_working_after_a_summary(paths: Paths) -> None:
    paths.config_file.write_text(
        paths.config_file.read_text()
        + "model:\n  context_window: 1024\n  reserve_tokens: 256\n  keep_recent: 2\n"
    )
    seen: list[list[Message]] = []

    class Model:
        name = "m"
        stand_in = True  # no memory extraction or skill drafts: only requests and summaries

        async def chat(self, messages: list[Message], tools: object) -> ModelResponse:
            seen.append(list(messages))
            if messages[0].content.startswith("Summarize"):
                return ModelResponse(content="short summary")
            return ModelResponse(content="reply " + "z" * 300)

    async with Kernel(paths, provider=Model()) as kernel:  # type: ignore[arg-type]
        for i in range(6):
            await kernel.ask(f"turn {i} " + "q" * 400, keep_history=True)
        assert any(m.content.startswith(SUMMARY_PREFIX) for m in kernel.history)
        # the conversation keeps no request's context, only the summary and the turns
        assert all(
            m.role != "system" or m.content.startswith(SUMMARY_PREFIX) for m in kernel.history
        )
    answered = [s for s in seen if s[-1].role == "user" and s[-1].content.startswith("turn ")]
    assert len(answered) == 6
    # every request the model answered still had the date and time right after the prompt
    assert all(s[1].content.startswith("Now: ") for s in answered)


# -- pins for rewritten code ---------------------------------------------------------------


async def test_drain_does_not_wait_for_the_heartbeat(paths: Paths) -> None:
    async with Kernel(paths) as kernel:
        started = time.monotonic()
        await kernel.drain(timeout=20)
        assert time.monotonic() - started < 5


async def test_take_back_and_put_back_restore_the_exchange(paths: Paths) -> None:
    async with Kernel(paths) as kernel:
        await kernel.ask("first", keep_history=True)
        await kernel.ask("second", keep_history=True)
        before = [m.content for m in kernel.history]
        taken = await kernel.take_back(expected="sec")  # the start of it is enough
        assert taken is not None and taken.content == "second"
        assert [m.content for m in kernel.history] == before[:2]
        with pytest.raises(ValueError):
            await kernel.take_back(expected="something else")
        await kernel.put_back()
        assert [m.content for m in kernel.history] == before


async def test_take_back_without_a_question_returns_none(paths: Paths) -> None:
    async with Kernel(paths) as kernel:
        assert await kernel.take_back() is None


async def test_the_cores_own_tool_groups_describe_themselves(paths: Paths) -> None:
    async with Kernel(paths) as kernel:
        description, keywords = kernel._describe_module("memory")
        assert "Long-term memory" in description and "remember" in keywords
        keywords.append("changed")  # a copy: the next call is not affected
        assert "changed" not in kernel._describe_module("memory")[1]
        assert kernel._describe_module("skills")[1] == ["skill"]
        assert kernel._describe_module("tools") == ("", [])


async def test_enabling_a_module_in_a_dependency_cycle_fails(
    paths: Paths, write_module: ModuleWriter
) -> None:
    source = """
        from starlight.core import Module

        class Mod(Module):
            pass
    """
    write_module("aaa", source, dependencies="[bbb]", enabled_by_default="false")
    write_module("bbb", source, dependencies="[aaa]", enabled_by_default="false")
    async with Kernel(paths) as kernel:
        record = await kernel.modules.enable("aaa")
        assert record.instance is None and "dependency cycle" in (record.error or "")


def test_bytecode_is_made_hash_checked_once(tmp_path: Path) -> None:
    import importlib.util

    from starlight.core.modules import PYC_CHECK_SOURCE, PYC_HASH_BASED, _hash_checked_bytecode

    source = tmp_path / "thing.py"
    source.write_text("X = 1\n", encoding="utf-8")
    cached = Path(importlib.util.cache_from_source(str(source)))
    _hash_checked_bytecode(tmp_path)
    flags = int.from_bytes(cached.read_bytes()[4:8], "little")
    assert flags == PYC_HASH_BASED | PYC_CHECK_SOURCE == 0b11
    first = cached.stat().st_mtime_ns
    time.sleep(0.01)
    _hash_checked_bytecode(tmp_path)  # already hash-checked: not compiled again
    assert cached.stat().st_mtime_ns == first


def test_kept_programs_are_let_go_once_they_end() -> None:
    from starlight.core import modules

    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    running = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    saved = list(modules._KEPT)
    try:
        modules._KEPT[:] = [done]
        alias = modules._KEPT
        modules.keep_running(running)
        assert modules._KEPT is alias and [running] == modules._KEPT
    finally:
        running.kill()
        running.wait()
        modules._KEPT[:] = saved


async def test_background_tasks_are_tracked_and_stopped(paths: Paths) -> None:
    from starlight.core.events import EventBus
    from starlight.core.killswitch import KillSwitch
    from starlight.core.logging import get_logger
    from starlight.core.modules import ModuleContext

    ctx = ModuleContext(
        "m",
        {},
        get_logger("test"),
        EventBus(),
        KillSwitch(paths.data / "STOP"),
        paths,
        paths.data,
    )
    tasks = ctx.tasks
    first = ctx.background(lambda: asyncio.sleep(0), "quick", primary_only=False)
    await first
    second = ctx.background(lambda: asyncio.sleep(30), "slow", primary_only=False)
    assert ctx.tasks is tasks and ctx.tasks == [second]  # the finished one went
    await ctx.stop_background()
    assert ctx.tasks is tasks and ctx.tasks == [] and second.cancelled()
