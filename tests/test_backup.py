"""Backup and restore (starlight/core/backup.py): what is in the file, what is checked
before a file is trusted, and the restore that is staged and applied at the next start."""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

from starlight.cli import main
from starlight.core import backup
from starlight.core.backup import BackupError, BackupSources
from starlight.core.paths import Paths


@pytest.fixture
def pc(paths: Paths, tmp_path: Path) -> tuple[Paths, Path]:
    """A project with some of everything, and an output folder outside it."""
    out = tmp_path.parent / f"{tmp_path.name}-out"  # (outside the project folder)
    paths.config_file.write_text(
        "kill_switch:\n  hotkey: null\n  poll_interval: 0.02\n"
        f"output:\n  folder: {out.as_posix()}\n"
    )
    files = {
        "data/conversations/a.json": "chat one",
        "data/notebook/pinned.json": '["Notes/A.md"]',
        "data/skills.git/HEAD": "ref: refs/heads/main",
        "config/starlight.launcher.yaml": "model: {provider: lmstudio}\n",
        "config/starlight.remote.yaml": "web: {remote: {enabled: true}}\n",
        "config/starlight.safety.yaml": "level: careful\n",
        "data/notebook/flashcards.json": '{"cards": {}}',
        "data/devices/devices.json": "TOKENS",
        "data/app-window/profile": "cookies",
        "data/memory_index.sqlite": "index",
        "data/notebook/index.sqlite": "index",
        "data/core.lock": "",
        "memory/m1.md": "likes tea",
        "skills/s/skill.json": "{}",
        "config/servers.yaml": "servers: {}\n",
        "config/modules/notes.yaml": "x: 1\n",
        "config/vault_index.yaml": "keys: [mail.password]\n",
        "logs/starlight.log": "log",
        "STOP": "stop",
    }
    for name, text in files.items():
        path = paths.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    history = paths.data / "history" / "history.sqlite"
    history.parent.mkdir(parents=True)
    db = sqlite3.connect(history)
    db.execute("CREATE TABLE versions (id INTEGER PRIMARY KEY, path TEXT)")
    db.execute("INSERT INTO versions (path) VALUES ('Notes/A.md')")
    db.commit()
    db.close()
    (out / "Notes").mkdir(parents=True)
    (out / "Notes" / "A.md").write_text("# A")
    (out / "Journal").mkdir()
    (out / "Journal" / "2026-11-10.md").write_text("today")
    (out / "Notes" / ".hidden.tmp").write_text("temp")
    (out / "Notes" / "Attachments").mkdir()
    (out / "Notes" / "Attachments" / "photo.png").write_bytes(b"\x89PNG")
    (out / "Boards").mkdir()
    (out / "Boards" / "map.canvas").write_text("{}")
    (out / "Templates").mkdir()
    (out / "Templates" / "Meeting.md").write_text("# Meeting")
    (out / "Projects").mkdir()  # a folder of the user's own, and a loose file: notes too
    (out / "Projects" / "p.md").write_text("# project")
    (out / "todo.md").write_text("- [ ] tea")
    (out / "code").mkdir()  # the programs Starlight wrote: the index skips it, so does a backup
    (out / "code" / "tool.py").write_text("print()")
    (out / ".obsidian").mkdir()  # hidden at the top: not covered
    (out / ".obsidian" / "app.json").write_text("{}")
    return paths, out


def make(paths: Paths, out: Path, target: Path) -> dict[str, object]:
    return backup.create_backup(BackupSources(paths, out), target)


def zip_of(members: dict[str, bytes | str], manifest: object = None) -> bytes:
    """A crafted backup file."""
    if manifest is None:
        manifest = {"format": 1, "parts": {"data": {}, "notes": {}, "memory": {}}}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        if manifest != "none":
            text = manifest if isinstance(manifest, str) else json.dumps(manifest)
            zf.writestr("manifest.json", text)
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def refused(tmp_path: Path, data: bytes, match: str = "") -> None:
    file = tmp_path / "bad.zip"
    file.write_bytes(data)
    with pytest.raises(BackupError, match=match):
        backup.validate(file)


# -- what is in the file -------------------------------------------------------------------


