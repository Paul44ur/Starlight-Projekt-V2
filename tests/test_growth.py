"""Stability round 2: what grows over weeks of running is trimmed, and a damaged memory
index or an interrupted backup is repaired instead of piling up."""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.config import MemoryConfig
from starlight.core.http import ResponseCache
from starlight.core.stats import Stats
from starlight.core.tools import ToolError
from starlight.memory import Memory
from starlight.memory.service import _signature
from starlight.web.auth import LOCKOUT_SECONDS, MAX_FAILURES, Throttle
from tests.conftest import ScriptedProvider
from tests.test_debug_modules import User, call, instance

DAY = 86400.0


# -- statistics ------------------------------------------------------------------------------------


def test_old_statistics_are_pruned_and_the_file_is_compacted(tmp_path: Path) -> None:
    stats = Stats(tmp_path / "stats.db")
    for _ in range(1000):
        stats.record("gpu.utilization_percent", 50.0, note="x" * 600)
    stats._db.execute("UPDATE metrics SET ts = ?", (time.time() - 40 * DAY,))
    stats.record("core.heartbeat")  # today's: stays
    stats._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    big = (tmp_path / "stats.db").stat().st_size
    assert stats.maintain(retention_days=30) == 1000
    assert (tmp_path / "stats.db").stat().st_size < big / 2
    assert len(stats.query("core.heartbeat")) == 1
    stats.close()


# -- the web page cache ----------------------------------------------------------------------------


def page(url: str) -> Any:
    from starlight.core.http import HttpResponse

    return HttpResponse(url, 200, {}, "<html>" + "x" * 500 + "</html>")


def test_the_page_cache_drops_expired_pages_and_keeps_a_limited_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = ResponseCache(None)
    for n in range(10):
        cache.put(page(f"https://example.org/{n}"))
    cache._db.execute("UPDATE pages SET fetched = fetched - ? WHERE url LIKE '%/0'", (3 * DAY,))
    assert cache.purge() == 1  # only the expired one
    monkeypatch.setattr(ResponseCache, "MAX_ROWS", 4)
    cache.purge()
    left = [r[0] for r in cache._db.execute("SELECT url FROM pages ORDER BY fetched DESC")]
    assert len(left) == 4 and "https://example.org/0" not in left
    assert cache.get("https://example.org/9", ttl=3600) is not None  # the newest stayed


def test_the_page_cache_cleans_itself_while_it_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ResponseCache(None)
    monkeypatch.setattr(ResponseCache, "MAX_ROWS", 50)
    for n in range(400):
        cache.put(page(f"https://example.org/{n}"))
    (count,) = cache._db.execute("SELECT COUNT(*) FROM pages").fetchone()
    assert count <= 50 + 200  # trimmed every 200 pages, so it never runs away


# -- memory ----------------------------------------------------------------------------------------


async def test_only_the_newest_conversation_summaries_are_kept(paths: Paths) -> None:
    memory = Memory(paths, MemoryConfig(summary_keep=3))
    for n in range(6):
        entry = await memory.save_summary(f"Summary number {n}")
        stamp = time.time() - (60 - n) * 60  # older to newer
        os.utime(memory.store.path(entry.id), (stamp, stamp))
    await memory.save_summary("The newest summary")
    names = sorted(p.read_text() for p in (paths.memory / "conversations").glob("*.md"))
    assert len(names) == 3 and any("The newest summary" in text for text in names)
    assert not any("Summary number 0" in text for text in names)
    assert len(memory.index) == 3  # and the index followed


async def test_facts_and_the_profile_are_never_pruned(paths: Paths) -> None:
    memory = Memory(paths, MemoryConfig(summary_keep=1))
    await memory.remember("The NAS is called Deepthought.", topic="NAS")
    memory.profile.set("name", "Paul")
    for n in range(4):
        await memory.save_summary(f"Summary {n}")
    assert (paths.memory / "nas.md").is_file() and (paths.memory / "profile.md").is_file()
    assert len(list((paths.memory / "conversations").glob("*.md"))) == 1


async def test_keeping_all_summaries_is_possible(paths: Paths) -> None:
    memory = Memory(paths, MemoryConfig(summary_keep=0))
    for n in range(4):
        await memory.save_summary(f"Summary {n}")
    assert len(list((paths.memory / "conversations").glob("*.md"))) == 4


