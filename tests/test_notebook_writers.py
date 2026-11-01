"""Block A2 of the notes plan (docs/plan-notes-app.md): every writer goes through the safe path
(starlight/notebook), so no change Starlight makes to notes, the journal, recipes, saved files
or memories is lost, and the undo tools put things back."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path

import pytest

from starlight.core import Kernel, Paths, safety
from tests.conftest import REPO, ScriptedProvider
from tests.test_home_devices import call, fails
from tests.test_mail import User


@pytest.fixture
def notebook(paths: Paths, use_module: Callable[[str], Path], documents: Path) -> Paths:
    for name in ("notes", "journal", "recipes", "files"):
        use_module(name)
    return paths


async def test_notes_keep_each_version_and_come_back_from_the_trash(
    notebook: Paths, documents: Path
) -> None:
    user = User(True)
    async with Kernel(notebook, ui=user) as k:
        made = await call(k, "notes.note", title="Shopping", text="milk")
        assert made["changed"]["new"] and made["changed"]["version"] is None
        added = await call(k, "notes.append", title="Shopping", text="eggs")
        assert added["changed"]["version"] and not added["changed"]["new"]
        kept = await call(k, "files.versions", path="Notes/Shopping.md")
        assert kept["count"] == 1 and kept["versions"][0]["reason"] == "notes.append"
        changes = await call(k, "files.changes", path="Notes/Shopping.md")
        assert "+eggs" in changes["changes"]
        restored = await call(k, "files.restore", path="Notes/Shopping.md")
        note = documents / "Starlight" / "Notes" / "Shopping.md"
        assert "eggs" not in note.read_text("utf-8") and restored["changed"]["version"]

        await call(k, "notes.delete", title="Shopping")  # asks: the user says yes
        assert not note.exists()
        trash = await call(k, "notes.trash")
        assert [n["title"] for n in trash["notes"]] == ["Shopping"]
        await call(k, "notes.restore_deleted", title="shop")
        assert "milk" in note.read_text("utf-8")
        empty = await fails(k, "notes.restore_deleted", title="Shopping")
        assert "no deleted note" in empty and "it is empty" in empty


async def test_the_journal_and_its_weekly_review_keep_versions(notebook: Paths) -> None:
    provider = ScriptedProvider("A good week.", "A better week.")
    async with Kernel(notebook, provider=provider) as k:
        first = await call(k, "journal.write", text="Morning run")
        assert first["changed"]["new"]
        second = await call(k, "journal.write", text="Dentist")
        assert second["changed"]["version"]
        await call(k, "journal.week")
        again = await call(k, "journal.week")
        assert again["changed"]["version"]  # the first review was kept before the second
        today = dt.date.today().isoformat()
        kept = await call(k, "files.versions", path=f"Journal/{today}.md")
    assert kept["count"] == 1 and kept["versions"][0]["reason"] == "journal.write"


async def test_recipes_keep_the_version_before_a_change(notebook: Paths) -> None:
    async with Kernel(notebook) as k:
        await call(k, "recipes.save", title="Pancakes", ingredients=["flour"], steps=["mix"])
        changed = await call(
            k, "recipes.save", title="Pancakes", ingredients=["flour", "milk"], steps=["mix"]
        )
        kept = await call(k, "files.versions", path="Recipes/Pancakes.md")
    assert changed["changed"]["version"] == kept["versions"][0]["id"]


async def test_saved_files_keep_a_version_and_a_file_elsewhere_asks_before_restore(
    notebook: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    user = User(True, True, True, True)
    elsewhere = tmp_path_factory.mktemp("Work") / "plan.txt"
    async with Kernel(notebook, ui=user) as k:
        await call(k, "files.save", path="letter.txt", content="Dear Tom,")
        replaced = await call(
            k, "files.save", path="letter.txt", content="Dear Anna,", if_exists="replace"
        )
        assert replaced["changed"]["version"]
        asked = len(user.questions)
        await call(k, "files.restore", path="letter.txt")  # inside the Starlight folder
        assert len(user.questions) == asked  # no question
        elsewhere.write_text("old plan", encoding="utf-8")
        await call(k, "files.save", path=str(elsewhere), content="new", if_exists="replace")
        asked = len(user.questions)
        await call(k, "files.restore", path=str(elsewhere))
        assert len(user.questions) == asked + 1 and "Put back" in user.questions[-1]
        assert (await call(k, "files.versions", path="nothing.txt"))["count"] == 0
        missing = await fails(k, "files.restore", path="letter.txt", version=999999)
    assert elsewhere.read_text("utf-8") == "old plan"
    assert "no version 999999" in missing


async def test_memories_keep_versions_and_come_back(notebook: Paths) -> None:
    user = User(True)
    async with Kernel(notebook, ui=user) as k:
        memory = k.memory
        await call(k, "memory.remember", fact="My car is a blue Golf.", topic="Car")
        entry = next(e for e in memory.store.all() if e.topic == "Car")
        entry.content = "My car is a red Polo."
        memory.store.write(entry)  # a change (as merging or tidying does)
        versions = await call(k, "memory.versions", memory_id=entry.id)
        assert versions["versions"]
        await call(k, "memory.undo", memory_id=entry.id)
        assert "blue Golf" in memory.store.path(entry.id).read_text("utf-8")
        await call(k, "memory.forget", memory_id=entry.id)  # asks: yes
        assert not memory.store.path(entry.id).exists()
        back = await call(k, "memory.undo", memory_id=entry.id)
        assert back["done"] == "brought back" and memory.store.path(entry.id).exists()
        assert "no earlier version" in await fails(k, "memory.undo", memory_id="never-was")


async def test_versions_can_be_switched_off(notebook: Paths) -> None:
    notebook.config_file.write_text(
        notebook.config_file.read_text() + "notebook:\n  keep_versions: false\n"
    )
    async with Kernel(notebook) as k:
        await call(k, "notes.note", title="Ideas", text="one")
        added = await call(k, "notes.append", title="Ideas", text="two")
        kept = await call(k, "files.versions", path="Notes/Ideas.md")
    assert added["changed"]["version"] is None and kept["count"] == 0


def test_no_writer_goes_around_the_safe_path() -> None:
    for module in ("notes", "journal", "recipes"):
        source = (REPO / "modules" / module / "module.py").read_text("utf-8")
        for direct in (".write_text(", '.open("a"', "write_atomic("):
            assert direct not in source, f"{module} writes directly ({direct})"
    store = (REPO / "starlight" / "memory" / "store.py").read_text("utf-8")
    assert "self.history.save(path" in store and "self.history.trash(path" in store
    files = (REPO / "modules" / "files" / "module.py").read_text("utf-8")
    assert "self.ctx.history.save(dest" in files


def test_keeping_versions_is_a_safety_limit() -> None:
    switch = safety.BY_KEY["versions"]
    assert switch.setting == "notebook.keep_versions" and switch.strict is True
    assert safety.GERMAN["versions"][0] and safety.GERMAN["versions_count"][0]
    count = safety.BY_KEY["versions_count"]
    assert count.setting == "notebook.versions" and count.check(50) == 50
    with pytest.raises(ValueError):
        count.check(1)
