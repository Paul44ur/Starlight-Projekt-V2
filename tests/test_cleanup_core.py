"""Regression tests for the core cleanup pass: a failed module takes back what its setup
registered, Retry-After values that make no sense, a STOP file that vanishes mid-read, a
tool's own TimeoutError, a bad logging level, and the smaller fixes around them."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from starlight.core import ConfigError, Kernel, Paths, load_config
from starlight.core.config import HttpConfig
from starlight.core.conversations import ConversationStore
from starlight.core.http import PoliteHttp, parse_retry_after
from starlight.core.killswitch import KillSwitch, KillSwitchEngaged
from starlight.core.model import Message
from starlight.core.modules import ModuleManifest, ModuleState, ModuleStatus, discover
from tests.conftest import ModuleWriter

# -- a module that fails in setup() after it registered things ----------------------------

FLAKY = """
    from starlight.core import Module

    class Mod(Module):
        async def setup(self, ctx):
            ctx.bus.subscribe("deliver", self.on_event, owner=ctx.name)
            ctx.kill_switch.add_cleanup(lambda: None, owner=ctx.name)
            raise RuntimeError("setup bug")

        async def on_event(self, event):
            (self.ctx.data_dir / "got-event.txt").write_text("half-built module got it")

        async def shutdown(self):
            (self.ctx.data_dir / "shutdown.txt").write_text("released")
"""


async def test_a_module_that_fails_in_setup_leaves_nothing_registered(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module("flaky", FLAKY)
    async with Kernel(paths) as kernel:
        record = kernel.modules.records["flaky"]
        assert record.status is ModuleStatus.FAILED
        assert not [s for s in kernel.bus._subscriptions if s.owner == "flaky"]
        assert "flaky" not in kernel.kill_switch._cleanups
        data = paths.module_data("flaky")
        assert (data / "shutdown.txt").exists()  # what setup() opened is released

        await kernel.bus.publish("deliver")
        assert not (data / "got-event.txt").exists()

        await kernel.modules.reload("flaky")  # still failing: nothing piles up
        await kernel.modules.reload("flaky")
        assert not [s for s in kernel.bus._subscriptions if s.owner == "flaky"]
        assert "flaky" not in kernel.kill_switch._cleanups


async def test_a_failing_shutdown_after_a_failed_setup_is_only_logged(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module(
        "fragile",
        """
        from starlight.core import Module

        class Mod(Module):
            async def setup(self, ctx):
                raise RuntimeError("setup bug")

            async def shutdown(self):
                self.never_set.close()
        """,
    )
    async with Kernel(paths) as kernel:
        record = kernel.modules.records["fragile"]
        assert record.status is ModuleStatus.FAILED
        assert "setup bug" in (record.error or "")


# -- Retry-After ----------------------------------------------------------------------------


def test_retry_after_that_makes_no_sense_gives_the_default() -> None:
    for value in ("soon", "nan", "NaN", "12 minutes", "Tomorrow-ish", None, ""):
        assert parse_retry_after(value, 2.0) == 2.0, value
    assert parse_retry_after("3", 2.0) == 3.0
    assert parse_retry_after("-5", 2.0) == 0.0
    assert parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT", 2.0) > 1_000_000
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", 2.0) == 0.0


async def test_a_bad_retry_after_header_backs_off_by_the_default_and_retries() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, headers={"Retry-After": "soon"})
        return httpx.Response(200, text="ok")

    http = PoliteHttp(
        HttpConfig(per_domain_interval=0, backoff_base=0.05),
        transport=httpx.MockTransport(handler),
    )
    response = await http.get("https://slow.example/")
    await http.aclose()
    assert response.ok and calls["n"] == 2


# -- kill switch ----------------------------------------------------------------------------


def test_a_stop_file_that_cannot_be_read_still_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = tmp_path / "STOP"
    stop.write_text("by hand\n")
    ks = KillSwitch(stop)

    def refuse(self: Path, *args: Any, **kwargs: Any) -> str:
        raise PermissionError("locked for a moment")

    monkeypatch.setattr(Path, "read_text", refuse)
    with pytest.raises(KillSwitchEngaged):  # not a raw PermissionError
        ks.check()
    assert ks.reason == "STOP file present"


async def test_the_stop_file_watcher_survives_a_failed_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stop = tmp_path / "STOP"
    ks = KillSwitch(stop)
    real_sync = ks._sync
    polls = {"n": 0}

    def flaky_sync() -> None:
        polls["n"] += 1
        if polls["n"] == 1:
            raise OSError("disk hiccup")
        real_sync()

    monkeypatch.setattr(ks, "_sync", flaky_sync)
    watcher = asyncio.create_task(ks.watch(0.01))
    try:
        await asyncio.sleep(0.05)
        stop.write_text("from another terminal\n")
        for _ in range(100):
            if ks._fired:
                break
            await asyncio.sleep(0.02)
        assert ks._fired and not watcher.done()
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher


# -- the dispatcher's timeout ---------------------------------------------------------------

TIMEOUTS = """
    import asyncio
    from starlight.core import Module, tool

    class Mod(Module):
        @tool(own_timeout=True)
        async def own(self) -> str:
            "stops itself"
            await asyncio.wait_for(asyncio.sleep(5), 0.05)
            return "never"

        @tool
        async def inner(self) -> str:
            "has a shorter limit of its own, but is not marked"
            raise TimeoutError("the server did not answer")

        @tool
        async def slow(self) -> str:
            "runs into the dispatcher's limit"
            await asyncio.sleep(5)
            return "never"
