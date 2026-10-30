"""Memory and knowledge extras: facts that expire, people and places, forgetting what one
conversation taught, links to the page of a source, recordings summarized, the journal and
its weekly review, and skills passed between Starlights."""

from __future__ import annotations

import datetime as dt
import io
import json
import shutil
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.cli import main
from starlight.core import Kernel, Paths, ToolError
from starlight.memory import Memory
from starlight.memory.store import MemoryEntry
from starlight.skills.manager import SkillManager
from tests.conftest import ScriptedProvider
from tests.test_web_chat import client, web_home

TODAY = dt.date.today()
PAST = (TODAY - dt.timedelta(days=2)).isoformat()
FUTURE = (TODAY + dt.timedelta(days=5)).isoformat()


# -- facts that only hold for a while --------------------------------------------------------


async def test_an_expired_fact_is_not_recalled_and_is_cleared_away(paths: Paths) -> None:
    memory = Memory(paths)
    await memory.remember("The office is closed for renovation.", "Office", expires=PAST)
    await memory.remember("Anna visits us.", "Anna's visit", expires=FUTURE)
    entry = memory.store.get("office")
    assert entry is not None and entry.expires == PAST and entry.expired()
    assert f"expires: '{PAST}'" in memory.store.path("office").read_text()
    recalled = [h.entry.topic for h in await memory.recall("office closed renovation visit")]
    assert "Office" not in recalled and "Anna's visit" in recalled
    assert await memory.prune_expired() == 1
    assert memory.store.get("office") is None and memory.store.get("anna-s-visit") is not None
    memory.close()


async def test_extraction_marks_people_places_and_how_long_a_fact_holds(paths: Paths) -> None:
    facts = json.dumps([
        {"topic": "Sister", "fact": "Paul's sister is Lena.", "kind": "person"},
        {"topic": "Hometown", "fact": "Paul grew up in Kiel.", "kind": "place"},
        {"topic": "Lena's visit", "fact": "Lena stays until Friday.", "until": FUTURE},
        {"topic": "Bad date", "fact": "Something.", "until": "next week", "kind": "robot"},
    ])  # fmt: skip
    provider = ScriptedProvider(facts)
    memory = Memory(paths, provider=provider)
    saved = await memory.extract(
        "I talked about Lena", "Nice!", "req1", conversation="abc123abc123"
    )
    by_topic = {e.topic: e for e in saved}
    assert by_topic["Sister"].tags == ["person"] and by_topic["Hometown"].tags == ["place"]
    assert by_topic["Lena's visit"].expires == FUTURE
    assert by_topic["Bad date"].expires == "" and by_topic["Bad date"].tags == []
    assert all(e.source == "conversation abc123abc123" for e in saved)
    assert TODAY.isoformat() in provider.calls[0][0].content  # the model knows today's date
    people = memory.people_and_places()
    assert [p["topic"] for p in people["people"]] == ["Sister"]
    assert [p["topic"] for p in people["places"]] == ["Hometown"]
    memory.close()


async def test_forgetting_a_conversation_keeps_what_was_said_elsewhere_too(paths: Paths) -> None:
    memory = Memory(paths)
    memory.store.write(MemoryEntry("a", "Only here", "x", source="conversation aaaaaaaaaaaa"))
    memory.store.write(
        MemoryEntry("b", "Both", "y", source="conversation aaaaaaaaaaaa; conversation bbbbbbbbbbbb")
    )
    memory.store.write(MemoryEntry("c", "Elsewhere", "z", source="conversation bbbbbbbbbbbb"))
    result = await memory.forget_conversation("aaaaaaaaaaaa")
    assert result == {"forgotten": ["Only here"], "kept_because_also_said_elsewhere": ["Both"]}
    assert {e.id for e in memory.store.all()} == {"b", "c"}
    memory.close()


async def test_the_memory_tools(paths: Paths) -> None:
    async with Kernel(paths) as k:
        saved = await k.dispatcher.call(
            "memory.remember", {"fact": "Tom lives in Bremen.", "topic": "Tom", "kind": "person"}
        )
        assert saved.ok
        wrong = await k.dispatcher.call("memory.remember", {"fact": "x", "until": "soon"})
        assert not wrong.ok and "YYYY-MM-DD" in str(wrong.error)
        people = (await k.dispatcher.call("memory.people_and_places")).output
        assert people["people"][0]["topic"] == "Tom"
        bad = await k.dispatcher.call("memory.forget_conversation", {"conversation": "../x"})
        assert not bad.ok  # (it asks first; asked nothing here, so it is refused anyway)


