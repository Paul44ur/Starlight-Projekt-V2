"""Smarter memory: "what do you know about me", and tidying near-duplicate memories (only
after a yes)."""

from __future__ import annotations

from typing import Any

from starlight.core import Kernel, Paths
from starlight.memory.store import MemoryEntry


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)

    async def confirm(self, question: str) -> bool:
        return self.answers.pop(0) if self.answers else True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


def seed(paths: Paths) -> None:
    store_dir = paths.memory
    store_dir.mkdir(parents=True, exist_ok=True)
    entries = [
        MemoryEntry("editor", "Editor", "Paul uses Neovim for coding.", date="2026-09-01",
                    tags=["tools"], source="conversation a"),
        MemoryEntry("editor-choice", "Editor", "Paul uses Neovim for coding every day.",
                    date="2026-10-01", tags=["preference"], source="conversation b"),
        MemoryEntry("nas", "NAS", "The NAS Deepthought is at 192.168.1.20.", date="2026-08-01"),
    ]  # fmt: skip
    for entry in entries:
        (store_dir / f"{entry.id}.md").write_text(entry.to_markdown())


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(name, args)
    assert result.ok, result.error
    return result.output


async def test_about_me_has_the_profile_and_memories(paths: Paths) -> None:
    seed(paths)
    async with Kernel(paths) as k:
        await call(k, "memory.set_profile", field="name", value="Paul")
        about = await call(k, "memory.about_me")
        assert about["profile"] == {"name": "Paul"}
        assert [m["id"] for m in about["memories"]] == ["editor-choice", "editor", "nas"]
        assert about["count"] == 3  # the profile file is not counted twice


async def test_tidy_merges_near_duplicates_only_after_a_yes(paths: Paths) -> None:
    seed(paths)
    paths.config_file.write_text(
        paths.config_file.read_text() + "memory:\n  merge_threshold: 0.8\n"
    )
    async with Kernel(paths, ui=User(False, True)) as k:
        found = await call(k, "memory.duplicates")
        assert found["count"] == 1 and found["pairs"][0]["similarity"] >= 0.8
        refused = await k.dispatcher.call("memory.tidy", {})
        assert not refused.ok
        assert {p.stem for p in paths.memory.glob("*.md")} == {"editor", "editor-choice", "nas"}
        done = await call(k, "memory.tidy")
        assert done["details"] == [{"kept": "editor-choice", "merged_in": "editor"}]
        assert {p.stem for p in paths.memory.glob("*.md")} == {"editor-choice", "nas"}
        kept = k.memory.store.get("editor-choice")
        assert kept is not None
        assert kept.content == "Paul uses Neovim for coding every day."
        assert kept.tags == ["preference", "tools"]
        assert kept.source == "conversation a; conversation b"
        assert (await call(k, "memory.duplicates"))["count"] == 0


async def test_profile_and_summaries_are_never_merged(paths: Paths) -> None:
    paths.config_file.write_text(
        paths.config_file.read_text() + "memory:\n  merge_threshold: 0.5\n"
    )
    async with Kernel(paths) as k:
        await call(k, "memory.set_profile", field="home", value="Vienna")
        await k.memory.save_summary("We talked about the home in Vienna.")
        await k.memory.save_summary("We talked about the home in Vienna again.")
        assert (await call(k, "memory.duplicates"))["count"] == 0
