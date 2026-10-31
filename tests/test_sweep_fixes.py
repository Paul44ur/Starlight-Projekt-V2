"""Regression tests for bugs the obscure-code sweep fixed (docs/obscure-code-2026-10.md)
that had no test of their own: schedules, secret masking, the remembered hosts, ping
answers, UTF-16 files, login.yaml, the launcher's space check, pending_shutdown.json, the
window's buttons and e2e_chat's tool check. Each one fails with the code before its fix."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest

from starlight.core import Kernel, Paths
from starlight.core.vault import MemoryBackend
from tests.conftest import REPO
from tests.test_home_devices import call, fails, instance
from tests.test_launcher import L
from tests.test_mail import User

# -- core ------------------------------------------------------------------------------------


def test_weekly_without_a_day_asks_which_day() -> None:
    from starlight.core.schedule import ScheduleError, parse_schedule

    for text in ("weekly at 9:00", "every week at 9:00"):
        with pytest.raises(ScheduleError, match="say which day"):
            parse_schedule(text)  # it used to run every day at 9:00
    assert parse_schedule("every week on monday at 8:00").describe() == "Monday at 08:00"
    assert parse_schedule("weekly on friday 18:00").describe() == "Friday at 18:00"


def test_camel_case_and_plural_secret_keys_are_masked() -> None:
    from starlight.core.logging import REDACTED, redact

    masked = redact(
        {"accessToken": "abcdefgh", "clientSecret": "x", "X-Api-Key": "k", "secrets": ["a"]}
    )
    assert all(value == REDACTED for value in masked.values()), masked
    kept = {"maxTokens": 5, "promptTokens": 7, "passage": "x", "tokenCount": 3,
            "token_type": "Bearer", "password_length": 12}  # fmt: skip
    assert redact(kept) == kept  # about a secret, not one


def test_the_host_used_longest_ago_is_forgotten_first(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlight.core import http

    monkeypatch.setattr(http, "MAX_HOSTS", 3)
    polite = http.PoliteHttp(http.HttpConfig(), transport=httpx.MockTransport(lambda r: None))
    for name in ("a", "b", "c"):
        polite._host(f"https://{name}.test/")
    polite._host("https://a.test/page")  # a is used again ...
    polite._host("https://d.test/")  # ... so b, not a, makes room for d
    assert list(polite._hosts) == ["https://c.test", "https://a.test", "https://d.test"]


# -- modules ---------------------------------------------------------------------------------


class FakePing:
    """What asyncio.create_subprocess_exec gives back, for a ping that printed `output`."""

    def __init__(self, returncode: int, output: str) -> None:
        self.returncode, self.output = returncode, output.encode()

    async def communicate(self) -> tuple[bytes, bytes]:
        return self.output, b""

    async def wait(self) -> int:
        return self.returncode


@pytest.mark.parametrize("module", ["lan", "recon", "network"])
async def test_a_routers_unreachable_answer_does_not_count_as_up(
    module: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = importlib.import_module(f"modules.{module}.module")
    owner, method = {"lan": ("LanModule", "ping"), "recon": ("ReconModule", "_ping"),
                     "network": ("NetworkModule", "_ping")}[module]  # fmt: skip
    ping = getattr(getattr(code, owner), method)
    me = SimpleNamespace(config=SimpleNamespace(timeout=1))  # (all the methods look at)

    async def answered(output: str, returncode: int = 0) -> bool:
        async def start(*command: Any, **options: Any) -> FakePing:
            return FakePing(returncode, output)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
        result = await ping(me, "192.168.1.7")
        return bool(result["up"] if isinstance(result, dict) else result)

    # Windows' ping exits with 0 for both; only a real reply carries a TTL or a time.
    assert not await answered("Reply from 192.168.1.1: Destination host unreachable.")
    assert not await answered("Antwort von 192.168.1.1: Zielhost nicht erreichbar.")
    assert await answered("Antwort von 192.168.1.7: Bytes=32 Zeit<1ms TTL=64")
    assert await answered("64 bytes from 192.168.1.7: icmp_seq=1 ttl=64 time=0.4 ms")
    assert not await answered("1 packets transmitted, 0 received", returncode=1)


async def test_utf16_text_from_powershell_reads_as_text(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    use_module("files")
    folder = documents / "Starlight"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ps.txt").write_bytes("Größe: 12 MB\r\n".encode("utf-16"))  # with its BOM
    async with Kernel(paths, ui=User()) as k:
        read = await call(k, "files.read", path="ps.txt")
    assert read["content"].strip() == "Größe: 12 MB"


async def test_a_login_yaml_with_a_mistake_is_refused_not_written_over(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("login")
    config = paths.module_configs / "login.yaml"
    config.write_text("sites:\n  shop:\n    url: https://x.test/in\n", encoding="utf-8")
    user = User(True)
    async with Kernel(paths, ui=user, vault_backend=MemoryBackend()) as k:
        broken = "sites:\n  shop:\n    url: https://x.test/in\n  bank: {}\n"  # bank: no url
        config.write_text(broken, encoding="utf-8")
        error = await fails(
            k, "login.add_site", site="mail", url="https://m.test", username="u", password="p"
        )
    assert "has a mistake" in error and "bank" in error
    assert config.read_text(encoding="utf-8") == broken  # the other sites are not lost
    assert user.questions == []  # refused before asking


async def test_a_damaged_pending_shutdown_file_does_not_stop_the_module(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("system")
    saved = paths.module_data("system") / "pending_shutdown.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps({"action": "shutdown", "due": "soon"}), encoding="utf-8")
    async with Kernel(paths, ui=User()) as k:
        instance(k, "system")  # loaded: the damaged "due" counts as nothing pending


# -- the launcher ----------------------------------------------------------------------------


class Listening(L.Ui):  # type: ignore[name-defined,misc]
    def __init__(self) -> None:
        super().__init__(assume_yes=False)
        self.lines: list[str] = []

    def say(self, text: str = "") -> None:
        self.lines.append(text)

    def ask(self, question: str, default: bool) -> bool:
        return False  # (stop after the list of downloads)


def test_the_space_check_lists_only_what_will_be_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_lmstudio import settings_for, starlight_root

    settings = settings_for(starlight_root(tmp_path), monkeypatch)
    llama = settings.tools / "llama.cpp" / settings.variant / L.exe("llama-server")
    for path in (settings.tools / "uv" / L.exe("uv"), llama):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")  # Starlight and llama.cpp are installed; only the model is new
    monkeypatch.setattr(L, "probe", lambda url: L.RemoteFile(size=5 * L.GB, sha256="x"))
    monkeypatch.setattr(L.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * L.GB))
    ui = Listening()
    with pytest.raises(L.LauncherError, match="cancelled"):
        L.setup(settings, ui)
    listed = "\n".join(ui.lines)
    assert "the model" in listed and "Needed: about 5.0 GB" in listed
    assert "llama.cpp" not in listed and "uv, Python" not in listed  # they are there already


class Deferred:
    """threading.Thread that starts only when told: a click's thread runs later."""

    made: ClassVar[list[Deferred]] = []

    def __init__(self, target: Callable[[], Any], name: str = "", daemon: bool = True) -> None:
        self.target = target
        Deferred.made.append(self)

    def start(self) -> None:
        return None


