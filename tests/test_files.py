"""Saving the files Starlight makes: where they go, when it asks, what it never touches."""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import ConfigError, Kernel, ModelResponse, Paths, ToolCall, ToolResult
from starlight.core.output import known_folders, resolve_output_folder, system_folder
from tests.conftest import ScriptedProvider


class User:
    """Answers confirmations from a list (no answers left means no) and keeps the questions."""

    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("files")
    return paths


async def call(kernel: Kernel, tool: str, **args: Any) -> ToolResult:
    return await kernel.dispatcher.call(f"files.{tool}", args)


async def test_saves_in_the_starlight_folder_without_asking(
    project: Paths, documents: Path
) -> None:
    user = User()
    letter = documents / "Starlight" / "letter.txt"
    async with Kernel(project, ui=user) as kernel:
        first = await call(kernel, "save", path="letter.txt", content="Dear Anna,\nthanks!\n")
        assert first.ok, first.error
        assert first.output["saved"] == str(letter)
        assert letter.read_text(encoding="utf-8") == "Dear Anna,\nthanks!\n"

        # the same name again: a new file, the first one stays as it was
        again = await call(kernel, "save", path="letter.txt", content="A second letter")
        assert again.output["saved"] == str(letter.with_name("letter (2).txt"))
        assert "already existed" in again.output["note"]
        assert letter.read_text(encoding="utf-8") == "Dear Anna,\nthanks!\n"

        nested = await call(kernel, "save", path="reports\\2026/june.md", content="# June")
        assert Path(nested.output["saved"]) == letter.parent / "reports" / "2026" / "june.md"
    assert user.questions == []


async def test_replacing_a_file_asks_first(project: Paths, documents: Path) -> None:
    notes = documents / "Starlight" / "notes.txt"
    user = User(False, True)
    async with Kernel(project, ui=user) as kernel:
        await call(kernel, "save", path="notes.txt", content="old")
        refused = await call(kernel, "save", path="notes.txt", content="new", if_exists="replace")
        assert not refused.ok and "did not allow" in str(refused.error)
        assert notes.read_text(encoding="utf-8") == "old"
        replaced = await call(kernel, "save", path="notes.txt", content="new", if_exists="replace")
        assert replaced.ok and notes.read_text(encoding="utf-8") == "new"
    assert len(user.questions) == 2
    assert "Replace" in user.questions[0] and str(notes) in user.questions[0]

    (project.module_configs / "files.yaml").write_text("ask_before_replacing: false\n")
    quiet = User()
    async with Kernel(project, ui=quiet) as kernel:
        result = await call(kernel, "save", path="notes.txt", content="newer", if_exists="replace")
        assert result.ok
    assert quiet.questions == [] and notes.read_text(encoding="utf-8") == "newer"


