"""Regression tests for the cleanup of the engine parts: models, memory index, skills."""

from __future__ import annotations

import asyncio
import shutil
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import numpy as np
import pytest

from starlight.core import Paths
from starlight.core.config import ModelConfig, SkillsConfig
from starlight.core.model import ModelResponse, create_provider
from starlight.core.stats import Stats
from starlight.memory import index as memory_index
from starlight.memory.embed import HashEmbedder
from starlight.memory.index import MemoryIndex
from starlight.memory.store import MemoryEntry, MemoryStore
from starlight.models import bench
from starlight.models.openai_compat import OpenAICompatProvider
from starlight.skills import SkillManager
from starlight.skills.store import SkillStore

# -- skills ------------------------------------------------------------------------------------


def test_a_new_skill_version_does_not_keep_the_old_example_and_code(paths: Paths) -> None:
    skills = SkillManager(paths)
    skills.draft(
        "demo", "d", "step 1", example="Req: x", code={"a.py": "print(1)", "sub/b.py": "print(2)"}
    )
    first = skills.get("demo")
    assert first is not None and first.example == "Req: x\n"
    assert first.code == {"a.py": "print(1)", "sub/b.py": "print(2)"}

    skills.draft("demo", "d2", "step 1 new", code={"c.py": "print(3)"})
    second = skills.get("demo")
    assert second is not None and second.version == 2
    assert second.example == "" and second.code == {"c.py": "print(3)"}

    skills.draft("demo", "d3", "step 1 newer")
    third = skills.get("demo")
    assert third is not None and third.example == "" and third.code == {}


def test_approving_a_skill_keeps_its_example_and_code(paths: Paths) -> None:
    skills = SkillManager(paths)
    skills.draft("demo", "d", "step 1", example="Req: x", code={"a.py": "print(1)"})
    approved = skills.approve("demo")
    assert approved.status == "approved"
    again = skills.get("demo")
    assert again is not None and again.example == "Req: x\n" and again.code == {"a.py": "print(1)"}


def test_skill_code_cannot_overwrite_the_skills_own_files(paths: Paths) -> None:
    skills = SkillManager(paths)
    skills.draft("demo", "d", "the real steps")
    with pytest.raises(ValueError, match="invalid code file name"):
        skills.draft("demo", "d", "other", code={"../SKILL.md": "evil"})
    current = skills.get("demo")
    assert current is not None and current.instructions == "the real steps"


def test_listing_without_files_skips_example_and_code(paths: Paths) -> None:
    skills = SkillManager(paths)
    skills.draft("demo", "d", "step 1", example="Req: x", code={"a.py": "print(1)"})
    [light] = SkillStore(paths.skills).all(files=False)
    assert (light.name, light.instructions) == ("demo", "step 1")
    assert light.example == "" and light.code == {}
    [full] = SkillStore(paths.skills).all()
    assert full.code == {"a.py": "print(1)"}


class RecordingStats(Stats):
    def __init__(self) -> None:
        super().__init__(None)
        self.rows: list[tuple[str, float, dict[str, Any]]] = []

    def record(self, name: str, value: float = 1.0, **labels: Any) -> None:
        self.rows.append((name, value, labels))


def test_skill_counts_report_zero_after_the_last_draft_is_approved(paths: Paths) -> None:
    stats = RecordingStats()
    skills = SkillManager(paths, stats=stats)
    skills.draft("demo", "d", "step 1")
    skills.approve("demo")
    latest = {labels["status"]: value for _, value, labels in stats.rows}  # the last one wins
    assert latest == {"draft": 0, "approved": 1, "rejected": 0}


def test_promote_without_a_module_template_is_a_clear_error(paths: Paths) -> None:
    skills = SkillManager(paths)
    skills.draft("greeting", "Greet Paul", "Say hello.")
    skills.approve("greeting")
    shutil.rmtree(paths.modules / "_template")
    with pytest.raises(KeyError, match="module template not found"):
        skills.promote("greeting", paths.modules)
    assert not (paths.modules / "greeting").exists()


def task(tool: str, request: str) -> dict[str, Any]:
    return {
        "request": request,
        "answer": "done",
        "tool_results": [SimpleNamespace(tool=tool, ok=True, output=None)],
    }


class SlowModel:
    name = "slow"

    async def chat(self, messages: list[Any], tools: list[Any]) -> ModelResponse:
        await asyncio.sleep(0.05)
        return ModelResponse(content="no json at all")


async def test_tasks_finishing_together_do_not_lose_each_others_counts(paths: Paths) -> None:
    skills = SkillManager(paths, SkillsConfig(repeat_threshold=2), provider=SlowModel())
    assert await skills.on_task(task("a.one", "first")) is None
    drafted, other = await asyncio.gather(  # the second call drafts, which awaits the model
        skills.on_task(task("a.one", "second")), skills.on_task(task("b.two", "other"))
    )
    assert drafted is not None and other is None
    from starlight.core.jsonfile import load_json

    log = load_json(skills.task_log_file, {})
    assert log["a.one"]["count"] == 2 and log["b.two"]["count"] == 1


