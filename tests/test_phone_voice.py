"""Phone and voice: "Hey Starlight" (cutting speech out of the microphone's sound, finding the
phrase, answering out loud), choosing and installing voices, conversation mode in the page,
the photo actions, sharing from the phone, the kill switch on the home screen, the offline
page and waking a computer over the network."""

from __future__ import annotations

import array
import json
import math
import shutil
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from starlight.core import Kernel, Paths
from tests import test_chat_page
from tests.conftest import REPO, ScriptedProvider
from tests.test_chat_page import open_chat

static_site = test_chat_page.static_site  # (a fixture: a server for the page's files)


def instance(k: Kernel, name: str) -> Any:
    module = k.modules.records[name].instance
    assert module is not None, k.modules.records[name].error
    return module


def tone(seconds: float, level: int, rate: int = 16000) -> list[bytes]:
    """30 ms frames of a 440 Hz tone (level 0: silence)."""
    frame = int(rate * 0.03)
    count = round(seconds / 0.03)
    samples = array.array(
        "h", (int(level * math.sin(2 * math.pi * 440 * n / rate)) for n in range(frame))
    )
    return [samples.tobytes()] * count


# -- cutting speech out of the microphone's sound ----------------------------------------------


def test_speech_is_cut_into_stretches() -> None:
    from modules.voice.listening import Segmenter, loudness

    assert loudness(b"") == 0 and abs(loudness(tone(0.03, 1000)[0]) - 707) < 10  # (1000/√2)
    cut = Segmenter()
    out = [cut.feed(f) for f in tone(1.0, 30) + tone(1.2, 3000) + tone(1.0, 30)]
    stretches = [s for s in out if s is not None]
    assert len(stretches) == 1
    assert 1.2 <= len(stretches[0]) / 32000 <= 2.3  # the speech, a little before and the pause
    click = [cut.feed(f) for f in tone(0.06, 3000) + tone(1.0, 30)]
    assert all(s is None for s in click)  # a click is not speech
    loud_room = Segmenter()
    for frame in tone(3.0, 500):  # a fan: the floor rises with it
        loud_room.feed(frame)
    assert all(loud_room.feed(f) is None for f in tone(1.0, 900) + tone(1.0, 500))


def test_the_wake_phrase_however_it_is_written_down() -> None:
    from modules.voice.listening import wake_match

    assert wake_match("Hey, Starlight, what's the weather?") == (True, "what's the weather?")
    assert wake_match("Hey star light.") == (True, "")
    assert wake_match("Hallo Starlight, wie spät ist es") == (True, "wie spät ist es")
    assert wake_match("OK Starlight! Licht aus") == (True, "Licht aus")
    assert wake_match("The starlight was nice") == (False, "")
    assert wake_match("Starlight") == (False, "")  # the bare name: too easily said on TV
    assert wake_match("") == (False, "")


@pytest.fixture
def voice_home(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("voice")
    with paths.config_file.open("a") as config:
        config.write("memory:\n  enabled: false\nskills:\n  enabled: false\n")
    return paths


async def test_hey_starlight_answers_out_loud(voice_home: Paths) -> None:
    provider = ScriptedProvider("It is sunny in Kiel.", "It is 10 past 9.")
    async with Kernel(voice_home, provider=provider) as k:
        voice = instance(k, "voice")
        heard = iter(["Hey Starlight, how is the weather?", "Hey Starlight", "What time is it?",
                      "Nice weather today", "Hey Starlight"])  # fmt: skip
        said: list[str] = []

        async def transcribe(audio: bytes, language: str = "") -> str:
            assert audio[:4] == b"RIFF"
            return next(heard)

        async def say(text: str) -> None:
            said.append(text)

        voice.transcribe_audio, voice._say = transcribe, say

        async def more() -> bytes:
            return b"\0\0" * 1600

        async def nothing() -> None:
            return None

        assert await voice.on_speech(b"\0\0" * 1600, more) == "how is the weather?"
        assert said == ["It is sunny in Kiel."]
        assert await voice.on_speech(b"\0\0" * 1600, more) == "What time is it?"
        assert said[1:] == ["Ja?", "It is 10 past 9."]
        assert await voice.on_speech(b"\0\0" * 1600, more) is None  # not for Starlight
        assert await voice.on_speech(b"\0\0" * 1600, nothing) is None  # then nothing came
        assert said[-1] == "Ja?" and len(provider.calls) == 2


async def test_listening_is_switched_on_and_off(voice_home: Paths) -> None:
    async with Kernel(voice_home) as k:
        voice = instance(k, "voice")
        started: list[str] = []

        async def listen() -> None:
            started.append("listening")

        voice._listen = listen
        on = await k.dispatcher.call("voice.wake_word", {"on": True})
        assert on.ok and on.output["listening"]
        assert "wake_word: true" in (voice_home.module_configs / "voice.yaml").read_text()
        off = await k.dispatcher.call("voice.wake_word", {"on": False})
        assert off.ok and not off.output["listening"]


# -- choosing and installing voices ------------------------------------------------------------


def piper_voices(root: Path, *names: str) -> Path:
    folder = root / "tools" / "piper" / "voices"
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / f"{name}.onnx").write_bytes(b"model")
        (folder / f"{name}.onnx.json").write_text("{}")
    return folder