def test_a_restored_memory_file_changes_the_signature(tmp_path: Path) -> None:
    note = tmp_path / "a.md"
    note.write_text("version one")
    before = _signature([note])
    note.write_text("version two")  # the same size...
    os.utime(note, (1_000_000_000, 1_000_000_000))  # ...and an older date (a restored backup)
    assert _signature([note]) != before
    assert _signature([note]) == _signature([note])  # and stable while nothing changes


async def test_a_damaged_vector_is_rebuilt_not_fatal(paths: Paths) -> None:
    memory = Memory(paths)
    await memory.remember("Paul's NAS runs at 192.168.1.20.", topic="NAS")
    await memory.remember("The router is in the hallway.", topic="Router")
    await memory.sync(force=True)
    db = sqlite3.connect(paths.data / "memory_index.sqlite")
    db.execute("UPDATE vectors SET vector = substr(vector, 1, 7) WHERE id = 'nas'")  # cut off
    db.commit()
    db.close()
    fresh = Memory(paths)
    await fresh.sync(force=True)
    assert [h.entry.id for h in await fresh.recall("NAS address", top_k=2)]


async def test_vectors_of_different_sizes_make_the_index_start_over(paths: Paths) -> None:
    memory = Memory(paths)
    await memory.remember("Paul's NAS runs at 192.168.1.20.", topic="NAS")
    await memory.remember("The router is in the hallway.", topic="Router")
    await memory.sync(force=True)
    db = sqlite3.connect(paths.data / "memory_index.sqlite")
    db.execute("UPDATE vectors SET vector = substr(vector, 1, 8) WHERE id = 'nas'")  # 2 numbers
    db.commit()
    db.close()
    fresh = Memory(paths)
    await fresh.sync(force=True)
    assert len(fresh.index) == 2 and await fresh.recall("router", top_k=1)


# -- the web chat's login throttle -----------------------------------------------------------------


def test_a_throttle_does_not_collect_an_entry_per_visitor() -> None:
    throttle = Throttle()
    for n in range(50):
        assert not throttle.locked(f"10.0.0.{n}")
    assert throttle.failures == {}


def test_a_scan_from_many_addresses_is_forgotten_after_the_lockout() -> None:
    throttle = Throttle()
    for n in range(1200):
        throttle.fail(f"10.1.{n // 250}.{n % 250}", now=1000.0)
    later = 1000.0 + LOCKOUT_SECONDS + 1
    throttle.fail("10.9.9.9", now=later)
    assert list(throttle.failures) == ["10.9.9.9"]
    for _ in range(MAX_FAILURES):
        throttle.fail("10.9.9.8", now=later)
    assert throttle.locked("10.9.9.8", now=later)


# -- what the core keeps in memory -----------------------------------------------------------------


async def test_only_recent_conversations_stay_in_memory(
    paths: Paths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Kernel, "MAX_CACHED_HISTORIES", 2)
    paths.config_file.write_text(
        "kill_switch:\n  hotkey: null\nmemory:\n  enabled: false\nskills:\n  enabled: false\n"
    )
    names: list[str] = []
    async with Kernel(paths, provider=ScriptedProvider(*["ok"] * 6)) as k:
        for n in range(4):
            name = k.new_conversation()
            names.append(name)
            await k.ask(f"question {n}", keep_history=True, conversation=name)
        assert len(k._histories) <= 2 and names[0] not in k._histories
        again = await k.ask(
            "and again", keep_history=True, conversation=names[0]
        )  # read back from its file
        assert not again.failed
        assert any("question 0" in m.content for m in k.conversations.messages(names[0]))


async def test_a_deleted_conversation_leaves_nothing_behind(kernel: Kernel) -> None:
    name = kernel.new_conversation()
    await kernel.ask("hello", keep_history=True, conversation=name)
    kernel.forget_conversation(name)
    assert name not in kernel._histories and name not in kernel._stamps
    assert name not in kernel._conversation_locks and name not in kernel._taken_back


