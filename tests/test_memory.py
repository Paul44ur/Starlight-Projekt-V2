"""Step 5 'done when': a fact told in one session is recalled in the next after a restart."""

from __future__ import annotations

import json

import pytest

from starlight.cli import main
from starlight.core import Kernel, Paths
from starlight.core.model import ModelResponse, ToolCall
from starlight.memory import Memory
from starlight.memory.service import parse_facts
from tests.conftest import ScriptedProvider


async def test_fact_survives_restart(paths: Paths) -> None:
    # Session 1: the user tells Starlight something; the model decides to keep it.
    session1 = ScriptedProvider(
        ModelResponse(
            tool_calls=[
                ToolCall(
                    "memory.remember",
                    {
                        "fact": "Paul's NAS is called Deepthought and runs at 192.168.1.20.",
                        "topic": "Paul's NAS",
                    },
                )
            ]
        ),
        "Got it, I will remember that.",
    )
    async with Kernel(paths, provider=session1) as kernel:
        result = await kernel.ask("My NAS is Deepthought at 192.168.1.20, remember that.")
        assert result.tool_results[0].ok
    files = list(paths.memory.glob("*.md"))
    assert [f.name for f in files] == ["paul-s-nas.md"]
    assert "topic: Paul's NAS" in files[0].read_text()

    # Session 2, after a restart: the fact is recalled into the prompt.
    session2 = ScriptedProvider("Your NAS Deepthought is at 192.168.1.20.")
    async with Kernel(paths, provider=session2) as kernel:
        await kernel.ask("What is the IP of my NAS?")
    prompt = session2.calls[0]
    recalled = [m for m in prompt if m.role == "system" and "remember" in m.content]  # type: ignore[attr-defined]
    assert recalled and "192.168.1.20" in recalled[0].content  # type: ignore[attr-defined]


async def test_writing_after_each_task_merges_duplicates(paths: Paths) -> None:
    facts1 = json.dumps(
        [{"topic": "Favourite editor", "fact": "Paul uses VS Code.", "confidence": 0.8}]
    )
    facts2 = json.dumps(
        [{"topic": "Favourite editor", "fact": "Paul switched to Neovim.", "confidence": 0.9}]
    )
    provider = ScriptedProvider("ok", facts1, "ok", facts2, "ok", "[]")
    async with Kernel(paths, provider=provider) as kernel:
        await kernel.ask("I use VS Code")
        await kernel.drain()
        await kernel.ask("I switched to Neovim")
        await kernel.drain()
        await kernel.ask("hello")
        await kernel.drain()
    files = list(paths.memory.glob("*.md"))
    assert len(files) == 1  # merged, not piled up
    text = files[0].read_text()
    assert "Neovim" in text and "VS Code" not in text
    assert "confidence: 0.9" in text


async def test_folder_is_the_source_of_truth(paths: Paths) -> None:
    memory = Memory(paths)
    entry, merged = await memory.remember(
        "The garage door code is not stored here.", topic="Garage"
    )
    assert not merged
    assert (await memory.recall("garage door"))[0].entry.id == entry.id

    # Edit by hand: the index follows.
    path = paths.memory / f"{entry.id}.md"
    path.write_text(path.read_text().replace("garage door code", "boat mooring spot"))
    assert "boat" in (await memory.recall("boat mooring"))[0].entry.content
    # Delete by hand: gone from recall.
    path.unlink()
    assert await memory.recall("boat mooring") == []
    memory.close()


async def test_memory_tools_and_forget_needs_confirmation(kernel: Kernel) -> None:
    saved = await kernel.dispatcher.call("memory.remember", {"fact": "Likes tea", "topic": "Tea"})
    assert saved.ok
    found = await kernel.dispatcher.call("memory.recall", {"query": "tea"})
    assert found.output[0]["id"] == "tea"
    denied = await kernel.dispatcher.call("memory.forget", {"memory_id": "tea"})
    assert denied.error == "not confirmed by user"


def test_parse_facts() -> None:
    assert parse_facts('Sure! [{"topic": "a", "fact": "b"}] hope that helps') == [
        {"topic": "a", "fact": "b"}
    ]
    assert parse_facts("You said: hello") == []
    assert parse_facts('[{"topic": "a", "fact": ""}]') == []


def test_memory_cli(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    home = ["--home", str(paths.root)]
    assert (
        main(
            [*home, "memory", "add", "Server", "backups", "run", "at", "3am", "--topic", "Backups"]
        )
        == 0
    )
    assert main([*home, "memory", "list"]) == 0
    assert "backups" in capsys.readouterr().out
    assert main([*home, "memory", "search", "when do backups run"]) == 0
    assert "backups" in capsys.readouterr().out
    assert main([*home, "memory", "export", str(paths.root / "out.json")]) == 0
    assert json.loads((paths.root / "out.json").read_text())[0]["topic"] == "Backups"
    assert main([*home, "memory", "forget", "backups"]) == 0
    assert main([*home, "memory", "forget", "backups"]) == 1
