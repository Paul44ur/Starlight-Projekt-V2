"""PC power from the chat (the right command per system, asking first, the kill switch
cancelling a planned shutdown) and focus mode (messages held, urgent ones through, one
digest at the end)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.events import Event
from starlight.core.focus import Focus, digest
from starlight.core.tools import ToolError


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []
        self.notes: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        self.notes.append(text)


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("system", "focus", "reminders", "calendar"):
        use_module(name)
    return paths


def instance(k: Kernel, name: str) -> Any:
    module = k.modules.records[name].instance
    assert module is not None
    return module


def fake_runner(k: Kernel, platform: str) -> list[list[str]]:
    system = instance(k, "system")
    ran: list[list[str]] = []
    system._platform = lambda: platform
    system._run = ran.append
    return ran


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        (
            "win32",
            [
                ["rundll32.exe", "user32.dll,LockWorkStation"],
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Add-Type -AssemblyName System.Windows.Forms; "
                    "[System.Windows.Forms.Application]::SetSuspendState("
                    "'Suspend', $false, $false)",
                ],
                ["shutdown", "/s", "/t", "600"],
                ["shutdown", "/r", "/t", "0"],
                ["shutdown", "/a"],
            ],
        ),
        (
            "linux",
            [
                ["loginctl", "lock-session"],
                ["systemctl", "suspend"],
                ["shutdown", "-h", "+10"],
                ["shutdown", "-r", "now"],
                ["shutdown", "-c"],
            ],
        ),
    ],
)
async def test_power_commands(project: Paths, platform: str, expected: list[list[str]]) -> None:
    user = User()
    async with Kernel(project, ui=user) as k:
        ran = fake_runner(k, platform)
        for name, args in (
            ("system.lock", {}),
            ("system.sleep", {}),
            ("system.shutdown", {"in_minutes": 10}),
            ("system.restart", {}),
            ("system.cancel_shutdown", {}),
        ):
            result = await k.dispatcher.call(name, args)
            assert result.ok, result.error
        assert ran == expected
        # Lock and cancel run on their own; sleep, shutdown and restart asked first.
        assert len(user.questions) == 3


async def test_power_asks_and_the_kill_switch_cancels_a_planned_shutdown(
    project: Paths,
) -> None:
    async with Kernel(project, ui=User(False, True)) as k:
        ran = fake_runner(k, "win32")
        denied = await k.dispatcher.call("system.shutdown", {"in_minutes": 5})
        assert not denied.ok and ran == []
        assert (await k.dispatcher.call("system.shutdown", {"in_minutes": 5})).ok
        assert ran == [["shutdown", "/s", "/t", "300"]]
        k.kill_switch.trigger("test", source="test")
        assert ran[-1] == ["shutdown", "/a"]  # cancelled with everything else
        k.kill_switch.reset()


async def test_a_planned_shutdown_is_cancelled_by_the_kill_switch_after_a_reload(
    project: Paths,
) -> None:
    async with Kernel(project, ui=User()) as k:
        ran = fake_runner(k, "linux")
        assert (await k.dispatcher.call("system.restart", {"in_minutes": 30})).ok
        await k.modules.reload("system")  # e.g. hot reload, or Starlight restarted
        ran = fake_runner(k, "linux")
        k.kill_switch.trigger("test", source="test")
        assert ran == [["shutdown", "-c"]]
        k.kill_switch.reset()
        await k.modules.reload("system")
        ran = fake_runner(k, "linux")
        k.kill_switch.trigger("again", source="test")
        assert ran == []  # already cancelled: nothing left to cancel
        k.kill_switch.reset()


async def test_unloading_the_module_does_not_shut_the_pc_down(project: Paths) -> None:
    async with Kernel(project) as k:
        ran = fake_runner(k, "win32")
        await k.modules.reload("system")  # runs the module's shutdown() hook
        assert ran == []


def test_mac_shutdown_only_right_away() -> None:
    import sys

    sys.path.append(str(Path(__file__).resolve().parent.parent / "modules"))
    try:
        from system.module import power_command  # type: ignore[import-not-found]
    finally:
        sys.path.pop()
    assert power_command("sleep", 0, "darwin") == ["pmset", "sleepnow"]
    with pytest.raises(ToolError):
        power_command("shutdown", 5, "darwin")


def test_focus_state_and_digest(tmp_path: Path) -> None:
    clock = [1000.0]
    focus = Focus(tmp_path / "focus.json", clock=lambda: clock[0])
    assert not focus.active()
    focus.start(until=1600)
    focus.hold("Reminder: call mum", "Reminder", "reminders")
    focus.hold("Kettle is now 44.9", "Watch", "watch")
    focus.hold("Reminder: water plants", "Reminder", "reminders")
    assert focus.active() and not focus.expired()
    clock[0] = 1700
    assert not focus.active() and focus.expired()
    text = digest(focus.stop())
    assert text.startswith("While you were focused: 2 reminder, 1 watch.")
    assert "- Reminder: call mum" in text and "- Kettle is now 44.9" in text
    assert not focus.read().on and focus.read().held == []


async def test_focus_holds_messages_but_not_urgent_ones(project: Paths) -> None:
    user = User()
    async with Kernel(project, ui=user) as k:
        delivered: list[str] = []

        async def on_deliver(event: Event) -> None:
            delivered.append(event.data["text"])

        k.bus.subscribe("deliver", on_deliver, owner="t")
        started = await k.dispatcher.call("focus.start", {"minutes": 60})
        assert started.ok and started.output["focus"]
        await k.deliver("Reminder: call mum", "Reminder", "reminders")
        await instance(k, "reminders").ctx.deliver("water the plants", title="Reminder")
        await k.deliver("Server nas is DOWN", "", "network", urgent=True)
        await k.drain()
        assert delivered == ["Server nas is DOWN"]
        status = await k.dispatcher.call("focus.status", {})
        assert status.output["focus"] and status.output["held"] == 2
        stopped = await k.dispatcher.call("focus.stop", {})
        await k.drain()
        assert "2 reminder" in stopped.output["held"]
        assert delivered[-1].startswith("Focus: While you were focused: 2 reminder.")
        assert "water the plants" in delivered[-1]
        await k.deliver("after focus", "", "core")
        await k.drain()
        assert delivered[-1] == "after focus"


async def test_focus_ends_on_its_own_with_a_digest(project: Paths) -> None:
    async with Kernel(project) as k:
        delivered: list[str] = []

        async def on_deliver(event: Event) -> None:
            delivered.append(event.data["text"])

        k.bus.subscribe("deliver", on_deliver, owner="t")
        k.focus.start(until=time.time() + 3600)
        await k.deliver("New mail from Anna", "Mail", "mail")
        assert delivered == []
        state = k.focus.read()
        state.until = time.time() - 1  # the hour is over
        k.focus._write(state)
        k.takeover_interval = 0.01
        k.config.stats.gpu_sample_interval = 0
        heartbeat = k._spawn(k._heartbeat(), "test-heartbeat")
        for _ in range(100):
            if delivered:
                break
            await k.bus.settle(0.1)
            await asyncio.sleep(0.01)
        heartbeat.cancel()
        assert delivered and "New mail from Anna" in delivered[0]
        assert not k.focus.read().on


async def test_focus_until_a_time(project: Paths) -> None:
    async with Kernel(project) as k:
        past = await k.dispatcher.call("focus.start", {"until": "yesterday 9:00"})
        assert not past.ok and "already passed" in (past.error or "")
        later = await k.dispatcher.call("focus.start", {"until": "in 2h"})
        assert later.ok and ":" in later.output["until"]
        assert k.focus.read().until is not None