async def test_choosing_and_installing_a_voice(voice_home: Paths) -> None:
    from modules.voice.module import voice_path

    assert voice_path("de_DE-eva_k-x_low") == "de/de_DE/eva_k/x_low/de_DE-eva_k-x_low"
    piper_voices(
        voice_home.root, "de_DE-thorsten-medium", "de_DE-kerstin-low", "en_US-lessac-medium"
    )
    asked: list[str] = []

    def hub(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, content=b"x" * 1000)

    async with Kernel(voice_home) as k:
        voice = instance(k, "voice")
        voice._client = httpx.AsyncClient(transport=httpx.MockTransport(hub))
        listed = (await k.dispatcher.call("voice.voices", {})).output
        speaking = {v["name"]: v["speaks"] for v in listed["installed"]}
        assert speaking == {
            "de_DE-kerstin-low": True,
            "de_DE-thorsten-medium": False,
            "en_US-lessac-medium": True,
        }  # fmt: skip (first one by name)
        assert "de_DE-ramona-low" in {v["name"] for v in listed["can_install"]}
        chosen = await k.dispatcher.call("voice.choose_voice", {"name": "de_DE-thorsten-medium"})
        assert chosen.ok and voice.voices()["de"].stem == "de_DE-thorsten-medium"
        assert (
            "de_DE-thorsten-medium.onnx" in (voice_home.module_configs / "voice.yaml").read_text()
        )
        missing = await k.dispatcher.call("voice.choose_voice", {"name": "de_DE-ramona-low"})
        assert not missing.ok and "not installed" in str(missing.error)
        installed = await k.dispatcher.call("voice.install_voice", {"name": "de_DE-ramona-low"})
        assert installed.ok, installed.error
        where = "/rhasspy/piper-voices/resolve/main/de/de_DE/ramona/low/de_DE-ramona-low"
        assert asked == [where + ".onnx.json", where + ".onnx"]
        folder = voice_home.root / "tools" / "piper" / "voices"
        assert (folder / "de_DE-ramona-low.onnx").read_bytes() == b"x" * 1000
        assert not list(folder.glob("*.part"))
        odd = await k.dispatcher.call("voice.install_voice", {"name": "../../evil"})
        assert not odd.ok and "not one of the voices" in str(odd.error)
        with pytest.raises(Exception, match="not installed"):
            voice._voice("de", "nope")


async def test_a_failed_download_leaves_nothing_behind(voice_home: Paths) -> None:
    async with Kernel(voice_home) as k:
        voice = instance(k, "voice")
        voice._client = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(404))
        )
        failed = await k.dispatcher.call("voice.install_voice", {"name": "de_DE-ramona-low"})
        assert not failed.ok and "404" in str(failed.error)
        assert list((voice_home.root / "tools" / "piper" / "voices").iterdir()) == []


# -- waking a computer -------------------------------------------------------------------------