async def test_a_damaged_task_log_is_moved_aside_not_overwritten(paths: Paths) -> None:
    skills = SkillManager(paths)
    skills.task_log_file.write_text("{ this is not json", encoding="utf-8")
    assert await skills.on_task(task("a.one", "first")) is None
    kept = list(skills.task_log_file.parent.glob("skill_tasks.unreadable-*.json"))
    assert len(kept) == 1 and kept[0].read_text(encoding="utf-8") == "{ this is not json"
    assert '"a.one"' in skills.task_log_file.read_text(encoding="utf-8")


async def test_a_task_log_saved_with_a_byte_order_mark_is_still_read(paths: Paths) -> None:
    skills = SkillManager(paths)
    entry = '{"a.one": {"count": 1, "requests": ["x"], "skill": null}}'
    skills.task_log_file.write_text("﻿" + entry, encoding="utf-8")
    await skills.on_task(task("a.one", "second"))  # the second time: drafts a skill
    assert [s.name for s in skills.all()] != []
    assert not list(skills.task_log_file.parent.glob("skill_tasks.unreadable-*"))


# -- models ------------------------------------------------------------------------------------


def fake_smi(monkeypatch: pytest.MonkeyPatch, nvidia: str | None) -> None:
    def run(cmd: list[str], timeout: float = 10.0) -> str | None:
        return nvidia if cmd[0] == "nvidia-smi" else None

    monkeypatch.setattr(bench, "_run", run)


def test_nvidia_smi_output_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_smi(monkeypatch, "1024, 8192, 37\n")
    assert bench.gpu_status() == {
        "source": "nvidia-smi",
        "used_bytes": 1024 * 2**20,
        "total_bytes": 8192 * 2**20,
        "utilization": 37.0,
    }


def test_nvidia_smi_not_available_fields_do_not_crash_the_benchmark(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_smi(monkeypatch, "[N/A], 8192, 12\n")  # a laptop or vGPU that reports no memory use
    status = bench.gpu_status()
    assert status is not None and status["used_bytes"] is None
    assert status["total_bytes"] == 8192 * 2**20 and status["utilization"] == 12.0
    fake_smi(monkeypatch, "[N/A], [N/A], [N/A]\n")
    assert bench.gpu_status() is None


async def test_openai_compat_reports_its_own_name_unless_told_otherwise() -> None:
    plain = create_provider(ModelConfig(provider="openai_compat"))
    named = create_provider(ModelConfig(provider="openai_compat", options={"name": "lmstudio"}))
    llama = create_provider(ModelConfig(provider="llamacpp"))
    assert (plain.name, named.name, llama.name) == ("openai_compat", "lmstudio", "llamacpp")
    for provider in (plain, named, llama):
        await provider.aclose()  # type: ignore[attr-defined]


async def test_warmup_accepts_a_server_without_health_that_lists_models() -> None:
    seen: list[str] = []

    def lm_studio(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200 if request.url.path == "/v1/models" else 404)

    provider = OpenAICompatProvider(transport=httpx.MockTransport(lm_studio))
    await provider.warmup()
    assert seen == ["/health", "/v1/models"]

    def llama_server(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200 if request.url.path == "/health" else 404)

    seen.clear()
    await OpenAICompatProvider(transport=httpx.MockTransport(llama_server)).warmup()
    assert seen == ["/health"]  # llama-server is asked nothing more

    nothing = OpenAICompatProvider(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(httpx.HTTPStatusError):
        await nothing.warmup()
    loading = OpenAICompatProvider(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        await loading.warmup()  # llama-server still loading the model


# -- memory index ------------------------------------------------------------------------------


async def test_similar_pairs_in_blocks_match_comparing_every_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore(tmp_path)
    topics = ["neovim editor", "nas backup", "garden plants", "router wifi", "coffee beans"]
    for i in range(17):
        entry = MemoryEntry(f"m{i:02d}", "t", f"{topics[i % 5]} note number {i % 3}")
        (tmp_path / f"{entry.id}.md").write_text(entry.to_markdown(), encoding="utf-8")
    index = MemoryIndex(store, HashEmbedder(), None)
    await index.sync()

    matrix = np.asarray(index._matrix)
    scores = matrix @ matrix.T
    expected = {
        (index._entries[i].id, index._entries[j].id)
        for i, j in combinations(range(len(index)), 2)
        if scores[i, j] >= 0.6
    }
    assert expected  # the test data has near-duplicates
    for block in (3, 5, 512):
        monkeypatch.setattr(memory_index, "PAIR_BLOCK", block)
        pairs = index.similar_pairs(0.6)
        assert {(a.id, b.id) for a, b, _ in pairs} == expected
        assert [s for _, _, s in pairs] == sorted((s for _, _, s in pairs), reverse=True)
    index.close()