def test_what_goes_in_and_what_stays_out(pc: tuple[Paths, Path], tmp_path: Path) -> None:
    paths, out = pc
    target = tmp_path / "b" / "one.zip"
    manifest = make(paths, out, target)
    names = set(zipfile.ZipFile(target).namelist())
    assert names == {
        "manifest.json",
        "data/conversations/a.json",
        "data/notebook/pinned.json",
        "config/starlight.safety.yaml",
        "data/notebook/flashcards.json",
        "data/history/history.sqlite",
        "memory/m1.md",
        "skills/s/skill.json",
        "config/servers.yaml",
        "config/modules/notes.yaml",
        "config/starlight.yaml",
        "config/vault_index.yaml",
        "notes/Notes/A.md",
        "notes/Journal/2026-11-10.md",
        "notes/Notes/Attachments/photo.png",
        "notes/Boards/map.canvas",
        "notes/Templates/Meeting.md",
        "notes/Projects/p.md",
        "notes/todo.md",
    }
    assert zipfile.ZipFile(target).namelist()[0] == "manifest.json"
    assert manifest["vault_keys"] == ["mail.password"]
    assert manifest["counts"] == {"conversations": 1, "notes": 5, "memories": 1}
    assert manifest["notes_folders"] == [
        "Boards", "Journal", "Notes", "Projects", "Templates"
    ]  # fmt: skip
    assert manifest["notes_files"] == ["todo.md"]
    assert manifest["skipped"] == 0 and not list(target.parent.glob("*.part"))
    assert backup.validate(target)["format"] == backup.FORMAT


def test_the_history_database_is_readable_after_a_round_trip(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    target = tmp_path / "one.zip"
    make(paths, out, target)
    (paths.data / "history" / "history.sqlite").unlink()
    (out / "Notes" / "A.md").write_text("changed")
    (out / "Journal" / "new.md").write_text("added later")
    backup.stage_restore(paths, target)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"], outcome
    db = sqlite3.connect(paths.data / "history" / "history.sqlite")
    assert db.execute("SELECT path FROM versions").fetchall() == [("Notes/A.md",)]
    db.close()
    assert (out / "Notes" / "A.md").read_text() == "# A"  # notes outside the root came back
    assert not (out / "Journal" / "new.md").exists()  # replaced, not merged
    assert (out / "code" / "tool.py").exists()  # never in a backup: untouched
    assert (out / ".obsidian" / "app.json").exists()
    assert (paths.memory / "m1.md").read_text() == "likes tea"
    # what is not in a backup is left alone
    assert (paths.data / "devices" / "devices.json").read_text() == "TOKENS"
    assert (paths.root / "STOP").read_text() == "stop"
    assert not (paths.data / "memory_index.sqlite").exists()
    assert not (paths.data / "notebook" / "index.sqlite").exists()


# -- refusing a file -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "../x",
        "memory/../../x",
        "/abs",
        "C:\\x",
        "memory\\a",
        "D:/x",
        "memory//a",
        "memory/./a",
        "memory/a.",
    ],
)
def test_unsafe_member_names_are_refused(tmp_path: Path, name: str) -> None:
    refused(tmp_path, zip_of({name: "x"}), "unsafe")


@pytest.mark.parametrize(
    "name",
    ["memory/NUL", "notes/Notes/con.md", "memory/Aux.txt", "memory/com1", "memory/LPT9.a.b",
     "memory/PRN", "skills/nul/x.md", "memory/CoM5.json"],
)  # fmt: skip
def test_windows_device_names_are_refused(tmp_path: Path, name: str) -> None:
    refused(tmp_path, zip_of({name: "x"}), "unsafe")


def test_names_that_only_look_like_device_names_are_fine(tmp_path: Path) -> None:
    file = tmp_path / "ok.zip"
    file.write_bytes(zip_of({"memory/console.md": "x", "memory/com10": "x", "memory/null.md": "x"}))
    assert backup.validate(file)["format"] == 1


def test_a_nul_in_a_name_is_refused() -> None:
    with pytest.raises(BackupError, match="unsafe"):
        backup._check_name("memory/a\x00b")  # (zipfile itself cuts such a name when writing)


