"""Bugs found in a debug pass of the core, the model layer, the launcher and the terminal
chat - each test is the failing case as it was reported, now working."""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths
from starlight.core.config import HttpConfig, PermissionsConfig
from starlight.core.events import Event
from starlight.core.http import PoliteHttp, body_encoding
from starlight.core.killswitch import KillSwitch
from starlight.core.model import ModelResponse, ToolCall
from starlight.core.permissions import Permission, PermissionPolicy
from starlight.core.schedule import ScheduleError, has_clock_time, parse_clock, parse_schedule
from starlight.models.output import StreamFilter, cut_at_turn_end
from tests.conftest import ModuleWriter
from tests.test_launcher import L, QuietUi

# -- kill switch ---------------------------------------------------------------------------


async def test_a_nested_agent_run_keeps_the_outer_one_stoppable(tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path / "STOP")
    me = asyncio.current_task()
    with switch.guard():
        with switch.guard():  # automations.run_now -> ctx.run_request: the same task again
            pass
        assert me in switch._tasks  # still stopped by the kill switch
    assert me not in switch._tasks


async def test_the_kill_switch_stops_a_run_after_a_nested_request(
    paths: Paths, write_module: ModuleWriter
) -> None:
    write_module(
        "nest",
        """
        from starlight.core import Module, tool
        class Mod(Module):
            @tool
            async def inner(self) -> str:
                "run a nested request"
                return await self.ctx.run_request("hello")
        """,
    )

    class Slow:
        name = "slow"

        async def chat(self, messages: list[Any], tools: list[Any]) -> ModelResponse:
            last = messages[-1]
            if last.role == "user" and last.content == "outer":
                return ModelResponse(tool_calls=[ToolCall("nest.inner", {})])
            if last.role == "user":
                return ModelResponse(content="inner answer")
            await asyncio.sleep(3)  # the outer run's next step: a long answer
            return ModelResponse(content="outer finished")

    paths.config_file.write_text(
        "kill_switch:\n  hotkey: null\n  poll_interval: 0.02\nmemory:\n  enabled: false\n"
        "skills:\n  enabled: false\n"
    )
    k = Kernel(paths, provider=Slow())
    await k.start()
    try:
        task = asyncio.create_task(k.ask("outer"))
        await asyncio.sleep(0.5)
        started = time.monotonic()
        k.kill_switch.trigger("test", source="test")
        result = await task
        assert result.stopped and time.monotonic() - started < 1.5
    finally:
        k.kill_switch.reset()
        await k.stop()


# -- conversations ---------------------------------------------------------------------------


async def test_two_starlight_programs_on_one_folder_keep_each_others_turns(paths: Paths) -> None:
    terminal, web = Kernel(paths), Kernel(paths)
    await terminal.start()
    await web.start()
    try:
        await terminal.ask("from terminal 1", keep_history=True)
        await web.ask("from web 1", keep_history=True)
        await terminal.ask("from terminal 2", keep_history=True)
        saved = [m.content for m in terminal.conversations.messages(terminal.conversations.current)]
        assert [s for s in saved if s.startswith("from")] == [
            "from terminal 1",
            "from web 1",
            "from terminal 2",
        ]
    finally:
        await web.stop()
        await terminal.stop()


async def test_a_long_paste_is_cut_for_the_model_but_saved_whole(paths: Paths) -> None:
    paths.config_file.write_text(
        "kill_switch:\n  hotkey: null\nmodel:\n  provider: echo\n  context_window: 4096\n"
        "  reserve_tokens: 1024\n"
    )
    async with Kernel(paths) as k:
        text = "A" * 20000 + "THE END"
        await k.ask(text, keep_history=True)
        saved = k.conversations.messages(k.conversations.current)
        assert saved[0].content == text
        assert "THE END" in k.conversations.markdown(k.conversations.current)


async def test_a_failed_turn_is_not_announced_as_completed(paths: Paths) -> None:
    class Flaky:
        name = "flaky"

        async def chat(self, messages: list[Any], tools: list[Any]) -> ModelResponse:
            if messages[-1].role == "tool":
                request = httpx.Request("POST", "http://x")
                raise httpx.HTTPStatusError(
                    "400", request=request, response=httpx.Response(400, request=request)
                )
            return ModelResponse(tool_calls=[ToolCall("tools.find", {"need": "x"})])

    k = Kernel(paths, provider=Flaky())
    await k.start()
    completed: list[Event] = []
    failed: list[Event] = []
    k.bus.subscribe("task.completed", completed.append)
    k.bus.subscribe("task.failed", failed.append)
    try:
        result = await k.ask("please do it")
        await asyncio.sleep(0.05)
        assert result.failed and not completed and len(failed) == 1  # memory/skills don't learn
    finally:
        await k.stop()