@pytest.mark.skipif(sys.platform != "win32" and not __import__("os").environ.get("DISPLAY"),
                    reason="needs tkinter and a display")  # fmt: skip
def test_the_window_buttons_keep_the_services_they_were_clicked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_cleanup_launcher import load_gui
    from tests.test_lmstudio import starlight_root

    gui = load_gui()
    monkeypatch.setattr(gui.App, "_start_background_lookups", lambda self: None)
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / ".uv-cache"))
    app = gui.App(L, L.build_parser().parse_args(["--root", str(starlight_root(tmp_path))]))
    calls: list[str] = []
    services = SimpleNamespace(
        set_kill_switch=lambda engage: calls.append(f"kill {engage}"),
        restart_model=lambda: calls.append("restart") or True,
        resume_model=lambda: calls.append("resume"),
    )
    try:
        monkeypatch.setattr(gui.threading, "Thread", Deferred)
        Deferred.made.clear()
        app.services = services
        app.last_status = L.Status(model="ready", chat=True, viewer=True, kill=False)
        app.toggle_kill()
        app.restart_model()
        app.services = None  # "Save and restart" cleared it before the threads ran
        for thread in Deferred.made:
            thread.target()  # used to fail on None, silently, in its thread
    finally:
        app.root.destroy()
    assert calls == ["kill True", "restart"]


# -- scripts/e2e_chat.py ---------------------------------------------------------------------


def e2e_chat() -> ModuleType:
    spec = importlib.util.spec_from_file_location("e2e_chat", REPO / "scripts" / "e2e_chat.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_e2e_chat_says_when_it_could_not_check_the_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = e2e_chat()
    monkeypatch.chdir(tmp_path)
    assert script.tool_ran("hello.greet") is None  # no logs/audit.log here: unknown
    log = tmp_path / "logs" / "audit.log"
    log.parent.mkdir()
    entry = {"event": "tool.result", "tool": "hello.shout", "ok": True}
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    assert script.tool_ran("hello.greet") is False
    log.write_text(json.dumps({**entry, "tool": "hello.greet"}) + "\n", encoding="utf-8")
    assert script.tool_ran("hello.greet") is True