def test_other_refusals(tmp_path: Path) -> None:
    refused(tmp_path, zip_of({"logs/x.log": "x"}), "not a place")
    refused(tmp_path, zip_of({"data/devices/d.json": "x"}), "not a place")
    refused(tmp_path, zip_of({"data/other.txt": "x"}), "not a place")
    refused(tmp_path, zip_of({"data/skills.git/hooks/post-commit": "#!/bin/sh"}), "not a place")
    refused(tmp_path, zip_of({"data/skills.git/config": "[core]"}), "not a place")
    refused(tmp_path, zip_of({"skills/.git/hooks/x": "x"}), "unsafe")
    refused(tmp_path, zip_of({"notes/Other/x.md": "x"}), "not a place")
    refused(tmp_path, zip_of({"config/a.txt": "x"}), "not a place")
    refused(tmp_path, zip_of({"memory/a": "1", "memory/a ": "2"}), "unsafe")
    refused(tmp_path, zip_of({"memory/a": "1", "Memory/A": "2"}), "twice")
    refused(tmp_path, b"this is not a zip", "damaged")
    refused(tmp_path, zip_of({"memory/a": "x"}, "none"), "no manifest")
    refused(tmp_path, zip_of({}, "{not json"), "manifest")
    refused(tmp_path, zip_of({}, "[1]"), "manifest")
    refused(tmp_path, zip_of({}, {"parts": {}}), "format")
    refused(tmp_path, zip_of({}, {"format": "1", "parts": {}}), "format")
    refused(tmp_path, zip_of({}, {"format": True, "parts": {}}), "format")
    refused(tmp_path, zip_of({}, {"format": 0, "parts": {}}), "format")
    refused(tmp_path, zip_of({}, {"format": 1}), "manifest")
    refused(tmp_path, zip_of({}, {"format": 2, "parts": {}}), "newer Starlight. Update Starlight")


