"""Step 2 'done when': a dummy task runs through the loop, is logged, and the kill switch
stops it mid-run."""

from __future__ import annotations

import asyncio
import threading
import time

from starlight.core import Kernel, Paths, ToolCall
from starlight.core.model import Message, ModelResponse
from tests.conftest import ModuleWriter, read_audit

SLOW_MODULE = """
    import asyncio, time
    from starlight.core import Module, tool

    class Mod(Module):
        released = False

        async def setup(self, ctx):
            ctx.kill_switch.add_cleanup(self.release, owner=ctx.name)

        def release(self):
            type(self).released = True

        @tool
        async def wait(self, seconds: float) -> str:
            await asyncio.sleep(seconds)
            return "done"

        @tool
        def block(self, seconds: float) -> str:
            time.sleep(seconds)
            return "done"
"""


class LoopingProvider:
    """Calls the same tool again and again, like a long multi-step plan."""

    name = "looping"

    def __init__(self, tool: str, args: dict[str, float]) -> None:
        self.tool, self.args, self.calls = tool, args, 0

    async def chat(self, messages: list[Message], tools: list[dict[str, object]]) -> ModelResponse:
        self.calls += 1
        return ModelResponse(tool_calls=[ToolCall(self.tool, dict(self.args))])


async def test_dummy_task_runs_and_is_logged(kernel: Kernel, paths: Paths) -> None:
    result = await kernel.ask("hello there")
    assert result.answer == "You said: hello there"
    assert not result.stopped
    events = read_audit(paths)
    assert "task.requested" in events and "task.completed" in events
    assert '"task completed"' in (paths.logs / "starlight.log").read_text()


async def test_kill_switch_stops_async_tool_mid_run(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module("slow", SLOW_MODULE)
    provider = LoopingProvider("slow.wait", {"seconds": 5})
    async with Kernel(paths, provider=provider) as kernel:
        task = asyncio.create_task(kernel.ask("wait forever"))
        await asyncio.sleep(0.2)
        started = time.monotonic()
        kernel.kill_switch.trigger("test", source="test")
        result = await task
        assert time.monotonic() - started < 1
        assert result.stopped
        assert "test" in result.answer
        mod = kernel.modules.records["slow"].instance
        assert type(mod).released  # cleanup callback ran (e.g. release mouse/keyboard)

        # Engaged until resume: the next task does not even start.
        assert (await kernel.ask("again")).stopped
        kernel.kill_switch.reset()
        provider.args = {"seconds": 0}
        assert not (await kernel.ask("again")).stopped
    events = read_audit(paths)
    assert {"killswitch.triggered", "tool.killed", "task.killed", "killswitch.reset"} <= set(events)


async def test_stop_file_from_another_process_stops_run(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module("slow", SLOW_MODULE)
    async with Kernel(paths, provider=LoopingProvider("slow.wait", {"seconds": 5})) as kernel:
        task = asyncio.create_task(kernel.ask("wait"))
        await asyncio.sleep(0.2)
        (paths.data / "STOP").write_text("starlight kill\n")  # what `starlight kill` does
        result = await asyncio.wait_for(task, timeout=2)
        assert result.stopped and "starlight kill" in result.answer


async def test_hotkey_thread_trigger_stops_blocking_tool_before_next_call(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module("slow", SLOW_MODULE)
    provider = LoopingProvider("slow.block", {"seconds": 0.1})
    async with Kernel(paths, provider=provider) as kernel:
        threading.Timer(0.35, kernel.kill_switch.trigger, args=("hotkey",)).start()
        result = await asyncio.wait_for(kernel.ask("block"), timeout=3)
        assert result.stopped
        assert provider.calls < 10


async def test_permissions_in_dispatcher(paths: Paths) -> None:
    from starlight.cli import main

    main(["--home", str(paths.root), "modules", "new", "hello"])
    paths.config_file.write_text(
        paths.config_file.read_text() + "permissions:\n  blocked: [hello.greet]\n"
    )
    asked: list[str] = []

    async def deny(tool: object, args: object) -> bool:
        asked.append(getattr(tool, "qualified_name", ""))
        return False

    async with Kernel(paths) as kernel:
        kernel.dispatcher.confirmer = deny
        greet = await kernel.dispatcher.call("hello.greet", {"name": "x"})
        shout = await kernel.dispatcher.call("hello.shout", {"text": "x"})
        unknown = await kernel.dispatcher.call("nope.tool")
        bad_args = await kernel.dispatcher.call("hello.shout", {})  # not blocked: invalid
    assert greet.error == "blocked by permissions"
    assert shout.error == "not confirmed by user" and asked == ["hello.shout"]
    assert unknown.error == "unknown tool 'nope.tool'"
    assert bad_args.error is not None and bad_args.error.startswith("invalid arguments")


async def test_step_limit(paths: Paths, write_module: ModuleWriter) -> None:
    write_module("slow", SLOW_MODULE)
    paths.config_file.write_text(paths.config_file.read_text() + "agent:\n  max_steps: 3\n")
    async with Kernel(paths, provider=LoopingProvider("slow.wait", {"seconds": 0})) as kernel:
        result = await kernel.ask("loop")
    assert result.steps == 3 and len(result.tool_results) == 3
    assert "3 steps" in result.answer