async def test_saving_anywhere_else_asks_and_shows_the_path(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    desktop = tmp_path_factory.mktemp("Desktop").resolve()
    todo = desktop / "todo.txt"
    user = User(False, True, True, True)
    async with Kernel(project, ui=user) as kernel:
        refused = await call(kernel, "save", path=str(todo), content="milk")
        assert not refused.ok and not todo.exists()
        saved = await call(kernel, "save", path=str(todo), content="milk\n")
        assert saved.ok and todo.read_text(encoding="utf-8") == "milk\n"
        added = await call(kernel, "save", path=str(todo), content="bread\n", if_exists="append")
        assert added.ok and todo.read_text(encoding="utf-8") == "milk\nbread\n"
        script = await call(kernel, "save", path=str(desktop / "backup.bat"), content="@echo off")
        assert script.ok
    assert str(todo) in user.questions[0] and "outside your Starlight folder" in user.questions[0]
    assert "Add" in user.questions[2]
    assert "Windows can run .bat files" in user.questions[3]


async def test_trusted_folders_need_no_question(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    work = tmp_path_factory.mktemp("Work").resolve()
    (project.module_configs / "files.yaml").write_text(f"trusted_folders: ['{work.as_posix()}']\n")
    user = User()
    async with Kernel(project, ui=user) as kernel:
        saved = await call(kernel, "save", path=str(work / "report.md"), content="# Report")
        read = await call(kernel, "read", path=str(work / "report.md"))
    assert saved.ok and read.ok and read.output["content"] == "# Report"
    assert user.questions == []


async def test_starlight_own_folder_is_off_limits(project: Paths) -> None:
    """Writing its own settings would let the AI change its permissions; its data holds
    login sessions. So the whole Starlight folder is refused, without even asking."""
    user = User(True, True, True, True, True)  # would say yes, but is never asked
    settings = project.config_file
    before = settings.read_text()
    attempts = [
        str(settings),
        str(project.data / "notes.txt"),
        str(project.root / "notes.txt"),
        "../config/starlight.yaml",
    ]
    async with Kernel(project, ui=user) as kernel:
        for path in attempts:
            result = await call(kernel, "save", path=path, content="permissions: {}")
            assert not result.ok, path
        read = await call(kernel, "read", path=str(settings))
    assert not read.ok and "Starlight's own folder" in str(read.error)
    assert settings.read_text() == before
    assert not (project.data / "notes.txt").exists() and not (project.root / "notes.txt").exists()
    assert user.questions == []


async def test_text_formats_only_with_names_windows_accepts(
    project: Paths, documents: Path
) -> None:
    async with Kernel(project, ui=User()) as kernel:
        docx = await call(kernel, "save", path="Letter.docx", content="Dear Anna")
        xlsx = await call(kernel, "save", path="Prices.xlsx", content="a;b")
        odd = await call(kernel, "save", path="Report: June?.txt", content="x")
        device = await call(kernel, "save", path="con.txt", content="x")
    assert not docx.ok and ".html" in str(docx.error)
    assert not xlsx.ok and ".csv" in str(xlsx.error)
    assert Path(odd.output["saved"]).name == "Report_ June_.txt"
    assert Path(device.output["saved"]).name == "_con.txt"
    assert not list((documents / "Starlight").glob("*.docx"))


async def test_csv_opens_in_excel_with_umlauts(project: Paths, documents: Path) -> None:
    table = "Artikel;Preis\nMüsli;2,49\n"
    async with Kernel(project, ui=User()) as kernel:
        await call(kernel, "save", path="prices.csv", content=table)
        read = await call(kernel, "read", path="prices.csv")
    assert (documents / "Starlight" / "prices.csv").read_bytes().startswith(b"\xef\xbb\xbf")
    assert read.output["content"] == table


async def test_append_adds_a_line(project: Paths, documents: Path) -> None:
    user = User()
    async with Kernel(project, ui=user) as kernel:
        await call(kernel, "save", path="shopping.txt", content="milk")
        await call(kernel, "save", path="shopping.txt", content="bread\n", if_exists="append")
        fresh = await call(kernel, "save", path="new.txt", content="eggs", if_exists="append")
    shopping = documents / "Starlight" / "shopping.txt"
    assert shopping.read_text(encoding="utf-8") == "milk\nbread\n"
    assert Path(fresh.output["saved"]).read_text(encoding="utf-8") == "eggs"
    assert user.questions == []


async def test_read_and_list(
    project: Paths, documents: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    folder = documents / "Starlight"
    folder.mkdir()
    old = folder / "alt.txt"
    old.write_bytes("Grüße aus Köln".encode("cp1252"))  # saved by an older Windows program
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    (folder / "blob.dat").write_bytes(b"\x00\x01\x02binary")
    other = tmp_path_factory.mktemp("Other").resolve() / "info.txt"
    other.write_text("not in the Starlight folder", encoding="utf-8")
    user = User(False, True)
    async with Kernel(project, ui=user) as kernel:
        await call(kernel, "save", path="long.txt", content="x" * 50_000)
        umlauts = await call(kernel, "read", path="alt.txt")
        long = await call(kernel, "read", path="long.txt")
        binary = await call(kernel, "read", path="blob.dat")
        refused = await call(kernel, "read", path=str(other))
        allowed = await call(kernel, "read", path=str(other))
        listing = await call(kernel, "list")
        missing = await call(kernel, "read", path="nope.txt")
    assert umlauts.output["content"] == "Grüße aus Köln"
    assert len(long.output["content"]) == 40_000 and "first 40000" in long.output["note"]
    assert not binary.ok and "binary" in str(binary.error)
    assert not refused.ok and allowed.output["content"] == "not in the Starlight folder"
    assert str(other) in user.questions[0]
    names = [f["name"] for f in listing.output["files"]]
    assert names[0] == "long.txt" and "alt.txt" in names  # newest first
    assert not missing.ok and "does not exist" in str(missing.error)


async def test_size_limit(project: Paths) -> None:
    (project.module_configs / "files.yaml").write_text("max_file_mb: 0.001\n")
    async with Kernel(project, ui=User()) as kernel:
        result = await call(kernel, "save", path="big.txt", content="x" * 5000)
    assert not result.ok and "too large" in str(result.error)


async def test_the_ai_saves_what_it_wrote(project: Paths, documents: Path) -> None:
    letter = "Dear landlord,\nthe heating is broken.\n"
    provider = ScriptedProvider(
        ModelResponse(
            tool_calls=[ToolCall("files.save", {"path": "heating.txt", "content": letter})]
        ),
        "I saved the letter as heating.txt in your Starlight folder.",
    )
    async with Kernel(project, provider=provider, ui=User()) as kernel:
        result = await kernel.ask("Write a letter about the broken heating and save it")
        assert kernel.dispatcher.get("files.save") is not None
    assert result.tool_results[0].ok
    assert (documents / "Starlight" / "heating.txt").read_text(encoding="utf-8") == letter


async def test_output_folder_setting(
    project: Paths, documents: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    root = tmp_path_factory.mktemp("Install").resolve()
    assert resolve_output_folder("", root) == (documents / "Starlight").resolve()
    # Starlight itself installed at Documents/Starlight: its files go next to it
    inside = documents / "Starlight"
    assert resolve_output_folder("", inside) == (documents / "Starlight files").resolve()
    with pytest.raises(ConfigError, match="overlaps"):
        resolve_output_folder(str(root / "out"), root)
    with pytest.raises(ConfigError, match="overlaps"):
        resolve_output_folder(str(root.parent), root)

    custom = tmp_path_factory.mktemp("Custom").resolve()
    project.config_file.write_text(
        project.config_file.read_text() + f"output:\n  folder: '{custom.as_posix()}'\n"
    )
    async with Kernel(project, ui=User()) as kernel:
        saved = await call(kernel, "save", path="here.txt", content="x")
        where = await call(kernel, "locations")
    assert Path(saved.output["saved"]) == custom.resolve() / "here.txt"
    assert where.output["starlight_folder"] == str(custom.resolve())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows known folders")
def test_windows_known_folders() -> None:
    from starlight.core import output

    for name in ("desktop", "documents", "downloads"):
        found = output._system_folder(name)
        assert found is not None and found.is_absolute(), name
    assert known_folders()["documents"] == output._system_folder("documents")


@pytest.mark.skipif(sys.platform == "win32", reason="xdg-user-dirs is Linux")
def test_localized_linux_folders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "user-dirs.dirs").write_text(
        'XDG_DOCUMENTS_DIR="$HOME/Dokumente"\nXDG_DESKTOP_DIR="$HOME/"\n', encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert system_folder("documents") == Path.home() / "Dokumente"
    assert system_folder("desktop") == Path.home() / "Desktop"  # $HOME itself means unused
    assert system_folder("downloads") == Path.home() / "Downloads"  # not listed


async def test_a_bad_output_folder_does_not_stop_starlight(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    """output.folder pointing into Starlight's own folder only breaks saving files (with a
    clear reason); the rest of Starlight starts and works."""
    from starlight.core.modules import ModuleStatus

    use_module("files")
    use_module("hello")
    paths.config_file.write_text(
        paths.config_file.read_text() + f"output:\n  folder: '{(paths.root / 'out').as_posix()}'\n"
    )
    async with Kernel(paths) as kernel:  # starts instead of raising ConfigError
        files = kernel.modules.records["files"]
        assert files.status is ModuleStatus.FAILED and "overlaps" in (files.error or "")
        assert (await kernel.dispatcher.call("hello.greet", {"name": "Paul"})).ok
