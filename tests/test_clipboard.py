"""The clipboard module: read, write and clear, with a clear message when there is no
clipboard (a server without a desktop)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths


class FakeBackend:
    name = "fake"

    def __init__(self) -> None:
        self.text = ""

    def get(self) -> str:
        return self.text

    def set(self, text: str) -> None:
        self.text = text


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("clipboard")
    return paths


def clip(k: Kernel) -> Any:
    module = k.modules.records["clipboard"].instance
    assert module is not None
    return module


async def test_write_read_clear_round_trip(project: Paths) -> None:
    async with Kernel(project) as k:
        clip(k).backend = FakeBackend()
        assert (await k.dispatcher.call("clipboard.write", {"text": "hello world"})).ok
        read = await k.dispatcher.call("clipboard.read")
        assert read.output["text"] == "hello world" and read.output["length"] == 11
        await k.dispatcher.call("clipboard.clear")
        empty = await k.dispatcher.call("clipboard.read")
        assert empty.output["text"] == "" and "empty" in empty.output["note"]


async def test_read_is_capped(project: Paths) -> None:
    async with Kernel(project) as k:
        module = clip(k)
        module.backend = FakeBackend()
        module.config.max_chars = 10
        await k.dispatcher.call("clipboard.write", {"text": "x" * 5})
        too_long = await k.dispatcher.call("clipboard.write", {"text": "y" * 50})
        assert not too_long.ok and "too long" in str(too_long.error)
        module.backend.text = "z" * 50  # something longer put there by another program
        read = await k.dispatcher.call("clipboard.read")
    assert len(read.output["text"]) == 10 and "first 10" in read.output["note"]


async def test_clear_message_when_no_clipboard(project: Paths) -> None:
    async with Kernel(project) as k:
        clip(k).backend = None  # a server with no desktop
        result = await k.dispatcher.call("clipboard.read")
    assert not result.ok and "no clipboard" in str(result.error)


async def test_detecting_the_real_backend_never_crashes(project: Paths) -> None:
    async with Kernel(project) as k:
        backend = clip(k).backend  # detect() ran for real at setup
    assert backend is None or hasattr(backend, "get")
