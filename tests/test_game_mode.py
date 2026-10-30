"""Game mode: a game starting pauses the model (the launcher stops the server or unloads it
in LM Studio), closing it brings the model back, and Starlight answers meanwhile."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.cli import main
from starlight.core import Kernel, Paths
from starlight.core.modelpause import pause_model, pause_note, read_pause, resume_model
from tests.test_launcher import L
from tests.test_stability import FakeGroup, FakeProcess, Said, wait_until, watchdog

FOLDERS = ["steamapps/common", "Epic Games"]
IGNORE = ["steam.exe", "epicgameslauncher.exe"]


def find(processes: list[tuple[str, str]], games: list[str] | None = None) -> str:
    from modules.game.module import find_game

    return find_game(processes, FOLDERS, games or [], IGNORE)


def test_a_game_is_a_program_from_a_game_folder_or_one_you_named() -> None:
    steam = (
        "eldenring.exe",
        r"C:\Program Files (x86)\Steam\steamapps\common\ELDEN RING\eldenring.exe",
    )
    assert find([("notepad.exe", r"C:\Windows\notepad.exe"), steam]) == "eldenring.exe"
    launcher = ("Steam.exe", r"C:\Steam\steamapps\common\Steam.exe")
    assert find([launcher]) == ""  # in a game folder, but ignored
    assert find([("javaw.exe", r"C:\Java\bin\javaw.exe")]) == ""
    assert find([("javaw.exe", r"C:\Java\bin\javaw.exe")], games=["JAVAW.exe"]) == "javaw.exe"
    assert find([("", "/home/me/Epic Games/Fortnite/fortnite")]) == "fortnite"


@pytest.fixture
def game_project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("game")
    return paths


def instance(k: Kernel) -> Any:
    module = k.modules.records["game"].instance
    assert module is not None
    return module


async def test_a_game_pauses_the_model_and_closing_it_brings_it_back(game_project: Paths) -> None:
    async with Kernel(game_project) as k:
        game = instance(k)
        running: list[tuple[str, str]] = []
        game._find = lambda: find(running)
        delivered: list[str] = []

        async def deliver(text: str, title: str = "", **_: Any) -> None:
            delivered.append(text)

        game.ctx.deliver = deliver
        await game.check()
        assert read_pause(game_project) is None
        running.append(("game.exe", "D:/SteamLibrary/steamapps/common/Game/game.exe"))
        await game.check()
        pause = read_pause(game_project)
        assert pause is not None and pause["by"] == "game mode"
        assert "game.exe is running" in pause["reason"] and "paused" in delivered[-1]
        result = await k.ask("hello")  # no model call: a clear note instead
        assert result.failed and "paused to free the graphics card" in result.answer

        running.clear()
        await game.check()
        assert read_pause(game_project) is None and "closed" in delivered[-1]


async def test_resumed_by_you_while_a_game_runs_the_model_stays(game_project: Paths) -> None:
    async with Kernel(game_project) as k:
        game = instance(k)
        running = [("game.exe", "C:/Epic Games/Game/game.exe")]
        game._find = lambda: find(running)

        async def deliver(*_: Any, **__: Any) -> None:
            return None

        game.ctx.deliver = deliver
        await game.check()
        assert read_pause(game_project) is not None
        assert "loading again" in (await k.dispatcher.call("game.resume_model")).output
        await game.check()
        assert read_pause(game_project) is None  # not paused again for the same game
        running.clear()
        await game.check()
        running.append(("game.exe", "C:/Epic Games/Game/game.exe"))
        await game.check()
        assert read_pause(game_project) is not None  # started again: paused again


async def test_a_pause_you_made_is_not_ended_by_game_mode(game_project: Paths) -> None:
    async with Kernel(game_project) as k:
        game = instance(k)
        game._find = lambda: ""
        await k.dispatcher.call("game.pause_model", {"reason": "rendering a video"})
        await game.check()
        pause = read_pause(game_project) or {}
        assert pause.get("reason") == "rendering a video" and pause.get("by") == "you"


async def test_games_you_add_count(game_project: Paths) -> None:
    async with Kernel(game_project) as k:
        await k.dispatcher.call("game.add_game", {"program": r"C:\Games\minecraft.exe"})
        listed = (await k.dispatcher.call("game.games")).output
        assert "minecraft.exe" in listed["programs"]
        await k.dispatcher.call("game.remove_game", {"program": "MINECRAFT.EXE"})
        assert "minecraft.exe" not in (await k.dispatcher.call("game.games")).output["programs"]


def test_pause_and_resume_from_the_command_line(
    paths: Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--home", str(paths.root), "model", "pause"]) == 0
    assert read_pause(paths) is not None and "Starlight's model is paused" in pause_note(paths)
    assert main(["--home", str(paths.root), "model", "resume"]) == 0
    assert read_pause(paths) is None and pause_note(paths) == ""
    capsys.readouterr()
    main(["--home", str(paths.root), "model", "resume"])
    assert "not paused" in capsys.readouterr().out


# -- the launcher's side --------------------------------------------------------------------------


def test_the_launcher_reads_the_same_pause_file(paths: Paths) -> None:
    pause_model(paths, "game.exe is running", "game mode")
    assert L.pause_file(paths.root) == paths.data / "model.pause.json"
    assert (L.read_pause(paths.root) or {})["reason"] == "game.exe is running"
    assert L.end_pause(paths.root) and not resume_model(paths) and not L.end_pause(paths.root)


def test_the_watchdog_stops_the_server_while_paused(tmp_path: Path) -> None:
    pause = tmp_path / "model.pause.json"
    group, ui = FakeGroup(crash_again=False), Said()
    process = FakeProcess(returncode=None)
    ended: list[Any] = []

    def terminate(proc: FakeProcess) -> None:
        ended.append(proc)
        proc.returncode = 1

    group.terminate = terminate  # type: ignore[attr-defined]
    dog = watchdog(group, process, ui, pause=pause)
    dog.start()
    try:
        pause.write_text(json.dumps({"reason": "game"}))
        assert wait_until(lambda: dog.paused and ended == [process])
        assert group.started == []  # stopped on purpose: not restarted as a crash
        pause.unlink()
        assert wait_until(lambda: not dog.paused and len(group.started) == 1)
        assert dog.process is group.started[0] and not dog.gave_up
    finally:
        dog.stop()
    assert any("Game mode" in line for line in ui.lines)


def test_restarting_by_hand_ends_the_pause(tmp_path: Path) -> None:
    pause = tmp_path / "model.pause.json"
    pause.write_text("{}")
    group, ui = FakeGroup(crash_again=False), Said()
    group.terminate = lambda proc: None  # type: ignore[attr-defined]
    dog = watchdog(group, FakeProcess(returncode=None), ui, pause=pause)
    dog.restart_now()
    try:
        assert not pause.exists() and not dog.paused and len(group.started) == 1
    finally:
        dog.stop()


def test_lm_studio_unloads_and_loads_the_model_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    loaded = {"key": "qwen/qwen3-14b"}
    monkeypatch.setattr(
        L, "lmstudio_state", lambda url: L.LMStudioState(answers=True, loaded=loaded["key"])
    )

    def unload(url: str, instance: str) -> str:
        calls.append(("unload", instance))
        loaded["key"] = ""
        return ""

    def load(url: str, model: str, context: int, timeout: float = 600) -> str:
        calls.append(("load", model))
        return ""

    monkeypatch.setattr(L, "unload_in_lmstudio", unload)
    monkeypatch.setattr(L, "load_in_lmstudio", load)
    settings = type(
        "S", (), {"root": tmp_path, "lmstudio_url": "http://127.0.0.1:1234",
                  "lmstudio_model": "", "context": 16384},
    )()  # fmt: skip
    watcher = L.LMStudioPause(settings, Said())
    watcher.check()
    assert calls == []
    L.pause_file(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    L.pause_file(tmp_path).write_text("{}")
    watcher.check()
    watcher.check()  # still paused: nothing more
    assert calls == [("unload", "qwen/qwen3-14b")]
    L.end_pause(tmp_path)
    watcher.check()
    assert calls == [("unload", "qwen/qwen3-14b"), ("load", "qwen/qwen3-14b")]


def test_the_status_says_paused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.test_lmstudio import settings_for, starlight_root

    settings = settings_for(starlight_root(tmp_path), monkeypatch, "--lmstudio")
    monkeypatch.setattr(L, "fetch_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(L, "port_in_use", lambda port: False)
    services = L.Services(settings, Said(), installed=None)
    assert services.poll().model == "waiting"
    L.pause_file(settings.root).parent.mkdir(parents=True, exist_ok=True)
    L.pause_file(settings.root).write_text(json.dumps({"reason": "game.exe is running"}))
    status = services.poll()
    assert status.model == "paused" and "game.exe is running" in status.model_detail
    assert services.resume_model() and services.poll().model == "waiting"
