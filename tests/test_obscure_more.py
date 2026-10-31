"""Small bugs the obscure-code sweep found outside the core: picture types sent to the model,
/talk without a limit, "j"/"ja" meaning no in `starlight ask`, "ß" dropped from a download's
name, and the shutdown request files of `starlight service`."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def test_pictures_are_sent_with_their_own_type() -> None:
    from starlight.models.common import image_mime

    assert image_mime(b"\xff\xd8\xff\xe0 jpeg") == "image/jpeg"
    assert image_mime(b"GIF89a....") == "image/gif" and image_mime(b"GIF87a") == "image/gif"
    assert image_mime(b"RIFF\x10\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert image_mime(b"\x89PNG\r\n\x1a\n") == "image/png"
    assert image_mime(b"RIFF\x10\x00\x00\x00WAVEfmt ") == "image/png"  # not a picture: as before


def test_talk_records_two_minutes_at_most() -> None:
    from starlight.cli import MAX_TALK_SECONDS, _talk

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
    assert recorded == [MAX_TALK_SECONDS, 6, 10]


def test_yes_is_the_same_word_everywhere_in_the_terminal() -> None:
    from starlight import console

    assert {"y", "yes", "j", "ja"} == set(console.YES)
    source = (Path(__file__).parents[1] / "starlight" / "cli.py").read_text(encoding="utf-8")
    assert 'in ("y", "yes")' not in source  # `starlight ask` takes "j"/"ja" too now


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


def test_every_loopback_address_is_this_pc_only() -> None:
    from modules.dev.module import _loopback

    assert _loopback("127.0.0.1") and _loopback("127.0.0.53") and _loopback("::1")
    assert not _loopback("0.0.0.0") and not _loopback("192.168.1.5") and not _loopback("x")


async def test_a_folder_named_like_a_note_is_refused_not_looped(
    paths: Any, use_module: Any, documents: Path
) -> None:
    from starlight.core import Kernel

    use_module("notes")
    async with Kernel(paths) as k:
        notes = k.modules.records["notes"].instance
        notes._file("Ideas").mkdir(parents=True)  # a folder called "Ideas.md"
        result = await k.dispatcher.call("notes.note", {"title": "Ideas", "text": "a"})
    assert not result.ok and "is a folder" in str(result.error)