def test_the_wake_on_lan_packet() -> None:
    from modules.network.module import magic_packet

    packet = magic_packet("A4-2B-B0-11-22-33")
    assert packet[:6] == b"\xff" * 6 and packet[6:12] == bytes.fromhex("a42bb0112233")
    assert len(packet) == 102
    with pytest.raises(Exception, match="not a hardware address"):
        magic_packet("a4:2b")


async def test_waking_a_computer_sends_the_packet(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    use_module("network")
    (paths.config / "servers.yaml").write_text("servers: {}\n")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(5)
        port = listener.getsockname()[1]
        async with Kernel(paths) as k:
            import modules.network.module as network

            sent: list[tuple[bytes, Any]] = []
            real = socket.socket

            class Spy(real):  # type: ignore[misc,valid-type]
                def sendto(self, data: bytes, address: Any) -> int:  # type: ignore[override]
                    sent.append((data, address))
                    return super().sendto(data, ("127.0.0.1", port))

            network.socket.socket = Spy  # type: ignore[misc]
            try:
                woke = await k.dispatcher.call("network.wake", {"mac": "a4:2b:b0:11:22:33"})
            finally:
                network.socket.socket = real  # type: ignore[misc]
            assert woke.ok, woke.error
        assert [a for _, a in sent] == [("255.255.255.255", 9), ("255.255.255.255", 7)]
        assert listener.recv(200)[6:12] == bytes.fromhex("a42bb0112233")


# -- the phone: sharing, the kill switch, the offline page -------------------------------------


@pytest.fixture
def web_home(tmp_path: Path) -> Path:
    from tests.test_web_chat import web_home as make

    root = make(tmp_path / "home")
    shutil.copytree(REPO / "modules" / "voice", root / "modules" / "voice")
    piper_voices(root, "de_DE-thorsten-medium")
    (root / "config" / "starlight.remote.yaml").write_text(
        "web:\n  remote:\n    wake_url: https://ha.local:8123/api/webhook/wake-pc\n"
    )
    return root


def multipart(
    fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]
) -> tuple[bytes, str]:
    boundary = "----starlight-test"
    parts = []
    for name, value in fields.items():
        disposition = f'Content-Disposition: form-data; name="{name}"'
        parts.append(f"--{boundary}\r\n{disposition}\r\n\r\n{value}\r\n".encode())
    for name, (filename, data, kind) in files.items():
        disposition = f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'
        head = f"--{boundary}\r\n{disposition}\r\nContent-Type: {kind}\r\n\r\n".encode()
        parts.append(head + data + b"\r\n")
    return b"".join(
        parts
    ) + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def test_sharing_from_the_phone_lands_in_the_chat_box(web_home: Path) -> None:
    from tests.test_web_chat import LOCAL, client

    with client(web_home) as c:
        manifest = c.get("/manifest.webmanifest").json()
        assert manifest["share_target"]["action"] == "/share"
        assert manifest["shortcuts"][0]["url"] == "/kill"
        data, kind = multipart(
            {"title": "Pasta", "text": "Look at this", "url": "https://example.org/pasta"},
            {"file": ("menu.txt", b"Spaghetti 12 EUR", "text/plain")},
        )
        shared = c.post("/share", content=data, headers={**LOCAL, "content-type": kind},
                        follow_redirects=False)  # fmt: skip
        assert shared.status_code == 303
        query = parse_qs(urlsplit(shared.headers["location"]).query)
        assert query["share"] == ["Pasta\nLook at this\nhttps://example.org/pasta"]
        files = json.loads(query["files"][0])
        assert files[0]["name"] == "menu.txt" and len(files[0]["id"]) == 32
        plain = c.post("/share", content=b"url=https%3A%2F%2Fexample.org%2Fx",
                       headers={**LOCAL, "content-type": "application/x-www-form-urlencoded"},
                       follow_redirects=False)  # fmt: skip
        assert parse_qs(urlsplit(plain.headers["location"]).query)["share"] == [
            "https://example.org/x"
        ]
        # the phone's share menu: sent by the browser itself, with "null" as its origin
        menu = {**LOCAL, "content-type": kind, "origin": "null", "sec-fetch-site": "none"}
        assert (
            c.post("/share", content=data, headers=menu, follow_redirects=False).status_code == 303
        )
        page = {**LOCAL, "content-type": kind, "origin": "null", "sec-fetch-site": "cross-site"}
        assert (
            c.post("/share", content=data, headers=page, follow_redirects=False).status_code == 403
        )
        evil = {**LOCAL, "content-type": kind, "origin": "https://evil.example"}
        assert (
            c.post("/share", content=data, headers=evil, follow_redirects=False).status_code == 403
        )
        elsewhere = {"host": "other.example", "content-type": kind}
        stranger = c.post("/share", content=data, headers=elsewhere, follow_redirects=False)
        assert stranger.status_code == 403


