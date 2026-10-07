"""Regression tests for bugs found in the recheck. Each one failed before its fix."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path
from unittest import mock

import httpx
import pytest

from starlight.core import Kernel, Paths
from starlight.core import logging as slog
from starlight.core.killswitch import KillSwitch
from starlight.core.model import Message, ModelResponse
from starlight.core.vault import MemoryBackend, Vault, VaultError
from starlight.models.router import RouterProvider
from starlight.sandbox import ProcessSandbox
from starlight.viewer import StatsReader
from tests.conftest import REPO, ModuleWriter, read_audit

INDICATOR = REPO / "modules" / "desktop" / "indicator.py"


async def test_resume_from_another_terminal_releases_the_running_core(kernel: Kernel) -> None:
    stop_file = kernel.paths.data / "STOP"
    KillSwitch(stop_file).trigger("starlight kill", source="cli")  # another process
    await asyncio.sleep(0.1)
    assert (await kernel.ask("hello")).stopped
    KillSwitch(stop_file).reset()  # `starlight resume` in another terminal
    result = await kernel.ask("hello")
    assert not result.stopped and result.answer == "You said: hello"


async def test_kill_switch_holds_when_the_stop_file_cannot_be_written(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path / "STOP")
    with mock.patch.object(Path, "write_text", side_effect=PermissionError("read-only")):
        switch.trigger("hotkey")
    assert switch.engaged  # no file, but this process must still stay stopped
    switch.reset()
    assert not switch.engaged


async def test_kill_from_another_thread_keeps_its_reason(tmp_path: Path) -> None:
    """The failsafe and the hotkey fire from their own threads while the watcher polls the
    STOP file; the watcher used to win and record "STOP file present" (CI caught it)."""
    for i in range(100):
        switch = KillSwitch(tmp_path / f"STOP{i}")
        watcher = asyncio.create_task(switch.watch(interval=0))
        await asyncio.sleep(0)
        thread = threading.Thread(target=switch.trigger, args=("failsafe: corner", "failsafe"))
        thread.start()
        while thread.is_alive():
            await asyncio.sleep(0)
        watcher.cancel()
        assert switch.reason == "failsafe: corner"
        assert (tmp_path / f"STOP{i}").read_text(encoding="utf-8").strip() == "failsafe: corner"


async def test_core_starts_and_answers_when_the_embedding_model_is_down(paths: Paths) -> None:
    paths.config_file.write_text(
        paths.config_file.read_text()
        + "memory:\n  embedder:\n    provider: ollama\n    base_url: http://127.0.0.1:9\n"
    )
    (paths.memory / "nas.md").write_text("---\ntopic: NAS\n---\nThe NAS is at 192.168.1.20.\n")
    async with Kernel(paths) as kernel:
        result = await kernel.ask("hello")
    assert result.answer == "You said: hello"


async def test_two_requests_enabling_a_module_load_it_once(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module(
        "slowstart",
        """
        import asyncio
        from starlight.core import Module, tool

        class Mod(Module):
            async def setup(self, ctx):
                with open(ctx.data_dir / "starts", "a") as f:  # each load re-imports
                    f.write("x")
                await asyncio.sleep(0.1)

            @tool
            def ping(self) -> str:
                return "pong"
    """,
    )
    async with Kernel(paths) as kernel:
        await kernel.modules.disable("slowstart")
        kernel.modules.state.set("slowstart", True)  # enabled from another terminal
        await asyncio.gather(kernel.ask("terminal"), kernel.ask("whatsapp"))
        record = kernel.modules.records["slowstart"]
        assert record.status.value == "loaded", record.error
        assert (await kernel.dispatcher.call("slowstart.ping")).output == "pong"
        starts = (paths.data / "modules" / "slowstart" / "starts").read_text()
        assert len(starts) == 2  # the first start and one re-enable, not three


async def test_router_keeps_each_request_on_its_own_model() -> None:
    class Model:
        supports_streaming = False

        def __init__(self, label: str) -> None:
            self.name = self.label = label

        async def chat(self, messages: list[Message], tools: object) -> ModelResponse:
            await asyncio.sleep(0.02)
            text = messages[-1].content
            if text.startswith("Classify"):
                return ModelResponse("SIMPLE" if text.endswith("hi") else "COMPLEX")
            return ModelResponse(self.label)

    router = RouterProvider(Model("fast"), Model("main"))  # type: ignore[arg-type]

    async def request(text: str) -> list[str]:
        steps = [(await router.chat([Message("user", text)], [])).content]
        await asyncio.sleep(0.1)  # a tool runs; the other request is classified meanwhile
        steps.append(
            (await router.chat([Message("user", text), Message("tool", "ok")], [])).content
        )
        return steps

    simple, complex_ = await asyncio.gather(request("hi"), request("plan my whole week"))
    assert simple == ["fast", "fast"] and complex_ == ["main", "main"]


def test_background_start_without_a_console(tmp_path: Path) -> None:
    """pythonw.exe has no stdout/stderr; the web server used to crash on start."""
    code = textwrap.dedent(f"""
        import sys
        sys.stdout = None
        sys.stderr = None
        from pathlib import Path
        from starlight.cli import _ensure_console
        from starlight.core import Paths
        _ensure_console(Paths(Path({str(tmp_path)!r})))
        import uvicorn
        from fastapi import FastAPI
        uvicorn.Config(FastAPI(), log_level="warning", log_config=None).load()
        print("started")
    """)
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "started" in (tmp_path / "logs" / "console.log").read_text()


def test_log_lines_survive_a_locked_file_on_windows(tmp_path: Path) -> None:
    slog.setup_logging(tmp_path, max_bytes=200, backups=2)
    log = slog.get_logger("test")

    def locked(src: object, dst: object) -> None:
        raise PermissionError(32, "being used by another process")

    console = io.StringIO()
    with (
        contextlib.redirect_stderr(console),
        mock.patch("os.rename", locked),
        mock.patch("os.replace", locked),
    ):
        for i in range(20):
            log.info("event", i=i, filler="x" * 50)
    assert len((tmp_path / "starlight.log").read_text().splitlines()) == 20
    assert "Logging error" not in console.getvalue()
    slog._State.handlers["starlight"]._next_try = 0  # type: ignore[attr-defined]
    log.info("after the other process let go")
    assert (tmp_path / "starlight.log.1").exists()  # rotation resumed


async def test_sandbox_allows_threads_caps_memory_and_runs_pytest(tmp_path: Path) -> None:
    sandbox = ProcessSandbox(tmp_path, deny=[], memory_mb=256)
    threads = await sandbox.run(
        {
            "main.py": "import threading\nt = threading.Thread(target=print, args=('ok',))\n"
            "t.start(); t.join()"
        },
        ["python", "main.py"],
    )
    assert threads.ok and threads.stdout.strip() == "ok"
    if sys.platform != "win32":
        big = await sandbox.run({"main.py": "x = bytearray(600 * 2**20)"}, ["python", "main.py"])
        assert not big.ok and "MemoryError" in big.stderr
    tests = await sandbox.run(
        {"test_a.py": "def test_x():\n    assert 1 + 1 == 2\n"},
        ["pytest", "-q", "-p", "no:cacheprovider"],
    )
    assert tests.ok, tests.summary()


async def test_binary_pages_are_not_given_to_the_model(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("web")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            content=b"%PDF-1.7\n" + bytes(range(256)) * 20,
            headers={"content-type": "application/pdf"},
        )

    async with Kernel(paths, http_transport=httpx.MockTransport(handler)) as kernel:
        result = await kernel.dispatcher.call("web.fetch", {"url": "https://x.example/a.pdf"})
    assert result.ok and "text" not in result.output
    assert result.output["content_type"] == "application/pdf"


async def test_typing_is_not_reported_as_failed_without_ocr(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    from tests.test_desktop import FakeDesktop, Yes, install

    use_module("desktop")
    (paths.module_configs / "desktop.yaml").write_text("indicator: false\nsettle: 0.01\n")
    fake = FakeDesktop()
    async with Kernel(paths, ui=Yes()) as kernel:
        module = install(kernel, fake)

        class NoTesseract:
            def boxes(self, image: object) -> list[object]:
                from starlight_modules.desktop.backends import DesktopUnavailable

                raise DesktopUnavailable("Tesseract not found")

        module.ocr = NoTesseract()
        result = await kernel.dispatcher.call(
            "desktop.type_text", {"text": "hello", "expect_text": "hello"}
        )
    assert result.ok and fake.text == "hello"  # typed once, and reported as done
    assert result.output["verified"] is None and "Tesseract" in result.output["note"]


async def test_the_test_model_learns_no_skills_or_memories(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("hello")
    async with Kernel(paths) as kernel:  # echo stand-in
        for _ in range(3):
            await kernel.ask('!hello.greet {"name": "Paul"}')
            await kernel.drain()
        assert kernel.skills.all() == [] and kernel.memory.store.all() == []


async def test_second_core_leaves_background_parts_and_takes_over(paths: Paths) -> None:
    stop_file = paths.data / "STOP"
    reader = StatsReader(paths.stats_db)
    first = Kernel(paths)
    await first.start()
    second = Kernel(paths)  # `starlight run` while `starlight serve` runs
    second.takeover_interval = 0.05
    await second.start()
    assert first.services.primary and not second.services.primary
    log = (paths.logs / "starlight.log").read_text()
    assert log.count("another Starlight core runs the webhook") == 1  # only the second

    await second.stop()  # a second core exiting must not say Starlight stopped
    assert reader.summary(60, stop_file)["core"]["state"] == "running"
    second = Kernel(paths)
    second.takeover_interval = 0.05
    await second.start()
    await first.stop()  # the background core exits: the other one takes over
    for _ in range(100):
        if second.services.primary:
            break
        await asyncio.sleep(0.02)
    assert second.services.primary
    await asyncio.sleep(0.05)
    assert reader.summary(60, stop_file)["core"]["state"] == "running"
    await second.stop()
    assert reader.summary(60, stop_file)["core"]["state"] == "offline"


async def test_serve_stops_cleanly_on_a_stop_request(paths: Paths) -> None:
    from starlight.cli import cmd_serve

    task = asyncio.create_task(cmd_serve(paths, argparse.Namespace()))
    await asyncio.sleep(1.0)
    started = time.monotonic()
    (paths.data / "SHUTDOWN.core").write_text("stop\n")  # `starlight service stop`
    assert await asyncio.wait_for(task, 5) == 0
    assert time.monotonic() - started < 3
    assert not (paths.data / "SHUTDOWN.core").exists()
    assert "core.stopped" in (paths.logs / "audit.log").read_text()


def test_vault_explains_the_windows_size_limit(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "index.yaml", MemoryBackend())
    with mock.patch("starlight.core.vault.sys.platform", "win32"):
        with pytest.raises(VaultError, match="Credential Manager"):
            vault.set("ssh.big", "x" * 3200)  # e.g. an RSA 4096 key
        vault.set("ssh.small", "x" * 400)  # an ed25519 key fits
    assert vault.get("ssh.small") == "x" * 400


def test_docker_in_windows_containers_mode_is_not_used() -> None:
    """Docker Desktop switched to Windows containers cannot run the Python image; the code
    module must fall back to the process sandbox instead of failing every run (CI caught it
    on a Windows runner)."""
    from starlight import sandbox

    def docker_info(os_type: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["docker", "info"], 0, stdout=f"{os_type}\n")

    with mock.patch.object(sandbox.shutil, "which", return_value="docker"):
        with mock.patch.object(sandbox.subprocess, "run", return_value=docker_info("windows")):
            assert not sandbox.docker_available()
            assert isinstance(
                sandbox.create_sandbox("auto", Path("w"), deny=[]), sandbox.ProcessSandbox
            )
        with mock.patch.object(sandbox.subprocess, "run", return_value=docker_info("linux")):
            assert sandbox.docker_available()


BANNER_CHILD = """
import gc, importlib.util, sys, threading, time
from starlight.core.banner import banner