async def test_housekeeping_trims_what_a_lower_limit_leaves_over(paths: Paths) -> None:
    paths.config_file.write_text("kill_switch:\n  hotkey: null\nskills:\n  enabled: false\n")
    async with Kernel(paths) as k:
        for n in range(3):
            await k.memory.save_summary(f"Summary {n}")
        files = list((paths.memory / "conversations").glob("*.md"))
        assert len(files) == 3  # (the default limit is 60)
        k.memory.config.summary_keep = 1  # a lower limit, applied later
        await k._housekeeping()
        assert len(list((paths.memory / "conversations").glob("*.md"))) == 1
        assert k._housekeeping_at > 0


# -- backups ---------------------------------------------------------------------------------------


def backup(k: Kernel) -> Any:
    return instance(k, "backup")


async def test_a_backup_clears_leftovers_and_stops_when_told(
    paths: Paths, use_module: Callable[[str], Path], tmp_path_factory: pytest.TempPathFactory
) -> None:
    use_module("backup")
    source, target = tmp_path_factory.mktemp("Docs"), tmp_path_factory.mktemp("Disk")
    for n in range(5):
        (source / f"file{n}.txt").write_text("text" * 100)
    async with Kernel(paths, ui=User()) as k:
        await call(k, "backup.add", name="docs", source=str(source), target=str(target))
        folder = target / "docs"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "docs 2020-01-01 000000.partial").write_bytes(b"half")  # from a power cut
        plan = backup(k)._set("docs")[1]
        stop = threading.Event()
        stop.set()
        with pytest.raises(ToolError, match="stopped"):
            backup(k)._make("docs", plan, stop)
        assert list(folder.glob("*.partial")) == [] and list(folder.glob("*.zip")) == []
        made = backup(k)._make("docs", plan)  # and a normal run still works
        assert made["files"] == 5 and len(list(folder.glob("*.zip"))) == 1


# -- a huge paste does not make every tool step summarize again ------------------------------------


class CountingModel:
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages: list[Any], tools: list[Any], **kwargs: Any) -> Any:
        from starlight.core.model import ModelResponse

        self.calls += 1
        return ModelResponse(content="A short summary.")


async def test_a_pasted_text_that_fills_the_budget_is_not_summarized_over_and_over() -> None:
    from starlight.core.config import ModelConfig
    from starlight.core.context import ContextManager
    from starlight.core.model import Message

    model = CountingModel()
    manager = ContextManager(
        model, ModelConfig(context_window=4000, reserve_tokens=500, keep_recent=4)
    )  # type: ignore[arg-type]
    old = [Message("system", "rules")]
    for n in range(8):
        old += [Message("user", f"question {n} " * 40), Message("assistant", "answer " * 40)]
    huge = [Message("user", "paste " * 6000)]
    messages = [*old, *huge, Message("assistant", "ok"), Message("user", "and now?")]
    assert await manager.compact(messages, []) is messages and model.calls == 0
    # without the huge paste the same history is still summarized, as before
    normal = [*old, Message("user", "x " * 4000), Message("assistant", "ok")]
    compacted = await manager.compact(normal, [])
    assert compacted is not normal and model.calls == 1


# -- searching with other spellings ----------------------------------------------------------------


def test_searching_finds_straße_with_strasse(tmp_path: Path) -> None:
    from starlight.core.conversations import ConversationStore
    from starlight.core.model import Message

    store = ConversationStore(tmp_path)
    name = store.create("Umzug")
    store.save(name, [Message("user", "Wir ziehen in die Hauptstraße 5")])
    assert [c["id"] for c in store.list("hauptstrasse")] == [name]
    assert [c["id"] for c in store.list("STRASSE")] == [name]


def test_the_newest_pages_stay_when_the_clock_does_not_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(Windows' clock moves every ~15 ms, so quick puts can share one time.)"""
    monkeypatch.setattr("starlight.core.http.time.time", lambda: 1_800_000_000.0)
    monkeypatch.setattr(ResponseCache, "MAX_ROWS", 3)
    cache = ResponseCache(None)
    for n in range(8):
        cache.put(page(f"https://example.org/{n}"))
    cache.purge()
    left = sorted(r[0] for r in cache._db.execute("SELECT url FROM pages"))
    assert left == [f"https://example.org/{n}" for n in (5, 6, 7)]