"""


async def test_only_the_dispatchers_own_limit_is_reported_as_its_timeout(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module("clock", TIMEOUTS)
    paths.config_file.write_text("kill_switch:\n  hotkey: null\ntools:\n  timeout: 0.2\n")
    async with Kernel(paths) as k:
        own = await k.dispatcher.call("clock.own", {})
        inner = await k.dispatcher.call("clock.inner", {})
        slow = await k.dispatcher.call("clock.slow", {})
    assert not own.ok and "after 0.2s" not in str(own.error) and "timed out" in str(own.error)
    assert not inner.ok and inner.error == "timed out: the server did not answer"
    assert not slow.ok and slow.error == "timed out after 0.2s"


# -- config ---------------------------------------------------------------------------------


def test_a_logging_level_that_does_not_exist_is_a_config_error(paths: Paths) -> None:
    paths.config_file.write_text("logging:\n  level: verbose\n")
    with pytest.raises(ConfigError) as caught:
        load_config(paths)
    text = str(caught.value)
    assert "logging.level" in text and "DEBUG, INFO" in text and "'verbose'" in text


def test_a_logging_level_may_be_written_in_any_case(paths: Paths) -> None:
    paths.config_file.write_text("logging:\n  level: debug\n")
    assert load_config(paths).logging.level == "DEBUG"


# -- the kernel -----------------------------------------------------------------------------


async def test_a_regenerate_that_worked_leaves_nothing_taken_back(paths: Paths) -> None:
    async with Kernel(paths) as k:
        name = k.new_conversation()
        await k.ask("one", keep_history=True, conversation=name)
        assert await k.take_back(name) is not None
        assert name in k._taken_back
        result = await k.ask("one again", keep_history=True, conversation=name)
        assert not (result.stopped or result.failed)
        assert name not in k._taken_back


async def test_a_stopped_regenerate_can_still_be_put_back(paths: Paths) -> None:
    async with Kernel(paths) as k:
        name = k.new_conversation()
        await k.ask("one", keep_history=True, conversation=name)
        await k.take_back(name)
        assert name in k._taken_back  # nothing was asked, so nothing is cleared
        await k.put_back(name)
        assert name not in k._taken_back
        assert k.conversations.messages(name)[0].content == "one"


async def test_the_gpu_is_sampled_every_interval_even_beyond_the_30s_heartbeat(
    paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = {"now": 1000.0}
    sleeps: list[float] = []
    sampled: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["now"] += seconds
        if len(sleeps) >= 6:
            raise asyncio.CancelledError
        await real_sleep(0)

    def gpu_status() -> dict[str, Any]:
        sampled.append(clock["now"])
        return {"used_bytes": 1, "total_bytes": 2, "utilization": 3}

    monkeypatch.setattr("starlight.models.bench.gpu_status", gpu_status)
    monkeypatch.setattr(
        "starlight.core.kernel.time", SimpleNamespace(monotonic=lambda: clock["now"])
    )
    monkeypatch.setattr("starlight.core.kernel.asyncio.sleep", fake_sleep)
    k = Kernel(paths)
    k.config.stats.gpu_sample_interval = 120
    k.services.primary = True
    k._housekeeping_at = clock["now"]
    try:
        with pytest.raises(asyncio.CancelledError):
            await k._heartbeat()
    finally:
        monkeypatch.undo()
        await k.stop()
    assert sleeps == [30.0] * 6  # the viewer needs a beat at least every 30 s
    assert sampled == [1000.0, 1120.0]  # but the GPU is asked every 120 s


async def test_one_failing_beat_does_not_end_the_heartbeat(
    paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError
        await real_sleep(0)

    k = Kernel(paths)
    k.config.stats.gpu_sample_interval = 0
    k.services.primary = True
    beats = {"n": 0}
    real_record = k.stats.record

    def record(name: str, *args: Any, **kwargs: Any) -> None:
        if name == "core.heartbeat":
            beats["n"] += 1
            if beats["n"] == 1:
                raise RuntimeError("database is locked")
        real_record(name, *args, **kwargs)

    monkeypatch.setattr(k.stats, "record", record)
    monkeypatch.setattr("starlight.core.kernel.asyncio.sleep", fake_sleep)
    try:
        with pytest.raises(asyncio.CancelledError):
            await k._heartbeat()
    finally:
        monkeypatch.undo()
        await k.stop()
    assert beats["n"] == 3  # the first failed, the loop went on


# -- module discovery on every request ------------------------------------------------------


def test_discover_parses_a_manifest_again_only_when_it_changed(
    paths: Paths, write_module: ModuleWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_module("alpha", "x = 1\n")
    write_module("beta", "x = 1\n")
    parsed: list[str] = []
    real = ModuleManifest.model_validate

    def counting(data: Any, *args: Any, **kwargs: Any) -> ModuleManifest:
        parsed.append(str(data.get("name")))
        return real(data, *args, **kwargs)

    monkeypatch.setattr(ModuleManifest, "model_validate", counting)
    cache: dict[Any, Any] = {}
    found, errors = discover(paths.modules, cache)
    assert {"alpha", "beta"} <= set(found) and not errors
    first = len(parsed)
    found_again, _ = discover(paths.modules, cache)
    assert len(parsed) == first and found_again.keys() == found.keys()

    (paths.modules / "alpha" / "module.yaml").write_text(
        "name: alpha\nentrypoint: module:Mod\nversion: 9.9.9\n"
    )
    found_again, _ = discover(paths.modules, cache)
    assert len(parsed) == first + 1 and found_again["alpha"][0].version == "9.9.9"

    (paths.modules / "beta" / "module.yaml").write_text("name: wrong\n")  # broken: reported
    _, errors = discover(paths.modules, cache)
    assert "must match the folder name" in errors["beta"]
    _, errors = discover(paths.modules, cache)  # and still, from the cache
    assert "must match the folder name" in errors["beta"]

    (paths.modules / "beta" / "module.yaml").unlink()
    (paths.modules / "beta" / "module.py").unlink()
    (paths.modules / "beta").rmdir()
    found_again, errors = discover(paths.modules, cache)
    assert "beta" not in found_again and "beta" not in errors
    assert all(path.parent.name != "beta" for path in cache)  # nothing kept for it


async def test_a_state_pass_reads_the_state_file_once(
    paths: Paths, write_module: ModuleWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("one", "two", "three"):
        write_module(name, "from starlight.core import Module\nclass Mod(Module): ...\n")
    async with Kernel(paths) as kernel:
        reads = {"n": 0}
        real = ModuleState._read

        def counting(self: ModuleState) -> tuple[set[str], set[str]]:
            reads["n"] += 1
            return real(self)

        monkeypatch.setattr(ModuleState, "_read", counting)
        await kernel.modules.sync_state()
        assert reads["n"] == 1
        # a change made by `starlight modules disable` is still noticed on the next pass
        ModuleState(paths.module_state_file).set("two", False)
        await kernel.modules.sync_state()
        assert kernel.modules.records["two"].status is ModuleStatus.DISABLED
        assert kernel.modules.records["one"].status is ModuleStatus.LOADED


# -- conversation list ----------------------------------------------------------------------


def test_the_conversation_list_does_not_read_unchanged_files_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ConversationStore(tmp_path / "conversations")
    first = store.create("Alpha")
    second = store.create()
    store.save(second, [Message("user", "needle in a haystack"), Message("assistant", "ok")])
    store.rename(first, "Title A")

    assert {c["title"] for c in store.list()} == {"Title A", "needle in a haystack"}
    reads: list[str] = []
    real = Path.read_text

    def counting(self: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(self.name)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    listed = store.list()
    assert not [name for name in reads if name.endswith(".json")]
    assert {c["id"]: c["count"] for c in listed} == {first: 0, second: 1}

    store.rename(first, "Title B")  # same length: a changed file is still noticed
    assert {c["title"] for c in store.list()} == {"Title B", "needle in a haystack"}
    store.save(first, [Message("user", "a"), Message("user", "b")])
    assert {c["id"]: c["count"] for c in store.list()}[first] == 2
    store.delete(first)
    assert [c["id"] for c in store.list()] == [second]

    reads.clear()
    assert [c["id"] for c in store.list("haystack")] == [second]  # searching reads the text
    assert [c["id"] for c in store.list("nothing like it")] == []
    assert reads
    assert [c["id"] for c in store.list()] == [second]  # the search left the list intact