def test_the_kill_switch_page_and_the_offline_shell(web_home: Path) -> None:
    from tests.test_web_chat import LOCAL, client

    with client(web_home) as c:
        page = c.get("/kill", headers=LOCAL)
        assert page.status_code == 200 and "STOP" in page.text and "kill.webmanifest" in page.text
        assert c.get("/kill.webmanifest").json()["start_url"] == "/kill"
        assert c.get("/kill-icon.svg").headers["content-type"].startswith("image/svg")
        worker = c.get("/sw.js")
        assert worker.headers["content-type"].startswith("text/javascript")
        assert (
            "/api" not in worker.text.split("FILES = ")[1].split("]")[0]
        )  # answers are never kept
        status = c.get("/api/status", headers=LOCAL).json()
        assert status["wake_url"] == "https://ha.local:8123/api/webhook/wake-pc"
        voices = c.get("/api/voices", headers=LOCAL).json()
        assert voices["installed"][0]["name"] == "de_DE-thorsten-medium"
        chosen = c.post("/api/voices/choose", json={"name": "de_DE-thorsten-medium"}, headers=LOCAL)
        assert chosen.status_code == 200
        bad = c.post("/api/voices/install", json={"name": "nope"}, headers=LOCAL)
        assert bad.status_code == 400


# -- in the page: conversation mode, photos, shared things, offline ---------------------------


async def test_conversation_mode_hears_pauses_and_interruptions(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            said = await page.evaluate(
                """() => {
                  const m = talkMachine(), out = [];
                  const run = (level, seconds) => {
                    for (let t = 0; t < seconds - 1e-9; t += 0.1) {
                      const w = m.feed(level, 0.1); if (w) out.push(w);
                    }
                  };
                  run(0.002, 1);    // the room
                  run(0.2, 1.5);    // a question
                  run(0.002, 1);    // a pause: the question is over
                  run(0.2, 0.1);    // a cough
                  run(0.002, 1);
                  m.playing = true; // the answer is read out
                  run(0.2, 0.1);    // a short noise does not stop it
                  run(0.002, 0.5);
                  run(0.2, 0.4);    // speaking does
                  return out;
                }"""
            )
            assert said == ["start", "end", "start", "barge"]
            assert await page.locator("#talk").get_attribute("aria-pressed") == "false"
            assert await page.locator("#camera").count() == 1
            assert await page.locator("#shot").get_attribute("capture") == "environment"
        finally:
            await browser.close()


async def test_shared_things_and_the_offline_banner(
    static_site: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_chat(playwright, static_site)
        try:
            files = json.dumps([{"id": "a" * 32, "name": "menu.txt"}])
            await page.evaluate(
                "q => { history.replaceState(null, '', '/chat.html?' + q); takeShared(); }",
                "share=Look+at+this&files=" + files,
            )
            assert await page.locator("#input").input_value() == "Look at this\n"
            assert "menu.txt" in await page.locator("#files").text_content()
            assert await page.evaluate("() => location.search") == ""
            await page.evaluate(
                "() => { localStorage.setItem('starlight-wake', 'https://ha.local/wake');"
                " showOffline(); }"
            )
            assert await page.locator("#offline").is_visible()
            assert await page.locator("#wake").is_visible()
        finally:
            await browser.close()