# -- modules, permissions, http ---------------------------------------------------------------


async def test_a_fixed_dependency_cycle_loads_after_a_reload(
    paths: Paths, write_module: ModuleWriter
) -> None:
    source = "from starlight.core import Module\nclass Mod(Module):\n    pass\n"
    write_module("aaa", source, dependencies="[bbb]")
    write_module("bbb", source, dependencies="[aaa]")
    async with Kernel(paths) as k:
        assert "cycle" in (k.modules.records["aaa"].error or "")
        write_module("bbb", source)  # the cycle is gone
        await k.modules.reload("bbb")
        assert (await k.modules.reload("aaa")).status == "loaded"


def test_allowing_one_tool_works_while_a_broader_rule_asks() -> None:
    policy = PermissionPolicy(PermissionsConfig())
    policy.set_rule("desktop.*", Permission.CONFIRM)
    policy.set_rule("desktop.click", Permission.SAFE)
    assert policy.resolve("desktop.click", Permission.CONFIRM) is Permission.SAFE
    assert policy.resolve("desktop.type", Permission.SAFE) is Permission.CONFIRM
    policy.set_rule("desktop.*", Permission.BLOCKED)  # never run still wins
    assert policy.resolve("desktop.click", Permission.CONFIRM) is Permission.BLOCKED