async def test_memories_learned_in_a_conversation_name_it(paths: Paths) -> None:
    async with Kernel(paths) as k:
        seen: list[dict[str, Any]] = []

        async def extract(request: str, answer: str, request_id: str, **more: Any) -> None:
            seen.append(more)

        k.memory.extract = extract  # type: ignore[method-assign]
        k.provider.stand_in = False  # type: ignore[union-attr]
        name = k.conversations.current
        await k.ask("hello", keep_history=True, conversation=name)
        for task in list(k._background):
            if task.get_name() == "memory-extract":
                await task
    assert seen == [{"conversation": name}]


def test_the_memory_page_shows_expiry_and_forgets_a_conversation(tmp_path: Path) -> None:
    root = web_home(tmp_path / "h")
    memory_dir = root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "x.md").write_text(
        f"---\ntopic: Gone\nsource: conversation cccccccccccc\nexpires: '{FUTURE}'\n---\nOld.\n"
    )
    with client(root) as c:
        listed = c.get("/api/memory").json()["memories"]  # type: ignore[attr-defined]
        assert listed[0]["expires"] == FUTURE and listed[0]["expired"] is False
        forgot = c.post("/api/conversations/cccccccccccc/forget-learned").json()  # type: ignore[attr-defined]
        assert forgot["forgotten"] == ["Gone"]
        assert c.post("/api/conversations/nope/forget-learned").status_code == 400  # type: ignore[attr-defined]


# -- sources open at their page ------------------------------------------------------------------


def test_a_source_links_to_its_page_and_only_indexed_files_are_served(tmp_path: Path) -> None:
    from modules.docs.module import DocsModule

    assert (
        DocsModule.link("C:\\Docs\\Lease.pdf", 3)
        == "/docs/file?path=C%3A%5CDocs%5CLease.pdf#page=3"
    )
    assert DocsModule.link("/home/me/notes.txt", 0) == "/docs/file?path=/home/me/notes.txt"
    root = web_home(tmp_path / "h")
    shutil.copytree(Path(__file__).parent.parent / "modules" / "docs", root / "modules" / "docs")
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(b"%PDF-1.4 secret")
    with client(root) as c:
        refused = c.get("/docs/file", params={"path": str(secret)})  # type: ignore[attr-defined]
        assert refused.status_code == 404  # not in the knowledge base
        docs = c.app.state.kernel.modules.records["docs"].instance  # type: ignore[attr-defined]
        docs._sql(
            "INSERT INTO parts (path, page, text, vector) VALUES (?, 1, 'x', ?)", (str(secret), b"")
        )
        served = c.get("/docs/file", params={"path": str(secret)})  # type: ignore[attr-defined]
        assert served.status_code == 200 and served.content.startswith(b"%PDF")
        assert served.headers["content-type"] == "application/pdf"
        assert served.headers["content-disposition"].startswith("inline")


# -- recordings -------------------------------------------------------------------------------


