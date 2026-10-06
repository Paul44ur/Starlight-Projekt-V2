"""Step 9 'done when': you ask for a small script, it is written and tested in the
sandbox, and a repeated task gets saved as a draft skill."""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from starlight.cli import main
from starlight.core import Kernel, Paths
from starlight.core.model import ModelResponse, ToolCall
from starlight.sandbox import DockerSandbox, ProcessSandbox, docker_available
from starlight.skills import SkillManager
from tests.conftest import ScriptedProvider

BUGGY = json.dumps(
    {
        "files": {
            "main.py": "def word_count(text):\n    return len(text.split(' '))\n",
            "test_main.py": "from main import word_count\n"
            "assert word_count('a  b') == 2, word_count('a  b')\n"
            "print('ALL TESTS PASSED')\n",
        },
        "test_command": "python test_main.py",
    }
)
FIXED = json.dumps(
    {
        "files": {
            "main.py": "def word_count(text):\n    return len(text.split())\n",
            "test_main.py": "from main import word_count\n"
            "assert word_count('a  b') == 2\nassert word_count('') == 0\n"
            "print('ALL TESTS PASSED')\n",
        },
        "test_command": "python test_main.py",
    }
)


class Yes:
    async def confirm(self, question: str) -> bool:
        return True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        self.notes = [*getattr(self, "notes", []), text]


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("code")
    (paths.module_configs / "code.yaml").write_text("sandbox: process\ntimeout: 20\n")
    paths.config_file.write_text(paths.config_file.read_text() + "memory:\n  auto_extract: false\n")
    return paths


def write_code_call() -> ModelResponse:
    return ModelResponse(
        tool_calls=[
            ToolCall("code.write_code", {"task": "a function that counts the words in a text"})
        ]
    )


async def test_script_written_tested_fixed_and_learned(project: Paths, documents: Path) -> None:
    provider = ScriptedProvider(write_code_call(), BUGGY, FIXED, "Here is word_count.")
    ui = Yes()
    async with Kernel(project, provider=provider, ui=ui) as kernel:
        first = await kernel.ask("Write me a small script that counts words")
        await kernel.drain()
        output = first.tool_results[0].output
        assert output["success"] is True, output
        assert output["attempts"] == 2  # failed once, read the error, fixed it
        assert output["sandbox"] == "process"
        assert "ALL TESTS PASSED" in output["test_output"]
        assert Path(output["saved_to"], "main.py").read_text().endswith("split())\n")
        # saved where the user finds it: Documents/Starlight/code/<task>
        assert Path(output["saved_to"]).parent == documents / "Starlight" / "code"
        # the error from attempt 1 was fed back to the model
        assert "AssertionError" in provider.calls[2][-1].content  # type: ignore[attr-defined]
        assert kernel.skills.all() == []  # once is not a pattern yet

        provider.responses += [
            write_code_call(),
            ModelResponse(FIXED),
            ModelResponse("Done again."),
        ]
        second = await kernel.ask("Count the words in a text for me, as a script")
        await kernel.drain()
        # the same task again gets its own folder: the first program is not overwritten
        assert second.tool_results[0].output["saved_to"] == output["saved_to"] + "-2"
        assert Path(output["saved_to"], "main.py").read_text().endswith("split())\n")

    [skill] = SkillManager(project).all()
    assert skill.status == "draft"  # never used before you approve it
    assert skill.tools == ["code.write_code"]
    assert "word_count" in skill.code["main.py"]  # the tested code is kept
    assert any("New draft skill" in n for n in ui.notes)


