"""Reminders: parsing "when", setting/listing/cancelling, firing due reminders (through
check_due, so no timing flakiness) and surviving a restart. All local, no network."""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("reminders")
    # Turn the background loop off so firing is driven explicitly in the test.
    (paths.module_configs / "reminders.yaml").write_text("check_interval: 0\n")
    return paths


def mod(k: Kernel) -> Any:
    instance = k.modules.records["reminders"].instance
    assert instance is not None
    return instance


# -- the pure parser (reached through the loaded module, like the other module tests) ------


def _parse_when(k: Kernel) -> Any:
    import sys

    return sys.modules[type(mod(k)).__module__].parse_when


async def test_parse_when_relative_and_clock(project: Paths) -> None:
    async with Kernel(project) as k:
        parse_when = _parse_when(k)
        now = datetime(2026, 10, 7, 14, 0, 0)
        assert parse_when("in 2h", now) == datetime(2026, 10, 7, 16, 0)
        assert parse_when("in 30m", now) == datetime(2026, 10, 7, 14, 30)
        assert parse_when("18:30", now) == datetime(2026, 10, 7, 18, 30)
        # a time already past today rolls to tomorrow
        assert parse_when("9am", now) == datetime(2026, 10, 8, 9, 0)
        assert parse_when("tomorrow 9am", now) == datetime(2026, 10, 8, 9, 0)
        assert parse_when("2026-12-24 20:00", now) == datetime(2026, 12, 24, 20, 0)


async def test_parse_when_rejects_nonsense(project: Paths) -> None:
    from starlight.core import ToolError

    async with Kernel(project) as k:
        parse_when = _parse_when(k)
        with pytest.raises(ToolError):
            parse_when("sometime soon", datetime(2026, 10, 7, 14, 0))


# -- the tools -----------------------------------------------------------------------------


async def test_set_list_cancel(project: Paths) -> None:
    async with Kernel(project) as k:
        set_result = await k.dispatcher.call(
            "reminders.remind", {"when": "in 1h", "text": "call Paul"}
        )
        rid = set_result.output["id"]
        listed = await k.dispatcher.call("reminders.list", {})
        assert listed.output["count"] == 1
        assert listed.output["reminders"][0]["text"] == "call Paul"
        cancelled = await k.dispatcher.call("reminders.cancel", {"id": rid})
        assert cancelled.output["cancelled"]
        empty = await k.dispatcher.call("reminders.list", {})
        assert empty.output["count"] == 0


async def test_due_once_fires_and_is_removed(project: Paths) -> None:
    fired: list[str] = []
    async with Kernel(project) as k:
        k.bus.subscribe("reminder.due", lambda e: fired.append(e.data["text"]))
        await k.dispatcher.call("reminders.remind", {"when": "in 1h", "text": "ping"})
        module = mod(k)
        future = datetime.now().replace(year=datetime.now().year + 1)
        result = await module.check_due(future)
        assert len(result) == 1
        assert fired == ["ping"]
        assert (await k.dispatcher.call("reminders.list", {})).output["count"] == 0


async def test_daily_reschedules_instead_of_removing(project: Paths) -> None:
    async with Kernel(project) as k:
        await k.dispatcher.call("reminders.every", {"schedule": "8:00", "text": "stretch"})
        module = mod(k)
        future = datetime.now().replace(year=datetime.now().year + 1)
        fired = await module.check_due(future)
        assert fired[0]["text"] == "stretch"
        # still present, moved to a future time
        listed = (await k.dispatcher.call("reminders.list", {})).output
        assert listed["count"] == 1 and listed["reminders"][0]["repeats"] == "daily"


async def test_reminders_survive_a_restart(project: Paths) -> None:
    async with Kernel(project) as k:
        await k.dispatcher.call("reminders.remind", {"when": "tomorrow 9am", "text": "survive"})
    async with Kernel(project) as k2:
        listed = (await k2.dispatcher.call("reminders.list", {})).output
        assert listed["count"] == 1 and listed["reminders"][0]["text"] == "survive"


async def test_every_weekday_and_unit_word_is_understood(project: Paths) -> None:
    """Weekly reminders work for every day (not just Monday, Friday and Sunday), and
    'in 30 minutes', 'in 2 days', 'in 10 seconds', 'in an hour' all parse."""
    async with Kernel(project) as k:
        module = sys.modules[type(mod(k)).__module__]
        now = datetime(2026, 10, 7, 14, 0, 0)  # a Wednesday
        for index, day in enumerate(
            ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        ):
            for text in (f"{day} 9am", f"every {day} at 8:00", f"{day[:3]} 18:30", f"{day}s 7:15"):
                *_, weekday = module.parse_schedule(text)
                assert weekday == index, text
        assert module.parse_schedule("every day at 8:00") == (8, 0, None)
        assert module.parse_schedule("daily 6pm") == (18, 0, None)
        for text, delta in (
            ("in 30 minutes", 1800),
            ("in 2 days", 2 * 86400),
            ("in 10 seconds", 10),
            ("in an hour", 3600),
            ("in 1 week", 7 * 86400),
        ):
            assert (module.parse_when(text, now) - now).total_seconds() == delta, text
