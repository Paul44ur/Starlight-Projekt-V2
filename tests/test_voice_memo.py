"""Block D2 of the notes plan (docs/plan-notes-app.md): voice memos. Spoken, written down by
the speech recognition, kept as text in the inbox or the journal; the sound is not kept."""

from __future__ import annotations

import datetime as dt
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from tests.conftest import REPO
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_home_devices import call, fails
from tests.test_notes_page import notebook as notebook  # (the fixture)
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL, client, home


@pytest.fixture
def memo_project(paths: Paths, use_module: Callable[[str], Path], documents: Path) -> Path:
    for name in ("voice", "notes", "journal"):
        use_module(name)
    return documents / "Starlight"


async def test_a_memo_is_recorded_written_down_and_kept_as_text(
    paths: Paths, memo_project: Path
) -> None:
    async with Kernel(paths) as k:
        voice: Any = k.modules.records["voice"].instance
        recorded: list[float] = []
        heard = ["Buy oat milk tomorrow", "Today was calm", ""]

        def record(seconds: float) -> bytes:
            recorded.append(seconds)
            return b"RIFF....WAVE"

        async def transcribe_audio(audio: bytes, language: str = "") -> str:
            return heard.pop(0)

        voice.record, voice.transcribe_audio = record, transcribe_audio
        kept = await call(k, "voice.memo", seconds=8)
        assert kept == {"text": "Buy oat milk tomorrow", "kept_in": "inbox"}
        assert recorded == [8]
        to_journal = await call(k, "voice.memo", seconds=500, to="journal")
        assert to_journal["kept_in"] == "journal" and recorded[-1] == 120  # two minutes at most
        assert "nothing was understood" in await fails(k, "voice.memo")
    inbox = (memo_project / "Inbox" / "Inbox.md").read_text("utf-8")
    assert "🎤 Buy oat milk tomorrow" in inbox
    day = memo_project / "Journal" / f"{dt.date.today().isoformat()}.md"
    assert "🎤 Today was calm" in day.read_text("utf-8")
    assert not list(memo_project.rglob("*.wav"))  # only the text is kept


@pytest.fixture
def memo_app(tmp_path: Path, notebook: Path) -> Iterator[Any]:
    root = home(tmp_path / "h")
    for name in ("notes", "journal"):
        shutil.copytree(REPO / "modules" / name, root / "modules" / name)
    with client(root) as c:
        yield c


def test_the_memo_endpoint_keeps_the_text(memo_app: Any, notebook: Path) -> None:
    kept = memo_app.post("/api/notes/memo", json={"text": "Call Anna"}, headers=LOCAL)
    assert kept.status_code == 200 and kept.json()["kept_in"] == "inbox"
    assert "🎤 Call Anna" in (notebook / "Inbox" / "Inbox.md").read_text("utf-8")
    day = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    body = {"text": "A long walk", "to": "journal", "day": day}
    assert memo_app.post("/api/notes/memo", json=body, headers=LOCAL).json()["kept_in"] == "journal"
    assert "🎤 A long walk" in (notebook / "Journal" / f"{day}.md").read_text("utf-8")
    empty = memo_app.post("/api/notes/memo", json={"text": "  "}, headers=LOCAL)
    assert empty.status_code == 400


FAKE_MICROPHONE = """
navigator.mediaDevices.getUserMedia = async () => {
  const context = new AudioContext();
  const tone = context.createOscillator();
  const out = context.createMediaStreamDestination();
  tone.connect(out); tone.start();
  return out.stream;
};
"""


async def test_the_memo_button_records_and_keeps_the_text(
    static_site: str, memo_app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, memo_app) as page:
        uploads: list[bytes] = []

        async def speech(route: Any, request: Any) -> None:
            uploads.append(request.post_data_buffer or b"")
            heard = '{"text": "Remember the dentist"}'
            await route.fulfill(body=heard, content_type="application/json")

        await page.add_init_script(FAKE_MICROPHONE)
        await page.route("**/api/transcribe", speech)
        await opened(page, f"{static_site}/notes.html?view=inbox")
        await page.locator("#memo").click()
        await page.wait_for_selector("#memo.on")
        assert await page.locator("#memo").get_attribute("data-icon") == "stop"
        await page.wait_for_timeout(600)  # a little sound
        await page.locator("#memo").click()
        await page.wait_for_selector("text=Memo kept in the inbox")
        assert uploads and uploads[0][:4] == b"RIFF" and uploads[0][8:12] == b"WAVE"
        assert await page.locator("#memo").get_attribute("data-icon") == "mic"
        assert "🎤 Remember the dentist" in (notebook / "Inbox" / "Inbox.md").read_text("utf-8")
        await page.wait_for_selector(".card-note")  # the inbox view shows it now
        assert page.errors == []  # type: ignore[attr-defined]
