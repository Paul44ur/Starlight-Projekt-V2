"""Block A1 of the notes plan (docs/plan-notes-app.md): the earlier versions of files Starlight
changes (starlight/notebook/history.py) and the one safe way to write them (write.py):
versions, compare, restore, the trash, cleaning up, conflicts and crashes."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from starlight.core.config import NotebookConfig, StarlightConfig
from starlight.notebook import History, NoteConflict, append_note, stamp, write_note
from starlight.notebook import db as notebook_db
from starlight.notebook import history as history_module
from starlight.notebook import write as write_module
from starlight.notebook.history import HistoryError, key_of


@pytest.fixture
def history(tmp_path: Path) -> History:
    return History(tmp_path / "data" / "history" / "history.sqlite", NotebookConfig())


@pytest.fixture
def note(tmp_path: Path) -> Path:
    folder = tmp_path / "Starlight" / "Notes"
    folder.mkdir(parents=True)
    return folder / "Shopping.md"


def test_each_change_keeps_the_note_as_it_was(history: History, note: Path) -> None:
    first = write_note(note, "# Shopping\n\nmilk\n", "notes.note", history)
    assert first.version is None  # a new note: nothing earlier to keep
    second = write_note(note, "# Shopping\n\nmilk\neggs\n", "notes.note", history)
    third = append_note(note, "bread\n", "notes.append", history)
    kept = history.versions(note)
    assert [v.id for v in kept] == [third.version, second.version]  # newest first
    assert [v.reason for v in kept] == ["notes.append", "notes.note"]
    assert history.text(kept[-1].id).replace("\r\n", "\n") == "# Shopping\n\nmilk\n"
    assert note.read_text("utf-8").replace("\r\n", "\n").endswith("eggs\nbread\n")


def test_a_version_can_be_compared_and_put_back_and_that_undone(
    history: History, note: Path
) -> None:
    write_note(note, "milk\n", "notes.note", history)
    older = write_note(note, "milk\neggs\n", "notes.note", history).version
    assert older is not None
    diff = history.diff(note, older)
    assert "+eggs" in diff and "version" in diff and "now" in diff
    before_restore = history.restore(note, older)
    assert note.read_text("utf-8").replace("\r\n", "\n") == "milk\n"
    assert before_restore is not None and history.versions(note)[0].reason == "before restore"
    history.restore(note, before_restore)  # the restore undone
    assert note.read_text("utf-8").replace("\r\n", "\n") == "milk\neggs\n"


def test_a_restore_puts_back_the_exact_bytes(history: History, note: Path) -> None:
    original = b"\xef\xbb\xbfcaf\xe9 \r\nline two\r\n"  # a BOM, not UTF-8 inside, CRLF
    note.write_bytes(original)
    version = write_note(note, "replaced\n", "notes.note", history).version
    assert version is not None
    history.restore(note, version)
    assert note.read_bytes() == original


def test_a_version_of_another_file_is_not_put_back(
    history: History, note: Path, tmp_path: Path
) -> None:
    other = tmp_path / "Starlight" / "Notes" / "Ideas.md"
    other.write_text("ideas", encoding="utf-8")
    version = write_note(other, "more ideas", "notes.note", history).version
    assert version is not None
    note.write_text("shopping", encoding="utf-8")
    with pytest.raises(HistoryError, match="another file"):
        history.restore(note, version)
    with pytest.raises(HistoryError, match="no version"):
        history.read(99999)


def test_a_deleted_note_goes_to_the_trash_and_comes_back(
    history: History, note: Path, tmp_path: Path
) -> None:
    write_note(note, "milk\n", "notes.note", history)
    history.trash(note)
    assert not note.exists()
    trash = history.deleted(tmp_path / "Starlight")
    assert [Path(v.path).name for v in trash] == ["Shopping.md"] and trash[0].deleted
    assert history.deleted(tmp_path / "elsewhere") == []
    history.restore_deleted(note)
    assert note.read_text("utf-8").replace("\r\n", "\n") == "milk\n"
    assert history.deleted() == []  # back, so no longer in the trash
    with pytest.raises(HistoryError, match="exists again"):
        history.restore_deleted(note)
    with pytest.raises(HistoryError, match="not in the trash"):
        history.restore_deleted(note.with_name("Never.md"))


def test_the_same_content_is_not_kept_twice(history: History, note: Path) -> None:
    for _ in range(4):
        write_note(note, "unchanged\n", "notes.note", history)
    assert len(history.versions(note)) == 1


def test_while_typing_one_version_per_few_minutes(history: History, note: Path) -> None:
    write_note(note, "a\n", "notes.page", history)
    for text in ("ab\n", "abc\n", "abcd\n"):  # autosaves seconds apart
        write_note(note, text, "notes.page", history, min_gap=300)
    kept = history.versions(note)
    assert len(kept) == 1 and history.text(kept[0].id).replace("\r\n", "\n") == "a\n"


def test_versions_are_cleaned_up_by_count_age_and_size(
    note: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = SimpleNamespace(keep_versions=True, versions=3, days=90, max_mb=200)
    history = History(tmp_path / "h.sqlite", settings)
    for n in range(6):
        write_note(note, f"version {n}\n", "notes.note", history)
    assert len(history.versions(note)) == 3  # by count

    clock = [1_000_000.0]
    monkeypatch.setattr(history_module.time, "time", lambda: clock[0])
    other = note.with_name("Old.md")
    other.write_text("old", encoding="utf-8")
    write_note(other, "newer", "notes.note", history)
    clock[0] += 91 * 86400  # three months later, another change somewhere
    write_note(note, "fresh\n", "notes.note", history)
    assert history.versions(other) == []  # by age

    settings.max_mb = 10_000 / 1024**2  # 10 KB for everything
    big = note.with_name("Big.md")
    for _ in range(6):
        big.write_bytes(os.urandom(4000))  # does not compress
        clock[0] += 1
        history.save(big, "test")
    with notebook_db.opened(history.db_path, history_module.MIGRATIONS) as db:
        total = db.execute("SELECT SUM(stored) FROM versions").fetchone()[0]
    assert total <= 10_000 and history.versions(big)  # by size: the oldest went first


def test_the_version_just_kept_stays_even_if_it_alone_is_too_big(
    note: Path, tmp_path: Path
) -> None:
    settings = SimpleNamespace(keep_versions=True, versions=20, days=90, max_mb=1000 / 1024**2)
    history = History(tmp_path / "h.sqlite", settings)
    note.write_bytes(os.urandom(4000))  # bigger than all versions may be together
    kept = history.save(note, "test")
    assert kept is not None and history.read(kept)  # still there for an undo


def test_two_changes_in_the_same_second_both_stay(
    history: History, note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(history_module.time, "time", lambda: 1_700_000_000.0)
    write_note(note, "one\n", "notes.note", history)
    write_note(note, "two\n", "notes.note", history)
    write_note(note, "three\n", "notes.note", history)
    texts = [history.text(v.id).replace("\r\n", "\n") for v in history.versions(note)]
    assert texts == ["two\n", "one\n"]


def test_a_note_changed_meanwhile_is_not_overwritten(history: History, note: Path) -> None:
    written = write_note(note, "mine\n", "notes.note", history)
    assert written.stamp == stamp(note)
    note.write_text("changed in another program, a bit longer\n", encoding="utf-8")
    with pytest.raises(NoteConflict, match="changed meanwhile"):
        write_note(note, "overwrite\n", "notes.page", history, expected=written.stamp)
    with pytest.raises(NoteConflict):
        append_note(note, "more\n", "notes.append", history, expected=written.stamp)
    assert note.read_text("utf-8") == "changed in another program, a bit longer\n"
    assert history.versions(note) == []  # nothing was kept for a write that did not happen
    fresh = write_note(note, "now\n", "notes.page", history, expected=stamp(note))
    assert fresh.version is not None
    assert stamp(note.with_name("Missing.md")) == write_module.MISSING


def test_a_crash_while_writing_leaves_the_old_note(
    history: History, note: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_note(note, "safe\n", "notes.note", history)

    def power_cut(path: Path, data: bytes) -> None:
        raise OSError("power cut")

    monkeypatch.setattr(write_module, "write_atomic_bytes", power_cut)
    with pytest.raises(OSError, match="power cut"):
        write_note(note, "lost?\n", "notes.note", history)
    assert note.read_text("utf-8").replace("\r\n", "\n") == "safe\n"
    assert history.versions(note)  # and the version kept before it is there


def test_appending_keeps_the_files_own_bytes_and_line_endings(history: History, note: Path) -> None:
    note.write_bytes(b"\xef\xbb\xbf# Liste\r\nM\xfcsli\r\n")  # BOM, cp1252 byte, CRLF
    append_note(note, "eggs\nbread\n", "notes.append", history)
    assert note.read_bytes() == b"\xef\xbb\xbf# Liste\r\nM\xfcsli\r\neggs\r\nbread\r\n"
    fresh = note.with_name("New.md")
    append_note(fresh, "first line\n", "notes.append", history)
    assert fresh.read_bytes() == b"first line" + os.linesep.encode()


def test_versions_can_be_switched_off(tmp_path: Path, note: Path) -> None:
    history = History(tmp_path / "h.sqlite", NotebookConfig(keep_versions=False))
    write_note(note, "a\n", "notes.note", history)
    written = write_note(note, "b\n", "notes.note", history)
    assert written.version is None and history.versions(note) == []
    history.trash(note)  # deleting still works, without a copy
    assert not note.exists()


def test_the_same_file_however_its_path_is_written(
    history: History, note: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    note.write_text("x", encoding="utf-8")
    roundabout = note.parent / ".." / "Notes" / note.name
    monkeypatch.chdir(note.parent)
    assert key_of(roundabout) == key_of(note) == key_of(Path(note.name))
    weird = note.with_name("Straße & Größe - Ideen.md")
    write_note(weird, "eins\n", "notes.note", history)
    write_note(weird, "zwei\n", "notes.note", history)
    assert len(history.versions(weird)) == 1


def test_the_database_grows_by_steps_and_refuses_a_newer_one(tmp_path: Path) -> None:
    path = tmp_path / "x.sqlite"
    with notebook_db.opened(path, ["CREATE TABLE a (n INTEGER)"]) as db:
        db.execute("INSERT INTO a VALUES (1)")
    steps = ["CREATE TABLE a (n INTEGER)", "ALTER TABLE a ADD COLUMN m TEXT"]
    with notebook_db.opened(path, steps) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT n, m FROM a").fetchall() == [(1, None)]  # data kept
        assert db.execute("PRAGMA auto_vacuum").fetchone()[0] == 2  # incremental
    with pytest.raises(sqlite3.DatabaseError, match="newer Starlight"):
        notebook_db.connect(path, steps[:1])


def test_the_settings_are_part_of_starlights_config() -> None:
    config: Any = StarlightConfig.model_validate({"notebook": {"versions": 5, "days": 30}})
    assert config.notebook.versions == 5 and config.notebook.keep_versions
    with pytest.raises(ValueError):
        StarlightConfig.model_validate({"notebook": {"versions": 0}})