async def test_a_redirected_page_is_served_from_the_cache() -> None:
    hits = {"page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "http://ex.com/a/"})
        hits["page"] += 1
        return httpx.Response(200, text="page")

    http = PoliteHttp(HttpConfig(per_domain_interval=0), transport=httpx.MockTransport(handler))
    try:
        first = await http.get("http://ex.com/a")
        again = await http.get("http://ex.com/a")
        assert hits["page"] == 1 and again.from_cache
        assert again.url == first.url == "http://ex.com/a/"  # where the redirect ended
    finally:
        await http.aclose()


def test_a_body_is_decoded_as_it_declares_itself() -> None:
    feed = '<?xml version="1.0" encoding="ISO-8859-1"?><rss>München</rss>'.encode("latin-1")
    page = '<html><head><meta charset="windows-1252"></head>Größe'.encode("cp1252")
    assert feed.decode(body_encoding(None, feed)).endswith("München</rss>")
    assert page.decode(body_encoding(None, page)).endswith("Größe")
    assert body_encoding("utf-8", feed) == "utf-8"  # what the server says wins
    assert body_encoding(None, b"plain") == "utf-8"
    assert body_encoding("no-such-charset", b"") == "utf-8"


def test_impossible_clock_times_are_refused() -> None:
    assert parse_clock("9pm") == (21, 0) and parse_clock("12am") == (0, 0)
    assert parse_clock("9:75pm") is None and parse_clock("13pm") is None
    with pytest.raises(ScheduleError):
        parse_schedule("every day at 9:75pm")


def test_a_date_alone_is_not_a_time() -> None:
    assert has_clock_time("tomorrow 18.30") and has_clock_time("18:30 uhr")
    assert has_clock_time("am 24.12.2026 um 9.15") and has_clock_time("6pm")
    assert not has_clock_time("24.12.2026") and not has_clock_time("friday")
    assert not has_clock_time("in 1.5 hours")


# -- the reply guard -----------------------------------------------------------------------------

SHARED = (
    "def load_users(path):\n"
    '    """Read the user list from a CSV file and return it as dictionaries."""\n'
    "    with open(path, newline='', encoding='utf-8') as handle:\n"
    "        reader = csv.DictReader(handle, delimiter=';')\n"
    "        rows = [row for row in reader if row.get('name')]\n"
    "    for row in rows:\n"
    "        row['age'] = int(row['age'])\n"
)
FIX_ANSWER = (
    "Your function:\n\n```python\n" + SHARED + "    return row\n```\n\n"
    "The bug is the last line: it returns only the last row, not the list. "
    "Return `rows` instead:\n\n```python\n" + SHARED + "    return rows\n```\n\n"
    "Here is the whole file with the fix:\n\n```python\nimport csv\n\n\n" + SHARED
    + "    return rows\n\n\nif __name__ == '__main__':\n    print(load_users('users.csv'))\n```\n"
    "Now every row is returned."
)  # fmt: skip


def test_the_same_code_shown_three_times_is_not_a_loop() -> None:
    assert cut_at_turn_end(FIX_ANSWER) == FIX_ANSWER
    stream = StreamFilter()
    shown = "".join(stream.feed(FIX_ANSWER[i : i + 4]) for i in range(0, len(FIX_ANSWER), 4))
    assert shown + stream.held() == FIX_ANSWER and not stream.ended


def test_a_real_loop_is_still_cut_to_one_turn() -> None:
    turn = "I will now check the weather in Vienna for you and report back shortly. "
    text = "Sure. " + turn * 12
    out = cut_at_turn_end(text)
    assert out.startswith("Sure. I will now check") and out.count("check the weather") == 1


# -- launcher ------------------------------------------------------------------------------------

DATA = os.urandom(2 * 1024 * 1024 + 7)


class _Drops(BaseHTTPRequestHandler):
    """Closes the connection cleanly halfway through the first answer; serves ranges."""

    protocol_version = "HTTP/1.1"
    requests: list[str | None] = []  # noqa: RUF012

    def log_message(self, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        type(self).requests.append(self.headers.get("Range"))
        start = 0
        if self.headers.get("Range"):
            start = int(self.headers["Range"].removeprefix("bytes=").split("-")[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(DATA) - 1}/{len(DATA)}")
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(DATA) - start))
        self.send_header("Connection", "close")
        self.end_headers()
        body = DATA[start:]
        if len(type(self).requests) == 1:
            self.wfile.write(body[: len(body) // 2])
            self.close_connection = True
            return
        self.wfile.write(body)


@pytest.fixture
def dropping_server(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(L.time, "sleep", lambda seconds: None)
    _Drops.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Drops)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/file.bin"
    server.shutdown()
    server.server_close()


def test_a_download_that_stops_early_is_resumed(dropping_server: str, tmp_path: Path) -> None:
    dest = tmp_path / "model.gguf"
    L.download(dropping_server, dest, QuietUi(), sha256=hashlib.sha256(DATA).hexdigest())
    assert dest.read_bytes() == DATA
    assert _Drops.requests[0] is None and _Drops.requests[1] == f"bytes={len(DATA) // 2}-"


def test_a_broken_archive_is_a_clear_error_and_downloaded_again(tmp_path: Path) -> None:
    archive = tmp_path / "whisper.zip"
    archive.write_bytes(b"<html>not a zip</html>")
    with pytest.raises(L.LauncherError, match="not a complete archive"):
        L.extract_member(archive, "whisper-server", tmp_path / "whisper")
    assert not archive.exists()  # the next start downloads it again


def test_updating_a_program_keeps_its_downloaded_models(tmp_path: Path) -> None:
    target = tmp_path / "whisper"
    (target / "models").mkdir(parents=True)
    (target / "models" / "ggml-small.bin").write_bytes(b"model")
    (target / "old.dll").write_bytes(b"old")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("Release/whisper-server", "new")
        zf.writestr("Release/new.dll", "new")
    archive = tmp_path / "whisper.zip"
    archive.write_bytes(buffer.getvalue())
    L.extract_member(archive, "whisper-server", target, keep=("models",))
    assert (target / "models" / "ggml-small.bin").read_bytes() == b"model"
    assert (target / "new.dll").exists() and not (target / "old.dll").exists()


# -- terminal chat -------------------------------------------------------------------------------


def test_the_terminal_shows_a_command_exactly_and_never_crashes_on_brackets() -> None:
    from rich.console import Console

    from starlight.tui import ChatView

    view = ChatView(assume_yes=False)
    view.rich = True
    out = io.StringIO()
    view._console = Console(file=out, width=200, force_terminal=False)
    view._print_question(
        'Starlight wants to run shell.run({"command": "del C:\\\\B\\\\[old]\\\\*.zip"})'
    )
    view.info("re.split(r'[/\\\\]', path)")  # "[/" used to raise MarkupError
    view.error("[bold]not bold[/bold]")
    text = out.getvalue()
    assert "[old]" in text and "[/\\\\]" in text and "[bold]not bold[/bold]" in text


async def test_ctrl_c_in_the_terminal_client_stops_the_answer_on_the_backend() -> None:
    from starlight.web.client import stop_turn

    sent: list[dict[str, Any]] = []
    replies = [{"type": "token", "text": "late"}, {"type": "stopped"}]

    async def send(obj: dict[str, Any]) -> None:
        sent.append(obj)

    async def recv() -> dict[str, Any]:
        return replies.pop(0)

    await stop_turn(send, recv)
    assert sent == [{"type": "stop"}] and not replies  # read up to "stopped"