def indicator_module():  # a module reload imports the file again
    spec = importlib.util.spec_from_file_location("indicator", sys.argv[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

first = indicator_module().Indicator(True, linger=0.05)
first.hide(immediately=True)  # the kill switch, with nothing on screen
first.close()
assert banner()._thread is None, "hiding alone opened a window"
for round in range(3):
    indicator = indicator_module().Indicator(True, hotkey=f"Ctrl+Alt+{round}", linger=0.05)
    indicator.show()
    time.sleep(0.5)
    if not banner().available:
        print("NO DISPLAY")
        sys.exit(0)
    indicator.hide()
    time.sleep(0.3)
    indicator.close()
    del indicator
    gc.collect()  # in the main thread, as anywhere else in Starlight
threads = [t for t in threading.enumerate() if t.name == "banner"]
assert len(threads) == 1, threads
print("OK")
"""


def test_banner_survives_reloads_and_garbage_collection(tmp_path: Path) -> None:
    """Windows CI died mid-run with "Tcl_AsyncDelete: async handler deleted by the wrong
    thread": the desktop banner's Tk window sat in a reference cycle, and the garbage
    collector freed it from another thread, which makes Tcl abort the process. Starting
    a second Tk window in a new thread afterwards crashed as well. It runs in a child
    process, so a crash fails this test instead of ending the test run."""
    pytest.importorskip("tkinter")
    env = dict(os.environ)
    xvfb = None
    if sys.platform != "win32" and sys.platform != "darwin" and not env.get("DISPLAY"):
        if not shutil.which("Xvfb"):
            pytest.skip("no display and no Xvfb")
        env["DISPLAY"] = ":78"
        xvfb = subprocess.Popen(
            ["Xvfb", ":78", "-screen", "0", "1024x768x24"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.8)
    try:
        result = subprocess.run(
            [sys.executable, "-X", "faulthandler", "-c", BANNER_CHILD, str(INDICATOR)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=tmp_path,
        )
    finally:
        if xvfb is not None:
            xvfb.terminate()
            xvfb.wait()
    output = result.stdout + result.stderr
    if "NO DISPLAY" in output:
        pytest.skip("no display")
    assert result.returncode == 0 and "OK" in output, output[-3000:]


def _sse(*chunks: dict[str, object]) -> bytes:
    import json

    return b"".join(f"data: {json.dumps(c)}\n\n".encode() for c in chunks) + b"data: [DONE]\n\n"


async def test_an_answer_that_never_ends_is_cut_off(paths: Paths) -> None:
    """Found in real use: a model answered "What model are you" with 1300+ tokens and kept
    going; nothing limited the answer, so it would have run until the context was full.
    Answers now stop at model.reserve_tokens, and the user is told."""
    import json

    from starlight.core.agent import CUT_OFF
    from starlight.core.config import ModelConfig
    from starlight.core.model import create_provider

    sent: list[dict[str, object]] = []

    def server(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"content": "I am a model and "}}]},
                {
                    "choices": [
                        {"delta": {"content": "I am a model and"}, "finish_reason": "length"}
                    ]
                },
            ),
        )

    config = ModelConfig(provider="llamacpp", options={"transport": httpx.MockTransport(server)})
    provider = create_provider(config)
    streamed: list[str] = []
    async with Kernel(paths, provider=provider) as kernel:
        result = await kernel.ask("What model are you", on_token=streamed.append)
    assert sent[0]["max_tokens"] == 4096  # model.reserve_tokens
    assert result.answer.endswith(CUT_OFF)
    assert "".join(streamed).endswith(CUT_OFF)  # shown while streaming too

    # your own limit wins, and the models behind the router are capped as well
    custom = create_provider(ModelConfig(provider="llamacpp", options={"max_tokens": 100}))
    assert custom.max_tokens == 100  # type: ignore[attr-defined]
    router = create_provider(
        ModelConfig(
            provider="router",
            options={"fast": {"provider": "ollama"}, "main": {"provider": "llamacpp"}},
        )
    )
    assert router.fast.options["num_predict"] == 4096  # type: ignore[attr-defined]
    assert router.main.max_tokens == 4096  # type: ignore[attr-defined]


async def test_ctrl_c_stops_the_answer_not_starlight(paths: Paths) -> None:
    """Ctrl+C used to end Starlight. While an answer is being written it now stops only
    that answer; the next question works."""
    import signal

    from starlight.cli import _ask

    class SlowThenQuick:
        name = "slow"
        supports_streaming = True
        calls = 0

        async def chat(self, messages: object, tools: object, on_token: object = None) -> object:
            from starlight.core.model import ModelResponse

            type(self).calls += 1
            if type(self).calls == 1:
                await asyncio.sleep(60)  # writing forever
            return ModelResponse(content="Quick answer.")

    async def press_ctrl_c() -> None:
        await asyncio.sleep(0.5)
        signal.raise_signal(signal.SIGINT)

    before = signal.getsignal(signal.SIGINT)
    async with Kernel(paths, provider=SlowThenQuick()) as kernel:
        presser = asyncio.create_task(press_ctrl_c())
        stopped = await asyncio.wait_for(_ask(kernel, "Write forever", lambda t: None), 30)
        await presser
        after = await _ask(kernel, "Something short", lambda t: None)
    assert stopped is None
    assert after is not None and after.answer == "Quick answer."
    assert signal.getsignal(signal.SIGINT) is before  # Ctrl+C behaves as usual again
    assert "task.cancelled" in read_audit(paths)


def test_run_says_when_the_kill_switch_is_still_on(
    paths: Paths, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Found in real use: after a restart the kill switch was still on, and the first
    request was "killed" without a word of explanation."""
    from starlight.cli import main

    home = str(paths.root)
    assert main(["--home", home, "kill", "REPL /kill"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("/quit\n"))
    assert main(["--home", home, "run"]) == 0
    out = capsys.readouterr().out
    assert "kill switch" in out and "ON" in out and "REPL /kill" in out and "resume" in out


async def test_refusals_and_config_errors_are_reported_for_what_they_are(
    paths: Paths, write_module: ModuleWriter
) -> None:
    """Found in real use: a broken servers.yaml came back as "invalid arguments" (the
    dispatcher took any validation error inside a tool for bad arguments), and every
    refusal was logged as a crash with a traceback."""
    import json

    write_module(
        "probe",
        """
        from pydantic import BaseModel
        from starlight.core import Module, ToolError, tool
        from starlight.core.config import ConfigError

        class Setting(BaseModel):
            host: str

        class Mod(Module):
            @tool
            def refuse(self) -> str:
                raise ToolError("not allowed: that server is not listed")

            @tool
            def bad_file(self) -> str:
                raise ConfigError("config/x.yaml: invalid YAML")

            @tool
            def bad_data(self, count: int) -> str:
                Setting.model_validate({})  # a validation error inside the tool
                return "never"
        """,
    )
    async with Kernel(paths) as kernel:
        refused = await kernel.dispatcher.call("probe.refuse")
        config = await kernel.dispatcher.call("probe.bad_file")
        inside = await kernel.dispatcher.call("probe.bad_data", {"count": 1})
        wrong_args = await kernel.dispatcher.call("probe.bad_data", {"count": "many"})
    assert refused.error == "not allowed: that server is not listed"
    assert config.error == "configuration error: config/x.yaml: invalid YAML"
    assert inside.error is not None and not inside.error.startswith("invalid arguments")
    assert wrong_args.error is not None and wrong_args.error.startswith("invalid arguments")
    log = [json.loads(line) for line in (paths.logs / "starlight.log").read_text().splitlines()]
    refusals = [e for e in log if e["event"] == "tool refused"]
    assert refusals and refusals[0]["level"] == "warning" and "exception" not in refusals[0]


async def test_servers_yaml_mistakes_get_a_clear_answer(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    """The exact edits from a real session: "localhost:" with nothing under it, and an
    inline mapping missing its comma; plus a server that simply is not listed."""
    use_module("network")
    servers = paths.config / "servers.yaml"
    async with Kernel(paths) as kernel:
        servers.write_text("servers:\n  localhost:\n")
        empty = await kernel.dispatcher.call("network.servers")
        servers.write_text("servers: {localhost:\n    url: http://127.0.0.1:8765/\n")
        broken = await kernel.dispatcher.call("network.ping", {"server": "127.0.0.1"})
        servers.write_text("servers:\n  # nothing yet\n")
        none_yet = await kernel.dispatcher.call("network.servers")
        unlisted = await kernel.dispatcher.call("network.status", {"server": "localhost"})
    assert empty.error is not None and "needs at least a host" in empty.error
    assert not empty.error.startswith("invalid arguments")
    assert broken.error is not None and "invalid YAML" in broken.error
    assert none_yet.ok and none_yet.output == []
    assert unlisted.error is not None and "servers:\n  localhost:\n    host: localhost" in (
        unlisted.error
    )


def test_shipped_servers_yaml_allows_this_pc() -> None:
    from starlight.core.config import read_yaml

    data = read_yaml(REPO / "config" / "servers.yaml")
    assert data["servers"]["localhost"]["host"] == "127.0.0.1"
