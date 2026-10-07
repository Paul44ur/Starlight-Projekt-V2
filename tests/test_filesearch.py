"""Finding files on disk: by name, by content, info and recent changes. Read-only, and it
never looks inside Starlight's own folder."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths


@pytest.fixture
def tree(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small folder tree to search, outside the Starlight project folder."""
    root = tmp_path_factory.mktemp("search_root")
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "report.md").write_text("# Report\nquarterly numbers here\n")
    (root / "docs" / "notes.txt").write_text("remember the milk\nTODO: call Paul\n")
    (root / "invoice_2026.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.txt").write_text("should be skipped\n")
    return root


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path], tree: Path) -> Paths:
    use_module("filesearch")
    (paths.module_configs / "filesearch.yaml").write_text(
        f"roots: ['{tree.as_posix()}']\ninclude_default_folders: false\n"
    )
    return paths


async def call(k: Kernel, tool: str, **args: Any) -> Any:
    return await k.dispatcher.call(f"filesearch.{tool}", args)


async def test_find_by_wildcard(project: Paths) -> None:
    async with Kernel(project) as k:
        result = await call(k, "find", name_pattern="*.md")
    names = [f["name"] for f in result.output["files"]]
    assert names == ["report.md"]


async def test_find_by_substring(project: Paths) -> None:
    async with Kernel(project) as k:
        result = await call(k, "find", name_pattern="invoice")
    assert result.output["count"] == 1
    assert result.output["files"][0]["name"] == "invoice_2026.pdf"


async def test_grep_finds_text_and_skips_binary_and_heavy_dirs(project: Paths) -> None:
    async with Kernel(project) as k:
        result = await call(k, "grep", text="Paul")
    out = result.output
    assert out["files"] == 1
    assert out["matches"][0]["path"].endswith("notes.txt")
    assert out["matches"][0]["line"] == 2
    # node_modules is skipped even though junk.txt contains searchable text
    async with Kernel(project) as k:
        skipped = await call(k, "grep", text="skipped")
    assert skipped.output["files"] == 0


async def test_recent(project: Paths, tree: Path) -> None:
    old = tree / "docs" / "report.md"
    long_ago = time.time() - 30 * 86400
    os.utime(old, (long_ago, long_ago))
    async with Kernel(project) as k:
        result = await call(k, "recent", days=7)
    names = [f["name"] for f in result.output["files"]]
    assert "notes.txt" in names and "report.md" not in names


async def test_outside_allowed_root_is_refused(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("somewhere_else")
    async with Kernel(project) as k:
        result = await call(k, "find", name_pattern="*", root=str(outside))
    assert not result.ok and "outside the allowed folders" in str(result.error)


async def test_starlight_own_folder_is_never_searched(project: Paths) -> None:
    async with Kernel(project) as k:
        result = await call(k, "info", path=str(project.root / "config"))
    assert not result.ok and "off-limits" in str(result.error)
