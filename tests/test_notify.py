"""Desktop notifications: it uses the OS backend, and says so clearly when there is none."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths


class FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, bool]] = []

    def send(self, title: str, message: str, urgent: bool = False) -> None:
        self.sent.append((title, message, urgent))


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("notify")
    return paths


def mod(k: Kernel) -> Any:
    instance = k.modules.records["notify"].instance
    assert instance is not None
    return instance


async def test_notify_and_alert_reach_the_backend(project: Paths) -> None:
    async with Kernel(project) as k:
        backend = FakeBackend()
        mod(k).backend = backend
        ok = await k.dispatcher.call("notify.notify", {"message": "build done", "title": "CI"})
        assert ok.ok and ok.output["shown"] and ok.output["title"] == "CI"
        await k.dispatcher.call("notify.alert", {"message": "server down"})
    assert backend.sent[0] == ("CI", "build done", False)
    assert backend.sent[1][2] is True  # alert is urgent


async def test_no_backend_says_so(project: Paths) -> None:
    async with Kernel(project) as k:
        mod(k).backend = None
        result = await k.dispatcher.call("notify.notify", {"message": "hi"})
    assert not result.ok and "no desktop notifications" in str(result.error)


async def test_empty_message_refused(project: Paths) -> None:
    async with Kernel(project) as k:
        mod(k).backend = FakeBackend()
        result = await k.dispatcher.call("notify.notify", {"message": "   "})
    assert not result.ok and "give something to say" in str(result.error)
