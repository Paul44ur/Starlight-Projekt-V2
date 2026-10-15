"""Fixes from the front-end cleanup: `starlight ask` and its exit code, Ctrl+C at the chat
prompt, the terminal client's kill switch messages, the doctor's model address and the
systemd unit text."""

from __future__ import annotations

import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import pytest

from starlight.cli import main
from starlight.core import Paths
from starlight.doctor import Check, run_checks
from starlight.service import linux_plan, systemd_unit
from starlight.web import client
from tests.test_tui import _minimal_home

PROMPT = "you \u203a"  # the chat prompt of a real terminal

# -- `starlight ask` ---------------------------------------------------------------------------


def test_ask_fails_with_a_code_when_the_model_server_is_down(
    paths: Paths, capsys: pytest.CaptureFixture[str]
) -> None:
    paths.config_file.write_text(
        "kill_switch:\n  hotkey: null\nmodel:\n  provider: llamacpp\n"
        "  options:\n    base_url: http://127.0.0.1:1/v1\n"
    )
    assert main(["--home", str(paths.root), "ask", "hi"]) == 1
    captured = capsys.readouterr()
    assert "is not available" in captured.err and captured.out == ""  # scripts see the failure


# -- Ctrl+C at the chat prompt -----------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminals are POSIX")
def test_ctrl_c_at_the_prompt_leaves_cleanly(tmp_path: Path) -> None:
    import pty

    home = _minimal_home(tmp_path / "home")
    master, slave = pty.openpty()
    env = dict(os.environ, TERM="xterm-256color", COLUMNS="100", LINES="40")
    env.pop("STARLIGHT_HOME", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "starlight", "--home", str(home), "run"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        cwd=str(home),
        # a job started in the background of a shell inherits "ignore SIGINT", and Python then
        # never turns Ctrl+C into KeyboardInterrupt: give the child the normal handling
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )
    os.close(slave)
    output = ""
    try:
        end = time.monotonic() + 60
        while PROMPT not in output and time.monotonic() < end:
            ready, _, _ = select.select([master], [], [], 0.5)
            if ready:
                try:
                    output += os.read(master, 4096).decode(errors="replace")
                except OSError:  # the program ended: the assertion below shows why
                    break
        assert PROMPT in output, output[-2000:]
        time.sleep(0.3)  # the prompt is up: the read is waiting
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=30) == 0  # like /quit, not the 130 of an interrupted run
    finally:
        process.kill()
        process.wait()
        os.close(master)


# -- `starlight connect`: /kill and /resume -----------------------------------------------------


class Screen:
    def __init__(self) -> None:
        self.shown: list[tuple[str, str]] = []

    def info(self, text: str) -> None:
        self.shown.append(("info", text))

    def error(self, text: str) -> None:
        self.shown.append(("error", text))


def backend_says(monkeypatch: pytest.MonkeyPatch, status: int, body: dict[str, Any]) -> None:
    real = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )


async def test_resume_refused_by_the_backend_is_not_reported_as_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_says(monkeypatch, 403, {"detail": "only on the PC itself"})
    screen = Screen()
    await client._post("http://127.0.0.1:8765", "/api/resume", screen)
    assert [kind for kind, _ in screen.shown] == ["error"]
    assert "403" in screen.shown[0][1] and "only on the PC itself" in screen.shown[0][1]


async def test_kill_and_resume_report_success(monkeypatch: pytest.MonkeyPatch) -> None:
    backend_says(monkeypatch, 200, {"engaged": True})
    screen = Screen()
    await client._post("http://127.0.0.1:8765", "/api/kill", screen)
    await client._post("http://127.0.0.1:8765", "/api/resume", screen)
    assert screen.shown == [("info", "Kill switch engaged."), ("info", "Kill switch released.")]


# -- doctor: the model server's address ----------------------------------------------------------


def server_checks(paths: Paths, provider: str, status: int | None) -> tuple[list[str], list[Check]]:
    paths.config_file.write_text(f"kill_switch:\n  hotkey: null\nmodel:\n  provider: {provider}\n")
    seen: list[str] = []

    def get(url: str) -> tuple[int | None, str]:
        seen.append(url)
        return status, "" if status else "refused"

    return seen, run_checks(paths, get=get)