def wav(seconds: float, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


@pytest.fixture
def voice_project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("voice")
    return paths


async def test_a_long_recording_is_transcribed_in_pieces_and_summarized(
    voice_project: Paths, documents: Path
) -> None:
    recording = documents / "Starlight" / "meeting.wav"
    recording.parent.mkdir(parents=True, exist_ok=True)
    recording.write_bytes(wav(12 * 60))  # twelve minutes: three pieces
    provider = ScriptedProvider("Points: budget agreed. To-dos: Anna sends the offer.")
    async with Kernel(voice_project, provider=provider) as k:
        voice = k.modules.records["voice"].instance
        assert voice is not None
        heard: list[int] = []

        async def transcribe_audio(audio: bytes, language: str = "") -> str:
            heard.append(len(audio))
            return f"part {len(heard)}"

        voice.transcribe_audio = transcribe_audio  # type: ignore[method-assign]
        result = await k.dispatcher.call("voice.summarize_recording", {"path": str(recording)})
    assert result.ok, result.error
    assert len(heard) == 3
    assert "budget agreed" in result.output["summary"]
    saved = Path(result.output["saved"]).read_text()
    assert "## Transcript" in saved and "part 1 part 2 part 3" in saved


async def test_other_formats_need_ffmpeg(
    voice_project: Paths, documents: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clip = documents / "Starlight" / "memo.m4a"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"not really audio")
    monkeypatch.setattr("modules.voice.module.shutil.which", lambda name: None, raising=False)
    async with Kernel(voice_project) as k:
        voice = k.modules.records["voice"].instance
        assert voice is not None
        with pytest.raises(ToolError, match="need ffmpeg"):
            voice._pieces(clip)


# -- the journal ------------------------------------------------------------------------------


@pytest.fixture
def journal_project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("journal")
    return paths


def test_days_in_words() -> None:
    from modules.journal.module import parse_day

    friday = dt.date(2026, 10, 30)  # a Friday
    assert parse_day("", friday) == friday and parse_day("gestern", friday) == dt.date(2026, 10, 29)
    assert parse_day("monday", friday) == dt.date(2026, 10, 26)
    assert parse_day("Freitag", friday) == friday
    assert parse_day("24.12.", friday) == dt.date(2026, 12, 24)
    assert parse_day("2026-01-02", friday) == dt.date(2026, 1, 2)
    with pytest.raises(ToolError):
        parse_day("someday", friday)


async def test_the_journal_and_its_weekly_review(journal_project: Paths, documents: Path) -> None:
    provider = ScriptedProvider("The week: calm. Good things: the hike.")
    async with Kernel(journal_project, provider=provider) as k:
        await k.dispatcher.call("journal.write", {"text": "A long hike today, sunny."})
        await k.dispatcher.call("journal.write", {"text": "Tired but happy."})
        await k.dispatcher.call("journal.write", {"text": "Dentist.", "day": "yesterday"})
        today = (await k.dispatcher.call("journal.read")).output["journal"]
        assert today.count("**") == 4 and "A long hike" in today and "Tired" in today
        found = (await k.dispatcher.call("journal.search", {"text": "DENTIST"})).output
        assert found["count"] == 1
        review = await k.dispatcher.call("journal.week")
    assert review.ok, review.error
    assert "the hike" in review.output["review"]
    sent = provider.calls[0][1].content
    assert "A long hike" in sent and "Dentist" in sent
    year, week, _ = TODAY.isocalendar()
    assert Path(review.output["saved"]).name == f"{year}-W{week:02d}.md"
    assert (documents / "Starlight" / "Journal" / f"{TODAY.isoformat()}.md").is_file()


async def test_an_empty_week_has_nothing_to_review(journal_project: Paths) -> None:
    journal_project.config_file.write_text(
        "kill_switch:\n  hotkey: null\n", encoding="utf-8"
    )  # (no to-do or calendar module either)
    async with Kernel(journal_project) as k:
        empty = await k.dispatcher.call("journal.week")
    assert not empty.ok and "nothing for this week" in str(empty.error)


# -- skills between Starlights ------------------------------------------------------------------


def test_a_skill_goes_out_as_a_file_and_comes_back_as_a_draft(paths: Paths, tmp_path: Path) -> None:
    from starlight.core.config import SkillsConfig

    mine = SkillManager(paths, SkillsConfig())
    mine.draft("Weekly backup", "Back up the photos", "1. Run backup.run photos", ["backup"],
               code={"check.py": "print('ok')"})  # fmt: skip
    mine.approve("weekly-backup")
    data = mine.export_data("weekly-backup")
    assert data["format"] == "starlight-skill/1" and data["skill"]["code"] == {
        "check.py": "print('ok')"
    }
    other_paths = Paths(tmp_path / "other")
    other_paths.ensure()
    theirs = SkillManager(other_paths, SkillsConfig())
    skill = theirs.import_data(json.loads(json.dumps(data)), source="imported from a friend")
    assert skill.status == "draft" and skill.source == "imported from a friend"
    assert theirs.get("weekly-backup").code == {"check.py": "print('ok')"}  # type: ignore[union-attr]
    for bad in ({}, {"format": "x"}, {"format": "starlight-skill/1", "skill": {"name": "a"}}):
        with pytest.raises(ValueError):
            theirs.import_data(bad)


def test_skills_export_and_import_from_the_command_line(
    paths: Paths, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from starlight.core.config import SkillsConfig

    SkillManager(paths, SkillsConfig()).draft("Tea", "Make tea", "Boil water.")
    home = ["--home", str(paths.root)]
    assert main([*home, "skills", "export", "tea", str(tmp_path)]) == 0
    exported = tmp_path / "tea.skill.json"
    assert exported.is_file()
    other = Paths(tmp_path / "other")
    other.ensure()
    assert main(["--home", str(other.root), "skills", "import", str(exported)]) == 0
    assert "added as a draft" in capsys.readouterr().out
    (tmp_path / "junk.json").write_text("{}")
    assert main(["--home", str(other.root), "skills", "import", str(tmp_path / "junk.json")]) == 1
