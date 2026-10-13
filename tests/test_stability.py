"""Stability: files that are briefly locked, a model server that dies or stalls, and
`starlight doctor`."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths, fsutil
from starlight.core.conversations import ConversationStore
from starlight.core.jsonfile import load_json, save_json
from starlight.doctor import Check, exit_code, format_checks, run_checks
from starlight.models.openai_compat import OpenAICompatProvider
from tests.test_launcher import L, QuietUi

# -- files that are busy for a moment ------------------------------------------------------------


class Flaky:
    """os.replace that fails like Windows does while an antivirus holds the file."""

    def __init__(self, failures: int) -> None:
        self.failures, self.calls = failures, 0
        self.real = os.replace

    def __call__(self, source: Any, target: Any) -> None:
        self.calls += 1
        if self.calls <= self.failures:
            raise PermissionError(13, "being used by another process")
        self.real(source, target)


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behave as on Windows (a PermissionError is a busy file), without waiting long."""
    monkeypatch.setattr(fsutil, "is_transient", lambda error: isinstance(error, PermissionError))
    monkeypatch.setattr(fsutil, "DELAYS", (0.0, 0.0, 0.01, 0.01, 0.01, 0.01))


def test_a_save_waits_for_a_busy_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: None
) -> None:
    flaky = Flaky(failures=3)
    monkeypatch.setattr(fsutil.os, "replace", flaky)
    save_json(tmp_path / "todo.json", [{"text": "Steuer"}])
    assert load_json(tmp_path / "todo.json", []) == [{"text": "Steuer"}]
    assert flaky.calls == 4
    assert [p.name for p in tmp_path.iterdir()] == ["todo.json"]  # no temporary file left


def test_a_conversation_turn_survives_a_busy_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: None
) -> None:
    store = ConversationStore(tmp_path)
    name = store.create()
    monkeypatch.setattr(fsutil.os, "replace", Flaky(failures=2))
    store.rename(name, "Urlaub")
    assert store.get(name)["title"] == "Urlaub"


def test_a_file_that_stays_busy_fails_clearly_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows: None
) -> None:
    monkeypatch.setattr(fsutil.os, "replace", Flaky(failures=99))
    with pytest.raises(PermissionError):
        fsutil.write_atomic(tmp_path / "list.json", "[]")
    assert list(tmp_path.iterdir()) == []


def test_a_real_permission_problem_is_not_retried_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flaky = Flaky(failures=99)
    monkeypatch.setattr(fsutil.os, "replace", flaky)
    monkeypatch.setattr(fsutil, "is_transient", lambda error: False)  # Linux, macOS
    with pytest.raises(PermissionError):
        fsutil.write_atomic(tmp_path / "list.json", "[]")
    assert flaky.calls == 1


def test_the_launcher_waits_for_a_busy_download_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flaky = Flaky(failures=2)
    monkeypatch.setattr(L.os, "replace", flaky)
    monkeypatch.setattr(L.os, "name", "nt")
    monkeypatch.setattr(L.time, "sleep", lambda seconds: None)
    (tmp_path / "model.gguf.part").write_bytes(b"model")
    L.replace_file(tmp_path / "model.gguf.part", tmp_path / "model.gguf")
    assert (tmp_path / "model.gguf").read_bytes() == b"model" and flaky.calls == 3


# -- the model server dies -----------------------------------------------------------------------


class FakeProcess:
    def __init__(self, returncode: int | None = None) -> None:
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode


class FakeGroup:
    def __init__(self, crash_again: bool = True) -> None:
        self.started: list[FakeProcess] = []
        self.crash_again = crash_again

    def start(self, command: list[str], log: Path | None, cwd: Path) -> FakeProcess:
        process = FakeProcess(3 if self.crash_again else None)
        self.started.append(process)
        return process