async def test_approved_skills_are_used_and_drafts_are_not(paths: Paths) -> None:
    skills = SkillManager(paths)
    skills.draft(
        "backup-check",
        "Check last night's NAS backup",
        "1. Call `network.ssh_check` with `ls -l /backups`.\n2. Report the newest file.",
        triggers=["backup", "nas"],
    )
    provider = ScriptedProvider("a", "b")
    async with Kernel(paths, provider=provider) as kernel:
        await kernel.ask("Did the NAS backup run last night?")
        assert not any("Skill: backup-check" in m.content for m in provider.calls[0])  # type: ignore[attr-defined]
        kernel.skills.approve("backup-check")
        await kernel.ask("Did the NAS backup run last night?")
        assert any("Skill: backup-check" in m.content for m in provider.calls[-1])  # type: ignore[attr-defined]
        await kernel.ask("What's the weather like?")
        assert not any("Skill:" in m.content for m in provider.calls[-1])  # type: ignore[attr-defined]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_skills_are_versioned_and_can_be_rolled_back(
    paths: Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    skills = SkillManager(paths)
    skills.draft("greeting", "Greet Paul", "Say hello.")
    skills.approve("greeting")
    skills.draft("greeting", "Greet Paul", "Say SOMETHING BAD.")  # v2, a draft again
    assert skills.get("greeting").version == 2  # type: ignore[union-attr]
    assert len(skills.versions("greeting")) == 3

    restored = skills.rollback("greeting")
    assert restored.instructions == "Say hello." and restored.status == "approved"
    assert restored.version == 1

    home = ["--home", str(paths.root)]
    assert main([*home, "skills", "list"]) == 0
    assert "greeting" in capsys.readouterr().out
    assert main([*home, "skills", "history", "greeting"]) == 0
    assert "rollback greeting" in capsys.readouterr().out
    assert main([*home, "skills", "approve", "nope"]) == 1

    target = skills.promote("greeting", paths.modules)
    assert "enabled_by_default: false" in (target / "module.yaml").read_text()
    assert "Say hello." in (target / "README.md").read_text()


async def test_process_sandbox_blocks_what_matters(tmp_path: Path) -> None:
    secret = tmp_path / "config"
    secret.mkdir()
    (secret / "vault_index.yaml").write_text("keys: [x]")
    sandbox = ProcessSandbox(tmp_path / "sb", deny=[secret])

    async def run(code: str, network: bool = False, timeout: float = 10) -> str:
        result = await sandbox.run({"main.py": code}, ["python", "main.py"], timeout, network)
        return (
            "OK " + result.stdout.strip()
            if result.ok
            else ("TIMEOUT" if result.timed_out else result.stderr.strip().splitlines()[-1])
        )

    assert await run("print(6 * 7)") == "OK 42"
    assert "network" in await run("import socket; socket.create_connection(('1.1.1.1', 80))")
    assert "keyring" in await run("import keyring")
    assert "outside the sandbox" in await run(f"open({str(secret / 'vault_index.yaml')!r})")
    assert "starting programs" in await run("import os; os.system('echo hi')")
    assert await run("while True: pass", timeout=1) == "TIMEOUT"
    assert list((tmp_path / "sb").iterdir()) == []  # nothing left behind


@pytest.mark.skipif(not docker_available(), reason="Docker not running")
async def test_docker_sandbox(tmp_path: Path) -> None:
    sandbox = DockerSandbox(tmp_path)
    result = await sandbox.run({"main.py": "print('hi')"}, ["python", "main.py"], 120)
    assert result.ok and result.stdout.strip() == "hi"


def test_parse_solution() -> None:
    from starlight.core.modules import discover, import_module_class
    from tests.test_network import REPO

    found, _ = discover(REPO / "modules")
    import_module_class(*found["code"])
    parse = sys.modules["starlight_modules.code.module"].parse_solution

    assert parse(f"Sure!\n```json\n{FIXED}\n```\nGood luck") is not None
    assert parse(FIXED)["test_command"] == "python test_main.py"
    assert parse("no json here") is None
    assert parse('{"files": {}}') is None


async def test_sandbox_inside_a_denied_folder_still_works(tmp_path: Path) -> None:
    data = tmp_path / "data"
    sandbox = ProcessSandbox(data / "modules" / "code" / "sandbox", deny=[data])
    data.mkdir()
    (data / "stats.db").write_text("private")
    result = await sandbox.run(
        {"main.py": "from helper import x; print(x)", "helper.py": "x = 5"},
        ["python", "main.py"],
    )
    assert result.ok and result.stdout.strip() == "5"
    blocked = await sandbox.run(
        {"main.py": f"open({str(data / 'stats.db')!r})"}, ["python", "main.py"]
    )
    assert not blocked.ok and "outside the sandbox" in blocked.stderr
