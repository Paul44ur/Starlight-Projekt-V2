"""Small bugs the obscure-code sweep found outside the core: picture types sent to the model,
/talk without a limit, "j"/"ja" meaning no in `starlight ask`, "ß" dropped from a download's
name, and the shutdown request files of `starlight service`."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def test_pictures_are_sent_with_their_own_type() -> None:
    from starlight.core.model import Message
    from starlight.models.common import openai_messages

    def sent_as(picture: bytes) -> str:
        parts = openai_messages([Message("user", "look", images=[picture])])[0]["content"]
        return str(parts[1]["image_url"]["url"]).split(";")[0]

    assert sent_as(b"\xff\xd8\xff\xe0 jpeg") == "data:image/jpeg"
    assert sent_as(b"GIF89a....") == "data:image/gif" and sent_as(b"GIF87a") == "data:image/gif"
    assert sent_as(b"RIFF\x10\x00\x00\x00WEBPVP8 ") == "data:image/webp"
    assert sent_as(b"\x89PNG\r\n\x1a\n") == "data:image/png"
    assert sent_as(b"RIFF\x10\x00\x00\x00WAVEfmt ") == "data:image/png"  # not a picture: as before


def test_talk_records_two_minutes_at_most() -> None:
    from starlight.cli import _talk

    recorded: list[float] = []

    async def transcribe(audio: bytes) -> str:
        return "hello"

    voice = SimpleNamespace(
        config=SimpleNamespace(record_seconds=6),
        record=lambda seconds: recorded.append(seconds) or b"wav",
        transcribe_audio=transcribe,
    )
    kernel: Any = SimpleNamespace(
        modules=SimpleNamespace(records={"voice": SimpleNamespace(instance=voice)})
    )
    view: Any = SimpleNamespace(info=lambda text: None, error=lambda text: None,
                                you=lambda text: None)  # fmt: skip
    for asked in ("999", "", "10"):
        asyncio.run(_talk(kernel, asked, view))
    assert recorded == [120, 6, 10]  # "/talk 999": two minutes at most


def test_ja_is_yes_in_starlight_ask_too(monkeypatch: Any) -> None:
    from starlight import cli, console
    from starlight.core.permissions import PermissionPolicy

    answer = ""

    async def typed(reader: Any, prompt: str) -> str:
        return answer

    monkeypatch.setattr(console.lines, "read", typed)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    ui = cli.TerminalUI()
    confirm = cli.make_confirmer(PermissionPolicy(), assume_yes=False)
    tool: Any = SimpleNamespace(qualified_name="files.save")
    for answer, meant in (("j", True), ("Ja", True), ("yes", True), ("n", False), ("", False)):
        assert asyncio.run(ui.confirm("Save it?")) is meant, answer
        assert asyncio.run(confirm(tool, {})) is meant, answer  # "j" and "ja" used to mean no


def test_a_download_name_keeps_its_sharp_s(tmp_path: Path) -> None:
    from tests.test_web_chat import LOCAL, client, web_home

    root = web_home(tmp_path / "home")
    with client(root) as c:
        made = c.post("/api/conversations", json={}, headers=LOCAL).json()["id"]
        kernel = c.app.state.kernel  # type: ignore[attr-defined]
        kernel.conversations.rename(made, "Straße und Größe")
        got = c.get(f"/api/conversations/{made}/export", headers=LOCAL)
    assert 'filename="Strasse und Grosse.md"' in got.headers["content-disposition"]


def test_service_stop_writes_the_request_the_programs_watch_for(tmp_path: Path) -> None:
    from starlight import service

    plan = service.windows_plan(tmp_path, "stop", tmp_path / "data", folder=tmp_path / "Startup")
    assert set(plan.files.values()) == {service.SHUTDOWN_REQUEST}
    assert service.link_name("core") == "Starlight core.lnk"
    assert service.unit_name("viewer") == "starlight-viewer.service"


def test_top_must_end_by_itself_to_count_as_read_only() -> None:
    import sys

    sys.path.insert(0, str(Path(__file__).parents[1] / "modules"))
    try:
        from network.readonly import is_read_only  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    assert is_read_only("top -bn1") and is_read_only("top -b -n 1")
    assert not is_read_only("top -b")  # batch mode alone runs until stopped
    assert not is_read_only("top")
    assert not is_read_only("docker stats") and is_read_only("docker stats --no-stream")


def test_every_loopback_address_is_this_pc_only(monkeypatch: Any) -> None:
    from modules.dev.module import DevModule

    addresses = ["127.0.0.1", "127.0.0.53", "::1", "0.0.0.0", "192.168.1.5"]
    psutil = SimpleNamespace(
        CONN_LISTEN="LISTEN", AccessDenied=PermissionError, NoSuchProcess=LookupError,
        net_connections=lambda kind: [
            SimpleNamespace(status="LISTEN", laddr=SimpleNamespace(ip=ip, port=8000 + n), pid=0)
            for n, ip in enumerate(addresses)
        ],
    )  # fmt: skip
    monkeypatch.setitem(sys.modules, "psutil", psutil)
    found = DevModule.ports(object.__new__(DevModule))["listening"]
    reach = {row["address"]: row["reachable_from"] for row in found}
    assert reach == {
        "127.0.0.1": "this PC only", "127.0.0.53": "this PC only", "::1": "this PC only",
        "0.0.0.0": "the network", "192.168.1.5": "the network",
    }  # fmt: skip


async def test_a_folder_named_like_a_note_is_not_taken_for_one(
    paths: Any, use_module: Any, documents: Path
) -> None:
    from starlight.core import Kernel

    use_module("notes")
    async with Kernel(paths) as k:
        notes = k.modules.records["notes"].instance
        notes._file("Ideas").mkdir(parents=True)  # a folder called "Ideas.md"
        await k.dispatcher.call("notes.note", {"title": "Shopping", "text": "milk"})
        made = await k.dispatcher.call("notes.note", {"title": "Ideas", "text": "a"})
        added = await k.dispatcher.call("notes.append", {"title": "Ideas", "text": "a"})
        listed = await k.dispatcher.call("notes.list", {})
        found = await k.dispatcher.call("notes.find", {"text": "milk"})
    for refused in (made, added):  # it used to fail with "Is a directory"
        assert not refused.ok and "is a folder" in str(refused.error)
    assert [n["title"] for n in listed.output["notes"]] == ["Shopping"]
    assert found.ok and found.output["count"] == 1  # the folder used to break every search