def test_doctor_uses_the_providers_default_address(paths: Paths) -> None:
    seen, results = server_checks(paths, "llamacpp", 200)
    assert seen == ["http://127.0.0.1:8080/health"]
    assert not [c for c in results if c.name == "model server" and c.status != "ok"]


def test_doctor_asks_another_openai_server_for_its_model_list(paths: Paths) -> None:
    seen, results = server_checks(paths, "openai_compat", 404)  # e.g. no /v1/models either
    assert seen == ["http://127.0.0.1:8080/v1/models"]
    assert [c.status for c in results if c.name == "model server"] == ["ok"]
    _, down = server_checks(paths, "openai_compat", None)
    assert [c.status for c in down if c.name == "model server"] == ["fail"]


# -- the systemd unit ------------------------------------------------------------------------


def test_systemd_unit_quotes_a_path_with_a_space() -> None:
    # Pure POSIX paths: a unit is only ever written on Linux, and a Windows Path would
    # bring backslashes (which systemd reads as escapes) into the expected text.
    unit = systemd_unit(PurePosixPath("/home/o'brien/My Starlight"), "core")
    start = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert '"/home/o\'brien/My Starlight"' in start and start.endswith(" serve")
    assert "WorkingDirectory=/home/o'brien/My Starlight\n" in unit
    plain = next(
        line
        for line in systemd_unit(PurePosixPath("/srv/starlight"), "viewer").splitlines()
        if line.startswith("ExecStart=")
    )
    assert "--home /srv/starlight viewer" in plain and '"/srv' not in plain
    percent = systemd_unit(PurePosixPath("/srv/100%/$home"), "core")
    assert "--home /srv/100%%/$$home serve" in percent  # systemd would expand these itself


def test_linux_plan_needs_no_data_folder() -> None:
    assert linux_plan(Path("/srv/starlight"), "start").commands[0][:3] == [
        "systemctl",
        "--user",
        "start",
    ]


# -- the browser: a crashed Chromium must not leave its driver running -------------------------


class FakeChromium:
    def __init__(self, drivers: list[Any]) -> None:
        self.drivers = drivers
        self.browsers: list[Any] = []

    async def launch(self, **options: Any) -> Any:
        browser = type("FakeBrowser", (), {})()
        browser.connected = True
        browser.is_connected = lambda: browser.connected

        async def close() -> None:
            if not browser.connected:
                raise RuntimeError("Target closed")  # what Playwright says for a dead browser
            browser.connected = False

        browser.close = close
        self.browsers.append(browser)
        return browser


class FakeDriver:
    def __init__(self, drivers: list[Any]) -> None:
        self.chromium = FakeChromium(drivers)
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


async def test_a_crashed_browser_gets_its_old_driver_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import types

    from starlight.browser import Browser

    drivers: list[FakeDriver] = []

    class Starter:
        async def start(self) -> FakeDriver:
            drivers.append(FakeDriver(drivers))
            return drivers[-1]

    module = types.ModuleType("playwright.async_api")
    module.async_playwright = Starter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "playwright.async_api", module)

    browser = Browser(None)
    first = await browser._ensure()
    assert await browser._ensure() is first and len(drivers) == 1  # still connected: reused
    first.connected = False  # Chromium crashed
    second = await browser._ensure()
    assert second is not first and len(drivers) == 2
    assert drivers[0].stopped and not drivers[1].stopped  # no driver process left behind
    await browser.close()
    assert drivers[1].stopped


def test_the_statistics_page_shows_the_configured_request_cap(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from starlight.viewer.app import create_app

    home = _minimal_home(tmp_path / "home")
    (home / "config" / "starlight.local.yaml").write_text("http:\n  max_requests_per_second: 7\n")
    from starlight.core import Paths

    page = TestClient(create_app(Paths(home))).get("/").text
    assert "const WEB_CAP = 7;" in page and "__WEB_CAP__" not in page