def wait_until(condition: Callable[[], bool], seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class Said(QuietUi):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def say(self, text: str = "") -> None:
        self.lines.append(text)


def watchdog(group: FakeGroup, process: FakeProcess, ui: Said, **kwargs: Any) -> Any:
    return L.ModelServerWatchdog(
        group, ["serve"], process, Path("llama.log"), Path("."), ui,  # type: ignore[arg-type]
        poll=0.01, backoff=0.0, **kwargs,
    )  # fmt: skip


def test_a_crashed_model_server_is_started_again() -> None:
    group, ui = FakeGroup(crash_again=False), Said()
    dog = watchdog(group, FakeProcess(returncode=-1073741819), ui)
    dog.start()
    assert wait_until(lambda: len(group.started) == 1)
    dog.stop()
    assert any("stopped (exit code -1073741819)" in line for line in ui.lines)
    assert not dog.gave_up


def test_a_server_that_cannot_run_is_not_restarted_forever() -> None:
    group, ui = FakeGroup(crash_again=True), Said()
    dog = watchdog(group, FakeProcess(returncode=1), ui, limit=3)
    dog.start()
    assert wait_until(lambda: dog.gave_up)
    dog.stop()
    assert len(group.started) == 3
    assert any("stays down" in line for line in ui.lines)


def test_stopping_on_purpose_is_not_a_crash() -> None:
    group, ui = FakeGroup(), Said()
    process = FakeProcess(returncode=None)
    dog = watchdog(group, process, ui)
    dog.start()
    dog.stop()
    process.returncode = 0  # the launcher ended it
    time.sleep(0.1)
    assert group.started == [] and threading.active_count() >= 1


# -- the model server stalls ----------------------------------------------------------------------


def stalling(chunks: list[bytes], first_delay: float = 0.0) -> httpx.MockTransport:
    async def body() -> Any:
        await asyncio.sleep(first_delay)
        for chunk in chunks:
            yield chunk
        await asyncio.sleep(30)  # then nothing more, and the connection stays open

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":  # the chat request stalls; the health check answers
            return httpx.Response(200, content=body())
        return httpx.Response(200, json={"status": "ok"})

    return httpx.MockTransport(handler)


HELLO = b'data: {"choices":[{"delta":{"content":"Hello there, "}}]}\n\n'
CALL = (
    b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":'
    b'{"name":"hello__greet","arguments":"{\\"na"}}]}}]}\n\n'
)


async def test_a_stalled_answer_keeps_what_came_so_far(paths: Paths) -> None:
    provider = OpenAICompatProvider(transport=stalling([HELLO]), idle_timeout=0.2)
    pieces: list[str] = []
    started = time.monotonic()
    response = await provider.chat([], [], on_token=pieces.append)
    assert time.monotonic() - started < 5
    assert response.content == "Hello there, " and "".join(pieces) == "Hello there, "
    assert response.usage["finish_reason"] == "stalled"
    paths.config_file.write_text(
        "kill_switch:\n  hotkey: null\nmemory:\n  enabled: false\nskills:\n  enabled: false\n"
    )  # (no background model calls that would meet the stalled server too)
    async with Kernel(paths, provider=provider) as k:
        result = await k.ask("hi", on_token=lambda piece: None)  # as the chat page asks
    assert result.answer.startswith("Hello there, ")
    assert "stopped answering part way" in result.answer and not result.failed


async def test_a_half_written_tool_call_does_not_run() -> None:
    provider = OpenAICompatProvider(transport=stalling([CALL]), idle_timeout=0.2)
    response = await provider.chat([], [{"name": "hello.greet", "parameters": {}}], on_token=print)
    assert response.tool_calls == [] and response.usage["finish_reason"] == "stalled"


async def test_reading_a_long_prompt_may_take_longer_than_the_idle_limit() -> None:
    transport = stalling([HELLO], first_delay=0.5)  # before the first word: prompt processing
    provider = OpenAICompatProvider(transport=transport, idle_timeout=0.2)
    response = await provider.chat([], [], on_token=lambda piece: None)
    assert response.content == "Hello there, "


# -- starlight doctor -----------------------------------------------------------------------------


def names(results: list[Check], status: str) -> list[str]:
    return [c.name for c in results if c.status == status]


def test_doctor_on_a_fresh_install(paths: Paths) -> None:
    results = run_checks(paths, get=lambda url: (None, "refused"))
    assert names(results, "fail") == []
    assert "model" in names(results, "warn")  # the built-in test model
    assert exit_code(results) == 0
    assert "working, 1 thing(s) to look at" in format_checks(results)


def llamacpp_config(paths: Paths, model: Path) -> None:
    paths.config_file.write_text(
        "kill_switch:\n  hotkey: null\nmodel:\n  provider: llamacpp\n"
        "  options:\n    base_url: http://127.0.0.1:59999/v1\n"
        f"  server:\n    model_path: {model.as_posix()}\n"
    )


def test_doctor_names_a_missing_model_and_a_server_that_does_not_answer(
    paths: Paths, tmp_path: Path
) -> None:
    llamacpp_config(paths, tmp_path / "missing.gguf")
    (paths.logs / "llama-server.log").write_text(
        "load: ok\nggml_vulkan: Device memory allocation failed: out of memory\n"
    )
    results = run_checks(paths, get=lambda url: (None, "connection refused"))
    assert names(results, "fail") == ["model file", "model server"]
    text = format_checks(results)
    assert "missing.gguf is missing" in text
    assert "out of memory" in text and "starlight model serve" in text  # its log, and what to do
    assert exit_code(results) == 1


def test_doctor_is_happy_with_a_running_model(paths: Paths, tmp_path: Path) -> None:
    model = tmp_path / "qwen.gguf"
    model.write_bytes(b"x" * 1024)
    llamacpp_config(paths, model)
    seen: list[str] = []

    def get(url: str) -> tuple[int | None, str]:
        seen.append(url)
        return 200, ""

    results = run_checks(paths, get=get)
    assert names(results, "fail") == [] and "model" not in names(results, "warn")
    assert seen == ["http://127.0.0.1:59999/health"]


def test_doctor_says_when_the_model_is_still_loading(paths: Paths, tmp_path: Path) -> None:
    model = tmp_path / "qwen.gguf"
    model.write_bytes(b"x")
    llamacpp_config(paths, model)
    results = run_checks(paths, get=lambda url: (503, "loading"))
    assert "model server" in names(results, "warn") and exit_code(results) == 0


def test_doctor_reports_a_broken_settings_file_and_goes_on(paths: Paths) -> None:
    paths.config_file.write_text("model:\n  provider: echo\n  colour: blue\n")
    results = run_checks(paths, get=lambda url: (None, ""))
    assert names(results, "fail") == ["settings"] and "modules" in [c.name for c in results]
    assert exit_code(results) == 1


def test_doctor_points_out_list_files_that_were_moved_aside(paths: Paths) -> None:
    paths.data.mkdir(parents=True, exist_ok=True)
    (paths.data / "todo.unreadable-20261013-101500.json").write_text("[")
    results = run_checks(paths, get=lambda url: (None, ""))
    assert "damaged files" in names(results, "warn")


def test_the_doctor_command(paths: Paths, capsys: pytest.CaptureFixture[str]) -> None:
    from starlight.cli import main

    assert main(["--home", str(paths.root), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "[ok]" in out and "settings" in out and "disk space" in out
