"""Shared building blocks for modules: the schedule parser, the path guard, background work
that runs once (in the primary core) and pauses for the kill switch, delivering a message
to every channel, and event handlers that never hold up the publisher."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from starlight.core import Kernel, Paths
from starlight.core.events import EventBus
from starlight.core.safepaths import PathGuard
from starlight.core.schedule import Schedule, ScheduleError, parse_schedule, parse_when
from starlight.core.tools import ToolError

WEDNESDAY = datetime(2026, 10, 7, 14, 0)


# -- schedules --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "weekdays", "clock"),
    [
        ("every day at 8:00", "daily", (), (8, 0)),
        ("weekdays 7:30", "daily", (0, 1, 2, 3, 4), (7, 30)),
        ("weekends at 10am", "daily", (5, 6), (10, 0)),
        ("mon,wed,fri 18:00", "daily", (0, 2, 4), (18, 0)),
        ("every Tuesday and Thursday at 9pm", "daily", (1, 3), (21, 0)),
        ("saturdays 9:15", "daily", (5,), (9, 15)),
        ("daily 18.30 uhr", "daily", (), (18, 30)),
    ],
)
def test_daily_and_weekly_schedules(
    text: str, kind: str, weekdays: tuple[int, ...], clock: tuple[int, int]
) -> None:
    plan = parse_schedule(text)
    assert (plan.kind, plan.weekdays, (plan.hour, plan.minute)) == (kind, weekdays, clock)


def test_interval_and_monthly_schedules() -> None:
    assert parse_schedule("every 15 minutes") == Schedule("interval", seconds=900)
    assert parse_schedule("every 2 hours").seconds == 7200
    assert parse_schedule("every hour").seconds == 3600
    monthly = parse_schedule("every month on the 1st at 9:00")
    assert (monthly.kind, monthly.day, monthly.hour) == ("monthly", 1, 9)
    with pytest.raises(ScheduleError):
        parse_schedule("every 10 seconds")  # too often
    with pytest.raises(ScheduleError):
        parse_schedule("whenever")


def test_next_times() -> None:
    assert parse_schedule("weekdays 7:30").next_after(WEDNESDAY) == datetime(2026, 10, 8, 7, 30)
    friday_evening = datetime(2026, 10, 9, 20, 0)
    assert parse_schedule("weekdays 7:30").next_after(friday_evening) == datetime(
        2026, 10, 12, 7, 30
    )
    assert parse_schedule("every day at 15:00").next_after(WEDNESDAY) == datetime(
        2026, 10, 7, 15, 0
    )
    # the 31st in a 30-day month falls on its last day
    assert parse_schedule("monthly on the 31st at 9:00").next_after(
        datetime(2026, 11, 5)
    ) == datetime(2026, 11, 30, 9, 0)
    assert parse_schedule("every 2 hours").next_after(WEDNESDAY) == datetime(2026, 10, 7, 16, 0)


def test_schedule_round_trip_and_description() -> None:
    for text, words in (
        ("weekdays 7:30", "weekdays at 07:30"),
        ("mon,fri 9:00", "Monday, Friday at 09:00"),
        ("every 3 hours", "every 3 hours"),
        ("monthly on the 15th at 8:00", "monthly on day 15 at 08:00"),
    ):
        plan = parse_schedule(text)
        assert plan.describe() == words
        assert Schedule.from_dict(plan.to_dict()) == plan


def test_parse_when_keeps_working() -> None:
    assert parse_when("in 2h", WEDNESDAY) == datetime(2026, 10, 7, 16, 0)
    assert parse_when("tomorrow at 9am", WEDNESDAY) == datetime(2026, 10, 8, 9, 0)
    assert parse_when("24.12.2026 18:00", WEDNESDAY) == datetime(2026, 12, 24, 18, 0)
    # a named day keeps its time even when it has passed; a bare time moves to tomorrow
    assert parse_when("today 9:00", WEDNESDAY) == datetime(2026, 10, 7, 9, 0)
    assert parse_when("9:00", WEDNESDAY) == datetime(2026, 10, 8, 9, 0)
    assert parse_when("yesterday", WEDNESDAY) == datetime(2026, 10, 6, 9, 0)
    assert parse_when("friday 18:00", WEDNESDAY) == datetime(2026, 10, 9, 18, 0)
    assert parse_when("next monday", WEDNESDAY) == datetime(2026, 10, 12, 9, 0)


# -- paths -------------------------------------------------------------------------------


def test_path_guard(tmp_path: Path) -> None:
    root, output, trusted = tmp_path / "starlight", tmp_path / "out", tmp_path / "trusted"
    guard = PathGuard(root, output, [trusted])
    assert guard.resolve("notes/a.txt").path == (output / "notes" / "a.txt").resolve()
    assert guard.resolve("notes/a.txt").free
    assert guard.resolve(str(trusted / "x.txt")).free
    assert not guard.resolve(str(tmp_path / "elsewhere.txt")).free
    with pytest.raises(ToolError, match="off-limits"):
        guard.resolve(str(root / "config" / "starlight.yaml"))
    with pytest.raises(ToolError, match=r"'\.\.'"):
        guard.resolve("../escape.txt")
    with pytest.raises(ToolError):
        guard.resolve("  ")


# -- background work ---------------------------------------------------------------------


@pytest.fixture
def ticker(write_module: Callable[..., Path]) -> None:
    write_module(
        "ticker",
        """
        from starlight.core import Module

        class Mod(Module):
            async def setup(self, ctx):
                self.ticks = 0
                ctx.every(0.01, self.tick, "tick", first_delay=0)

            def tick(self):
                self.ticks += 1
                if self.ticks == 2:
                    raise RuntimeError("one bad round must not stop the loop")
        """,
    )


def ticks(k: Kernel) -> int:
    instance = k.modules.records["ticker"].instance
    assert instance is not None
    return int(instance.ticks)  # type: ignore[attr-defined]


async def test_background_work_runs_in_the_primary_core_only(paths: Paths, ticker: None) -> None:
    async with Kernel(paths) as primary, Kernel(paths) as second:
        await asyncio.sleep(0.15)
        assert ticks(primary) > 3  # kept going after the failing round
        assert ticks(second) == 0  # waits while the other one is primary


async def test_background_work_pauses_for_the_kill_switch(paths: Paths, ticker: None) -> None:
    async with Kernel(paths) as k:
        await asyncio.sleep(0.05)
        k.kill_switch.trigger("test")
        await asyncio.sleep(0.02)
        frozen = ticks(k)
        await asyncio.sleep(0.1)
        assert ticks(k) == frozen
        k.kill_switch.reset()
        await asyncio.sleep(0.1)
        assert ticks(k) > frozen


async def test_background_work_stops_when_the_module_unloads(paths: Paths, ticker: None) -> None:
    async with Kernel(paths) as k:
        instance = k.modules.records["ticker"].instance
        assert instance is not None
        tasks = list(instance.ctx.tasks)
        await k.modules.unload("ticker")
        assert tasks and all(t.done() for t in tasks)


# -- delivery and events ----------------------------------------------------------------


async def test_deliver_reaches_the_terminal_and_the_event(paths: Paths) -> None:
    told: list[str] = []

    class Terminal:
        async def confirm(self, question: str) -> bool:
            return False

        async def ask(self, question: str, secret: bool = False) -> str | None:
            return None

        async def notify(self, text: str) -> None:
            told.append(text)

    events: list[str] = []
    async with Kernel(paths, ui=Terminal()) as k:
        k.bus.subscribe("deliver", lambda e: events.append(e.data["text"]))
        await k.deliver("the oven is done", title="Kitchen")
    assert told == events == ["Kitchen: the oven is done"]


async def test_background_handlers_do_not_hold_up_publish() -> None:
    bus = EventBus()
    order: list[str] = []
    gate = asyncio.Event()

    async def slow(_: object) -> None:
        await gate.wait()
        order.append("slow")

    bus.subscribe("x", slow, background=True)
    bus.subscribe("x", lambda e: order.append("fast"))
    await bus.publish("x")
    assert order == ["fast"]  # publish returned before the slow handler finished
    gate.set()
    await bus.settle()
    assert order == ["fast", "slow"]


async def test_modules_can_use_the_embedder(
    paths: Paths, write_module: Callable[..., Path]
) -> None:
    write_module(
        "embeds",
        """
        from starlight.core import Module, tool

        class Mod(Module):
            @tool
            async def size(self, text: str) -> int:
                vectors = await self.ctx.embedder.embed([text])
                return len(vectors[0])
        """,
    )
    async with Kernel(paths) as k:
        result = await k.dispatcher.call("embeds.size", {"text": "hello"})
    assert result.ok and result.output > 0
