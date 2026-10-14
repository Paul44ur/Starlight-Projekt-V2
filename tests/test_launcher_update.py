"""--update keeps a way back: a copy of settings and data, the previous uv and llama.cpp,
and --rollback to return to them."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_launcher import L, QuietUi


class Asking(QuietUi):
    def __init__(self, answer: bool) -> None:
        super().__init__()
        self.answer = answer
        self.asked: list[str] = []

    def ask(self, question: str, default: bool) -> bool:
        self.asked.append(question)
        return self.answer


@pytest.fixture
def home(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "starlight"\n')
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "starlight.yaml").write_text("web:\n  port: 8701\n")
    (tmp_path / "data" / "conversations").mkdir(parents=True)
    (tmp_path / "data" / "conversations" / "abc.json").write_text('{"messages": []}')
    (tmp_path / "data" / "stats.db").write_bytes(b"x" * 100)
    (tmp_path / "data" / "core.lock").write_text("1")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "profile.md").write_text("name: Paul")
    return tmp_path


def settings_of(home: Path, **kwargs: Any) -> Any:
    settings = L.Settings(root=home, variant="cpu", **kwargs)
    return settings


def program(settings: Any, name: str, version: str) -> Path:
    """An installed program folder with one file telling its version."""
    folder = L.installed_folders(settings)[name]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "version.txt").write_text(version)
    (folder / "source.txt").write_text(f"download of {version}\n")
    return folder


# -- the copy before an update ---------------------------------------------------------------------


def test_settings_data_and_memory_are_copied_before_an_update(home: Path) -> None:
    ui = QuietUi()
    copy = L.snapshot_before_update(settings_of(home), ui)
    assert copy is not None and copy.parent == home / "backups"
    assert (copy / "config" / "starlight.yaml").read_text() == "web:\n  port: 8701\n"
    assert (copy / "data" / "conversations" / "abc.json").is_file()
    assert (copy / "memory" / "profile.md").read_text() == "name: Paul"
    assert not (copy / "data" / "stats.db").exists()  # rebuilt by Starlight
    assert not (copy / "data" / "core.lock").exists()
    assert any("--rollback" in line for line in ui.lines)


def test_only_the_newest_copies_are_kept(home: Path) -> None:
    settings = settings_of(home)
    for n in range(5):
        old = home / "backups" / f"update-2026010{n}-000000"
        (old / "config").mkdir(parents=True)
    made = L.snapshot_before_update(settings, QuietUi())
    names = sorted(p.name for p in (home / "backups").iterdir())
    assert len(names) == L.BACKUPS_KEEP and made is not None and made.name in names
    assert "update-20260100-000000" not in names


def test_two_copies_in_one_second_do_not_collide(home: Path) -> None:
    settings = settings_of(home)
    first = L.snapshot_before_update(settings, QuietUi())
    second = L.snapshot_before_update(settings, QuietUi())
    assert first is not None and second is not None and first != second


def test_nothing_to_copy_is_fine(tmp_path: Path) -> None:
    assert L.snapshot_before_update(settings_of(tmp_path), QuietUi()) is None


# -- the old program stays until the new one is in -------------------------------------------------


def test_a_failed_update_puts_the_old_program_back(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_of(home, update=True)
    folder = program(settings, "llama.cpp-cpu", "old")
    (folder / "llama-server").write_text("old server")
    monkeypatch.setattr(L, "resolve_llama", lambda s: ("https://x/new.zip", None))
    monkeypatch.setattr(L, "download", lambda *a, **k: home / "new.zip")

    def broken(*args: Any, **kwargs: Any) -> None:
        raise L.LauncherError("new.zip is not a complete archive")

    monkeypatch.setattr(L, "extract_member", broken)
    with pytest.raises(L.LauncherError, match="complete archive"):
        L.ensure_llama(settings, QuietUi())
    assert (folder / "version.txt").read_text() == "old"
    assert not L.previous_folder(settings, "llama.cpp-cpu").exists()


def test_a_good_update_keeps_the_old_program_as_previous(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_of(home, update=True)
    folder = program(settings, "llama.cpp-cpu", "old")
    archive = home / "new.zip"
    archive.write_bytes(b"zip")
    monkeypatch.setattr(L, "resolve_llama", lambda s: ("https://x/new.zip", None))
    monkeypatch.setattr(L, "download", lambda *a, **k: archive)

    def unpack(archive: Path, name: str, target: Path, keep: tuple[str, ...] = ()) -> Path:
        target.mkdir(parents=True, exist_ok=True)
        (target / "version.txt").write_text("new")
        return target / name

    monkeypatch.setattr(L, "extract_member", unpack)
    L.ensure_llama(settings, QuietUi())
    assert (folder / "version.txt").read_text() == "new"
    assert (folder / "source.txt").read_text().strip() == "https://x/new.zip"
    assert (L.previous_folder(settings, "llama.cpp-cpu") / "version.txt").read_text() == "old"


def test_a_program_in_use_stops_the_update_with_a_clear_message(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_of(home)
    folder = program(settings, "uv", "old")

    def busy(source: Any, target: Any) -> None:
        raise PermissionError(13, "being used by another process")

    monkeypatch.setattr(L, "replace_file", busy)
    with pytest.raises(L.LauncherError, match="in use: close Starlight"):
        L.set_aside(settings, folder, "uv")
    assert (folder / "version.txt").read_text() == "old"


# -- --rollback ------------------------------------------------------------------------------------


def updated_home(home: Path) -> Any:
    settings = settings_of(home)
    program(settings, "uv", "uv-new")
    program(settings, "llama.cpp-cpu", "llama-new")
    for name, version in (("uv", "uv-old"), ("llama.cpp-cpu", "llama-old")):
        kept = L.previous_folder(settings, name)
        kept.mkdir(parents=True)
        (kept / "version.txt").write_text(version)
        (kept / "source.txt").write_text(f"download of {version}\n")
    return settings


def test_rollback_goes_back_and_a_second_one_goes_forward(home: Path) -> None:
    settings = updated_home(home)
    uv, llama = L.installed_folders(settings)["uv"], L.installed_folders(settings)["llama.cpp-cpu"]
    ui = Asking(answer=False)
    assert L.rollback(settings, ui) == 0
    assert (uv / "version.txt").read_text() == "uv-old"
    assert (llama / "version.txt").read_text() == "llama-old"
    assert any("download of llama-old" in line for line in ui.lines)
    L.rollback(settings, Asking(answer=False))
    assert (uv / "version.txt").read_text() == "uv-new"
    assert (llama / "version.txt").read_text() == "llama-new"


def test_rollback_without_an_earlier_version_says_so(home: Path) -> None:
    with pytest.raises(L.LauncherError, match="no earlier version"):
        L.rollback(settings_of(home), QuietUi())


def test_rollback_can_put_settings_and_data_back_too(home: Path) -> None:
    settings = updated_home(home)
    copy = L.snapshot_before_update(settings, QuietUi())
    assert copy is not None
    (home / "config" / "starlight.yaml").write_text("web:\n  port: 9999\n")  # changed after
    (home / "data" / "conversations" / "new.json").write_text("{}")
    ui = Asking(answer=True)
    L.rollback(settings, ui)
    assert (home / "config" / "starlight.yaml").read_text() == "web:\n  port: 8701\n"
    assert (home / "data" / "conversations" / "new.json").is_file()  # newer files stay
    assert ui.asked and "settings and data" in ui.asked[0]


def test_rollback_leaves_settings_alone_unless_asked(home: Path) -> None:
    settings = updated_home(home)
    L.snapshot_before_update(settings, QuietUi())
    (home / "config" / "starlight.yaml").write_text("web:\n  port: 9999\n")
    L.rollback(settings, Asking(answer=False))
    assert (home / "config" / "starlight.yaml").read_text() == "web:\n  port: 9999\n"


def test_the_command_line_knows_rollback(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args = L.build_parser().parse_args(["--rollback"])
    assert not L.gui_wanted(args)  # text only
    assert L.main(["--rollback", "--root", str(home), "--yes"]) == 1
    assert "no earlier version" in capsys.readouterr().out