def test_a_duplicate_name_is_refused(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": 1, "parts": {}}))
        zf.writestr("memory/a", "1")
        with pytest.warns(UserWarning):  # (zipfile itself warns about the duplicate)
            zf.writestr("memory/a", "2")
    refused(tmp_path, buffer.getvalue(), "twice")


def test_a_symlink_entry_is_refused(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format": 1, "parts": {}}))
        link = zipfile.ZipInfo("memory/link")
        link.external_attr = 0o120777 << 16
        zf.writestr(link, "/etc/passwd")
    refused(tmp_path, buffer.getvalue(), "link")


def test_the_manifest_has_a_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backup, "MAX_MANIFEST", 50)
    refused(tmp_path, zip_of({}, {"format": 1, "parts": {}, "pad": "x" * 200}), "too big")


def test_caps_on_members_and_sizes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    many = zip_of({f"memory/{n}": "x" for n in range(5)})
    monkeypatch.setattr(backup, "MAX_MEMBERS", 4)
    refused(tmp_path, many, "too many")
    monkeypatch.setattr(backup, "MAX_MEMBERS", 100)
    monkeypatch.setattr(backup, "MAX_FILE", 10)
    refused(tmp_path, zip_of({"memory/a": "x" * 11}), "too big")
    monkeypatch.setattr(backup, "MAX_FILE", 100)
    monkeypatch.setattr(backup, "MAX_TOTAL", 15)
    refused(tmp_path, zip_of({"memory/a": "x" * 10, "memory/b": "x" * 10}), "too big")


def test_a_file_that_is_bigger_than_its_header_says_is_not_unpacked(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, _ = pc
    raw = bytearray(zip_of({"memory/a": b"A" * 4000}))
    size = (4000).to_bytes(4, "little")  # make the header lie: it says 10 bytes
    lie = (10).to_bytes(4, "little")
    while size in raw:
        at = raw.index(size)
        raw[at : at + 4] = lie
    file = tmp_path / "lie.zip"
    file.write_bytes(bytes(raw))
    with pytest.raises(BackupError):
        backup.stage_restore(paths, file)
    assert not backup.pending_file(paths).exists()
    assert not list((paths.root / "backups").glob(".restore-*"))


def test_the_real_bytes_count_not_the_header(
    pc: tuple[Paths, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = pc
    file = tmp_path / "ok.zip"
    file.write_bytes(zip_of({"memory/a": b"A" * 100}))
    monkeypatch.setattr(backup, "_validate", lambda zf: {"format": 1, "parts": {}})
    monkeypatch.setattr(backup, "MAX_FILE", 50)  # the header check was skipped above
    with pytest.raises(BackupError, match="more than it says"):
        backup.stage_restore(paths, file)
    assert not list((paths.root / "backups").glob(".restore-*"))


def test_a_corrupt_member_is_refused_at_staging(pc: tuple[Paths, Path], tmp_path: Path) -> None:
    paths, _ = pc
    raw = bytearray(zip_of({"memory/a": b"hello world" * 50}))
    raw[raw.index(b"hello world") + 3] ^= 0xFF  # flipped inside the (stored) data: bad CRC
    file = tmp_path / "crc.zip"
    file.write_bytes(bytes(raw))
    with pytest.raises(BackupError, match="damaged"):
        backup.stage_restore(paths, file)
    assert not backup.pending_file(paths).exists()


# -- staging and applying at start -----------------------------------------------------------


def staged(pc: tuple[Paths, Path], tmp_path: Path) -> Path:
    paths, out = pc
    target = tmp_path / "saved.zip"
    make(paths, out, target)
    (paths.memory / "m1.md").write_text("changed after the backup")
    (out / "Notes" / "A.md").write_text("changed too")
    backup.stage_restore(paths, target)
    return target


def test_staging_changes_nothing_yet(pc: tuple[Paths, Path], tmp_path: Path) -> None:
    paths, _ = pc
    staged(pc, tmp_path)
    assert (paths.memory / "m1.md").read_text() == "changed after the backup"
    pending = backup.read_pending(paths)
    assert pending and pending["manifest"]["format"] == 1
    assert (paths.root / "backups" / pending["stage"] / "memory" / "m1.md").exists()
    assert backup.cancel_pending(paths) is True
    assert not backup.pending_file(paths).exists()
    assert not list((paths.root / "backups").glob(".restore-*"))
    assert backup.cancel_pending(paths) is False


def test_applying_saves_the_current_state_first(pc: tuple[Paths, Path], tmp_path: Path) -> None:
    paths, out = pc
    staged(pc, tmp_path)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"] is True
    safety = paths.root / "backups" / outcome["safety"]
    assert safety.name.startswith("before-restore-")
    with zipfile.ZipFile(safety) as zf:  # it holds what was there before
        assert zf.read("memory/m1.md") == b"changed after the backup"
        assert zf.read("notes/Notes/A.md") == b"changed too"
    assert (paths.memory / "m1.md").read_text() == "likes tea"
    result = backup.last_result(paths)
    assert result and result["ok"] is True
    assert not backup.pending_file(paths).exists()
    assert not list((paths.root / "backups").glob(".restore-*"))
    assert not list(paths.root.rglob(".*.old-*")) and not list(out.rglob(".*.old-*"))


def test_only_the_newest_three_safety_copies_are_kept(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, _ = pc
    folder = paths.root / "backups"
    folder.mkdir()
    for day in range(1, 5):
        (folder / f"before-restore-2020010{day}-000000.zip").write_bytes(b"old")
    (folder / "starlight-backup-20200101-000000.zip").write_bytes(b"mine")
    staged(pc, tmp_path)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"]
    kept = sorted(p.name for p in folder.glob("before-restore-*.zip"))
    assert len(kept) == 3 and outcome["safety"] in kept
    assert "before-restore-20200101-000000.zip" not in kept
    assert (folder / "starlight-backup-20200101-000000.zip").exists()  # only its own kind


def snapshot_of(paths: Paths) -> dict[Path, bytes]:
    return {
        p.relative_to(paths.root): p.read_bytes()
        for p in paths.root.rglob("*")
        if p.is_file()
        and "backups" not in p.parts
        and p.name not in ("restore-result.json", "restore-pending.json")
    }


def test_a_failing_swap_rolls_back(
    pc: tuple[Paths, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, out = pc
    staged(pc, tmp_path)
    hidden = hidden_notes(out)
    before = snapshot_of(paths)
    real = backup.replace_file
    calls = {"n": 0}

    def flaky(source: Path | str, target: Path | str) -> None:
        calls["n"] += 1
        if calls["n"] == 8:  # once; the rollback must work
            raise PermissionError("locked")
        real(source, target)

    monkeypatch.setattr(backup, "replace_file", flaky)
    outcome = backup.apply_pending(paths)
    monkeypatch.undo()
    assert calls["n"] >= 8
    assert outcome and outcome["ok"] is False and "locked" in outcome["reason"]
    assert outcome["safety"].startswith("before-restore-")
    assert snapshot_of(paths) == before
    assert (out / "Notes" / "A.md").read_text() == "changed too"
    for name, text in hidden.items():
        assert (out / name).read_text() == text  # the old folder went back whole
    assert not list(paths.root.rglob(".*.old-*")) and not list(out.rglob(".*.old-*"))
    result = backup.last_result(paths)
    assert result and result["ok"] is False
    assert not backup.pending_file(paths).exists()


def hidden_notes(out: Path) -> dict[str, str]:
    """Hidden things a user keeps inside the note folders, and a note in a new folder."""
    things = {
        "Notes/.git/config": "[core]",
        "Notes/Projects/.obsidian/app.json": "{}",
        "Notes/Projects/plan.md": "# plan",
        "Notes/.stignore": "x",
    }
    for name, text in things.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(text)
    return things


def test_hidden_files_in_note_folders_stay_where_they_are(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    staged(pc, tmp_path)
    things = hidden_notes(out)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"] and "reason" not in outcome, outcome
    assert (out / "Notes" / "A.md").read_text() == "# A"  # restored
    assert not (out / "Notes" / "Projects" / "plan.md").exists()  # the old notes are replaced
    for name in (n for n in things if "/." in n):
        assert (out / name).read_text() == things[name], name  # hidden: in place, unchanged
    assert not list(out.rglob(".*.old-*"))


def test_hidden_files_in_a_folder_the_backup_holds_empty_survive(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    shutil.rmtree(out / "Journal")
    (out / "Journal").mkdir()  # (an empty folder: the backup lists it but holds no file in it)
    target = tmp_path / "saved.zip"
    make(paths, out, target)
    (out / "Journal" / "today.md").write_text("a note")
    (out / "Journal" / ".stfolder").mkdir()
    (out / "Journal" / ".stfolder" / "marker").write_text("sync")
    backup.stage_restore(paths, target)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"] and "reason" not in outcome, outcome
    assert not (out / "Journal" / "today.md").exists()
    assert (out / "Journal" / ".stfolder" / "marker").read_text() == "sync"
    assert not list(out.rglob(".*.old-*"))


def test_a_hidden_file_that_cannot_be_moved_keeps_the_old_folder(
    pc: tuple[Paths, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, out = pc
    staged(pc, tmp_path)
    hidden_notes(out)
    real = backup.replace_file

    def locked(source: Path | str, target: Path | str) -> None:
        if Path(source).name == ".git":
            raise PermissionError("locked")
        real(source, target)

    monkeypatch.setattr(backup, "replace_file", locked)
    outcome = backup.apply_pending(paths)
    monkeypatch.undo()
    assert outcome and outcome["ok"] is True  # the data is restored
    assert (out / "Notes" / "A.md").read_text() == "# A"
    old = list(out.glob(".Notes.old-*"))
    assert len(old) == 1 and str(old[0]) in outcome["reason"]
    assert "hidden files of your old notes" in outcome["reason"]
    assert (old[0] / ".git" / "config").read_text() == "[core]"  # nothing is lost
    assert (out / "Notes" / "Projects" / ".obsidian" / "app.json").exists()  # the rest moved
    result = backup.last_result(paths)
    assert result and result["reason"] == outcome["reason"]


def test_a_hidden_name_the_new_folder_already_has_keeps_the_old_folder(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    staged(pc, tmp_path)
    hidden_notes(out)
    pending = backup.read_pending(paths)
    assert pending
    clash = paths.root / "backups" / pending["stage"] / "notes" / "Notes" / ".git"
    clash.mkdir()
    (clash / "config").write_text("from the stage")
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"] and "still in" in outcome["reason"]
    assert (out / "Notes" / ".git" / "config").read_text() == "from the stage"  # the new wins
    (old,) = out.glob(".Notes.old-*")
    assert (old / ".git" / "config").read_text() == "[core]"


def test_a_failing_safety_copy_changes_nothing(
    pc: tuple[Paths, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _ = pc
    staged(pc, tmp_path)

    def full(*args: object, **kwargs: object) -> None:
        raise BackupError("disk full")

    monkeypatch.setattr(backup, "create_backup", full)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"] is False and "disk full" in outcome["reason"]
    assert (paths.memory / "m1.md").read_text() == "changed after the backup"
    assert not backup.pending_file(paths).exists()


@pytest.mark.parametrize(
    "text", ["{not json", "[]", '{"manifest": {}}', '{"manifest": {}, "stage": "../x"}']
)
def test_a_damaged_pending_file_does_not_stop_the_start(pc: tuple[Paths, Path], text: str) -> None:
    paths, _ = pc
    backup.pending_file(paths).write_text(text)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"] is False
    assert (paths.memory / "m1.md").read_text() == "likes tea"
    result = backup.last_result(paths)
    assert result and result["reason"]
    assert not backup.pending_file(paths).exists()


def test_nothing_pending_is_nothing(pc: tuple[Paths, Path]) -> None:
    assert backup.apply_pending(pc[0]) is None


def test_a_running_starlight_keeps_the_restore_staged(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    from starlight.core.corelock import CoreLock

    paths, _ = pc
    staged(pc, tmp_path)
    lock = CoreLock(paths.data / "core.lock")
    assert lock.acquire()
    try:
        assert backup.apply_pending(paths) is None
    finally:
        lock.release()
    assert backup.pending_file(paths).exists()
    assert (paths.memory / "m1.md").read_text() == "changed after the backup"


def test_a_backup_without_notes_leaves_current_notes_alone(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    target = tmp_path / "nonotes.zip"
    backup.create_backup(BackupSources(paths, None), target)
    backup.stage_restore(paths, target)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"]
    assert (out / "Notes" / "A.md").read_text() == "# A"


def test_the_start_applies_a_staged_restore_before_a_kernel_exists(
    pc: tuple[Paths, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every front end goes through cli._main (web, run, serve, ask, app)."""
    paths, _ = pc
    staged(pc, tmp_path)
    seen: list[str] = []

    def web(p: Paths, args: object) -> int:
        seen.append((p.memory / "m1.md").read_text())
        return 0

    monkeypatch.setattr("starlight.cli.cmd_web", web)
    assert main(["--home", str(paths.root), "web"]) == 0
    assert seen == ["likes tea"]


# -- the command line ----------------------------------------------------------------------


def test_the_cli_backup_and_restore(
    pc: tuple[Paths, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, _ = pc
    assert main(["--home", str(paths.root), "backup", "--to", str(tmp_path / "dest")]) == 0
    files = list((tmp_path / "dest").glob("starlight-backup-*.zip"))
    assert len(files) == 1 and "Backup saved" in capsys.readouterr().out
    assert main(["--home", str(paths.root), "backup"]) == 0  # default: the backups folder
    assert list((paths.root / "backups").glob("starlight-backup-*.zip"))
    (paths.memory / "m1.md").write_text("changed")
    assert main(["--home", str(paths.root), "restore", str(files[0])]) == 0
    assert (paths.memory / "m1.md").read_text() == "likes tea"  # not running: applied at once
    assert "Restored" in capsys.readouterr().out
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"nope")
    assert main(["--home", str(paths.root), "restore", str(bad)]) == 1
    assert "not a backup" in capsys.readouterr().err
    assert main(["--home", str(paths.root), "restore", str(tmp_path / "missing.zip")]) == 1


def test_the_cli_restore_stages_when_starlight_runs(
    pc: tuple[Paths, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from starlight.core.corelock import CoreLock

    paths, out = pc
    target = tmp_path / "saved.zip"
    make(paths, out, target)
    (paths.memory / "m1.md").write_text("changed")
    lock = CoreLock(paths.data / "core.lock")
    assert lock.acquire()
    try:
        assert main(["--home", str(paths.root), "restore", str(target)]) == 0
    finally:
        lock.release()
    assert "start it again" in capsys.readouterr().out
    assert (paths.memory / "m1.md").read_text() == "changed"
    assert backup.pending_file(paths).exists()


def test_the_skills_history_is_not_backed_up_nor_touched_by_a_restore(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    target = tmp_path / "one.zip"
    make(paths, out, target)
    assert not [n for n in zipfile.ZipFile(target).namelist() if "skills.git" in n]
    assert "skills/s/skill.json" in zipfile.ZipFile(target).namelist()
    own = paths.data / "skills.git" / "hooks" / "mine"
    own.parent.mkdir(parents=True)
    own.write_text("mine")
    (paths.skills / "s" / "skill.json").write_text("changed")
    backup.stage_restore(paths, target)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"], outcome
    assert (paths.skills / "s" / "skill.json").read_text() == "{}"
    assert own.read_text() == "mine"
    assert (paths.data / "skills.git" / "HEAD").read_text() == "ref: refs/heads/main"


def test_this_pcs_own_config_files_are_left_out_and_left_alone(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    target = tmp_path / "one.zip"
    make(paths, out, target)
    names = zipfile.ZipFile(target).namelist()
    assert "config/starlight.safety.yaml" in names and "config/starlight.yaml" in names
    assert not [n for n in names if "launcher" in n or "remote" in n]
    # an older backup that holds them: accepted, but they are ignored on restore
    old = tmp_path / "old.zip"
    with zipfile.ZipFile(old, "w") as zf:
        manifest = {"format": 1, "parts": {}, "notes_included": False}
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("config/starlight.launcher.yaml", "model: {provider: other}\n")
        zf.writestr("config/starlight.remote.yaml", "web: {remote: {enabled: false}}\n")
        zf.writestr("config/servers.yaml", "servers: {a: 1}\n")
    backup.validate(old)
    launcher = paths.config / "starlight.launcher.yaml"
    remote = paths.config / "starlight.remote.yaml"
    backup.stage_restore(paths, old)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"], outcome
    assert launcher.read_text() == "model: {provider: lmstudio}\n"
    assert remote.read_text() == "web: {remote: {enabled: true}}\n"
    assert "a: 1" in (paths.config / "servers.yaml").read_text()
    # and a backup without them does not delete the current ones either
    backup.stage_restore(paths, target)
    assert backup.apply_pending(paths)
    assert launcher.exists() and remote.exists()


def test_pictures_boards_and_the_flashcard_schedule_come_back(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    target = tmp_path / "one.zip"
    make(paths, out, target)
    (out / "Notes" / "Attachments" / "photo.png").unlink()
    (out / "Boards" / "map.canvas").unlink()
    (paths.data / "notebook" / "flashcards.json").write_text("changed")
    backup.stage_restore(paths, target)
    assert backup.apply_pending(paths)
    assert (out / "Notes" / "Attachments" / "photo.png").read_bytes() == b"\x89PNG"
    assert (out / "Boards" / "map.canvas").read_text() == "{}"
    assert (paths.data / "notebook" / "flashcards.json").read_text() == '{"cards": {}}'


def test_an_older_backup_does_not_remove_what_it_never_held(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    old = tmp_path / "old.zip"
    manifest = {
        "format": 1,
        "parts": {},
        "notes_included": True,
        "notes_folders": ["Notes", "Journal", "Recipes", "Clippings", "Inbox"],
    }
    with zipfile.ZipFile(old, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("notes/Notes/B.md", "# B")
    backup.stage_restore(paths, old)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"], outcome
    assert (out / "Notes" / "B.md").exists() and not (out / "Notes" / "A.md").exists()
    assert (out / "Boards" / "map.canvas").exists()  # an older backup had no Boards
    assert (out / "Templates" / "Meeting.md").exists()
    assert (paths.data / "notebook" / "flashcards.json").read_text() == '{"cards": {}}'


def test_folders_of_your_own_and_loose_files_are_in_and_replaced(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    target = tmp_path / "one.zip"
    make(paths, out, target)
    (out / "Projects" / "p.md").write_text("changed")
    (out / "Projects" / "extra.md").write_text("added later")
    (out / "todo.md").unlink()
    (out / "Newer").mkdir()  # a folder the backup never held: left alone
    (out / "Newer" / "n.md").write_text("n")
    (out / "other.md").write_text("a loose file the backup never held")
    backup.stage_restore(paths, target)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"], outcome
    assert (out / "Projects" / "p.md").read_text() == "# project"
    assert not (out / "Projects" / "extra.md").exists()
    assert (out / "todo.md").read_text() == "- [ ] tea"
    assert (out / "Newer" / "n.md").exists() and (out / "other.md").exists()


def test_the_manifest_decides_which_notes_places_are_allowed(tmp_path: Path) -> None:
    ok = {"format": 1, "parts": {}, "notes_folders": ["Mine"], "notes_files": ["a.md"]}
    file = tmp_path / "ok.zip"
    file.write_bytes(
        zip_of({"notes/Mine/x.md": "x", "notes/a.md": "y", "notes/Mine/s/t.md": "z"}, ok)
    )
    assert backup.validate(file)["notes_files"] == ["a.md"]
    refused(tmp_path, zip_of({"notes/Other/x.md": "x"}, ok), "not a place")
    refused(tmp_path, zip_of({"notes/b.md": "x"}, ok), "not a place")
    refused(tmp_path, zip_of({"notes/a.md/x": "x"}, ok), "not a place")
    refused(tmp_path, zip_of({"notes": "x"}, ok), "not a place")
    for bad in (["../x"], ["a/b"], ["con"], [5], ["x."], "Mine"):
        refused(tmp_path, zip_of({}, {**ok, "notes_folders": bad}), "manifest")
    # the folder of Starlight's programs is never a notes place, however the file spells it
    for programs in ("code", "Code", "CODE"):
        listed = {**ok, "notes_folders": [programs]}
        refused(tmp_path, zip_of({f"notes/{programs}/run.py": "x"}, listed), "manifest")
        refused(tmp_path, zip_of({}, {**ok, "notes_files": [programs]}), "manifest")
    refused(tmp_path, zip_of({}, {**ok, "notes_files": [".hidden"]}), "manifest")
    # an older manifest lists nothing: the five folders it held
    old = {"format": 1, "parts": {}}
    file.write_bytes(zip_of({"notes/Journal/x.md": "x"}, old))
    backup.validate(file)
    refused(tmp_path, zip_of({"notes/Projects/x.md": "x"}, old), "not a place")


def test_names_a_restore_would_refuse_are_left_out_and_counted(
    pc: tuple[Paths, Path], tmp_path: Path
) -> None:
    paths, out = pc
    (out / "con").mkdir()
    (out / "con" / "x.md").write_text("x")
    (out / "dots.").mkdir()
    (out / "dots." / "x.md").write_text("x")
    (out / "aux.md").write_text("x")
    (out / "Notes" / "nul.md").write_text("x")
    (out / "Notes" / "b.md").write_text("1")
    (out / "Notes" / "B.md").write_text("2")  # one file on Windows
    target = tmp_path / "one.zip"
    manifest = make(paths, out, target)
    names = zipfile.ZipFile(target).namelist()
    assert not [n for n in names if "con" in n.lower().split("/")[1:2] or "aux" in n]
    assert "notes/Notes/A.md" in names and "notes/Notes/nul.md" not in names
    assert sum(n.casefold() == "notes/notes/b.md" for n in names) == 1
    assert manifest["skipped"] == 5 and manifest["unsafe_count"] == 5
    assert "con" not in manifest["notes_folders"] and "dots." not in manifest["notes_folders"]
    assert "5 files with a name that Windows cannot hold are not in the backup" in " ".join(
        manifest["left_out"]
    )
    assert backup.validate(target)["format"] == 1  # a backup made is always one that is read


def test_a_file_over_the_limit_is_left_out_and_named(
    pc: tuple[Paths, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, out = pc
    (out / "Notes" / "big1.bin").write_bytes(b"x" * 12000)
    (out / "Notes" / "big2.bin").write_bytes(b"x" * 13000)
    monkeypatch.setattr(backup, "MAX_FILE", 10000)
    target = tmp_path / "one.zip"
    manifest = make(paths, out, target)
    names = zipfile.ZipFile(target).namelist()
    assert "notes/Notes/A.md" in names and not [n for n in names if "big" in n]
    assert manifest["skipped"] == 2 and manifest["too_big"] == [
        "notes/Notes/big1.bin",
        "notes/Notes/big2.bin",
    ]
    line = manifest["left_out"][0]
    assert line.startswith("2 files over 10 KB are not in the backup: notes/Notes/big1.bin")
    assert backup.validate(target)["format"] == 1  # (with the same, lowered limit)
    assert backup.check_backup(backup.BackupSources(paths, out))["left_out"] == [line]


def test_a_backup_that_could_not_be_restored_is_not_written(
    pc: tuple[Paths, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, out = pc
    (out / "Projects" / "big.bin").write_bytes(b"x" * 3000)
    target = tmp_path / "b" / "one.zip"
    monkeypatch.setattr(backup, "MAX_TOTAL", 2000)
    with pytest.raises(BackupError, match="Your data is") as error:
        make(paths, out, target)
    assert "notes/Projects" in str(error.value) and "(2 KB)" in str(error.value)
    assert not target.exists() and not list(target.parent.glob("*"))
    monkeypatch.setattr(backup, "MAX_TOTAL", 10**9)
    monkeypatch.setattr(backup, "MAX_MEMBERS", 5)
    with pytest.raises(BackupError, match="more than a backup can hold") as error:
        make(paths, out, target)
    assert "files" in str(error.value) and not target.exists()
    with pytest.raises(BackupError):
        backup.check_backup(backup.BackupSources(paths, out))


def test_the_safety_copy_follows_the_same_rules(
    pc: tuple[Paths, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, out = pc
    staged(pc, tmp_path)
    monkeypatch.setattr(backup, "MAX_TOTAL", 10)  # (the staged files are already unpacked)
    outcome = backup.apply_pending(paths)
    assert outcome and outcome["ok"] is False and "nothing was changed" in outcome["reason"]
    assert "more than a backup can hold" in outcome["reason"]
    assert (paths.memory / "m1.md").read_text() == "changed after the backup"
    assert (out / "Notes" / "A.md").read_text() == "changed too"


def test_the_cli_says_what_was_left_out_and_what_is_too_big(
    pc: tuple[Paths, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, out = pc
    (out / "Notes" / "big.bin").write_bytes(b"x" * 12000)
    monkeypatch.setattr(backup, "MAX_FILE", 10000)
    assert main(["--home", str(paths.root), "backup", "--to", str(tmp_path / "d")]) == 0
    assert "1 file over 10 KB is not in the backup: notes/Notes/big.bin" in (
        capsys.readouterr().out
    )
    monkeypatch.setattr(backup, "MAX_TOTAL", 100)
    assert main(["--home", str(paths.root), "backup", "--to", str(tmp_path / "e")]) == 1
    assert "more than a backup can hold" in capsys.readouterr().err
    assert not (tmp_path / "e").exists() or not list((tmp_path / "e").iterdir())
