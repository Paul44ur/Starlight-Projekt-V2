"""Block C1 of the notes plan (docs/plan-notes-app.md): links between notes. A rename
changes every link to the note (keeping a version of each note first), the assistant asks
before it changes other notes, and its tools follow links both ways."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from starlight.core import Kernel, Paths
from starlight.notebook import History
from starlight.notebook.history import HistoryError
from starlight.notebook.index import Index
from starlight.notebook.links import retarget
from starlight.notebook.markdown import wikilink_spans
from starlight.notebook.rename import plan, rename
from tests.test_home_devices import call, fails
from tests.test_mail import User

NOTES = {
    "Notes/Old.md": "---\naliases: [Other name]\n---\n# Old\nSee [[Old#Old]] (itself).\n",
    "Notes/A.md": (
        "[[Old]], [[old|shown]], [[Old#Part]], ![[Old]] and [[Notes/Old]].\n"
        "By alias: [[Other name]]. In code: `[[Old]]`\n\n```\n[[Old]]\n```\n"
        "| a | [[Old\\|in a table]] |\n"
    ),
    "Projects/Old.md": "Another note with the same name.\n",
    "Projects/B.md": "[[Old]] here means Projects/Old. [[Notes/Old]] means the other.\n",
    "Notes/C.md": "Nothing about it.\n",
}


def vault(root: Path) -> Path:
    for relative, text in NOTES.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


def test_retarget_keeps_folders_parts_and_shown_text() -> None:
    assert retarget("Old", "New") == "New"
    assert retarget("Old|shown", "New") == "New|shown"
    assert retarget("Old#Part", "New") == "New#Part"
    assert retarget("Notes/Old.md#^id", "New") == "Notes/New.md#^id"
    assert retarget("Old\\|in a table", "New") == "New\\|in a table"


def test_link_spans_skip_code() -> None:
    text = NOTES["Notes/A.md"]
    spans = wikilink_spans(text)
    assert [text[s:e] for s, e, _, _ in spans] == [
        "[[Old]]", "[[old|shown]]", "[[Old#Part]]", "![[Old]]", "[[Notes/Old]]",
        "[[Other name]]", "[[Old\\|in a table]]",
    ]  # fmt: skip
    assert [embed for *_, embed in spans][3] is True
    with_bom = "﻿---\na: 1\n---\n[[X]]\n"
    [(start, end, inside, _)] = wikilink_spans(with_bom)
    assert with_bom[start:end] == "[[X]]" and inside == "X"


def test_a_rename_changes_every_link_to_the_note_and_keeps_versions(tmp_path: Path) -> None:
    root = vault(tmp_path / "Starlight")
    index = Index(tmp_path / "index.sqlite", root)
    history = History(tmp_path / "history.sqlite")
    history.listeners.append(index.touch)
    planned = plan(index, root / "Notes" / "Old.md", root / "Notes" / "New.md")
    assert sorted(p.name for p in planned.texts) == ["A.md", "B.md", "Old.md"]
    assert sorted(p.relative_to(root).as_posix() for p in planned.others()) == [
        "Notes/A.md",
        "Projects/B.md",
    ]
    assert not (root / "Notes" / "New.md").exists()  # (a plan changes nothing)
    changed = rename(index, history, planned)
    assert len(changed) == 2 and not (root / "Notes" / "Old.md").exists()
    assert (root / "Notes" / "New.md").read_text("utf-8").endswith("See [[New#Old]] (itself).\n")
    a = (root / "Notes" / "A.md").read_text("utf-8")
    assert a.startswith("[[New]], [[New|shown]], [[New#Part]], ![[New]] and [[Notes/New]].\n")
    assert "By alias: [[Other name]]." in a  # the alias goes along with the note
    assert "`[[Old]]`" in a and "```\n[[Old]]\n```" in a  # code stays as it was
    assert "[[New\\|in a table]]" in a
    b = (root / "Projects" / "B.md").read_text("utf-8")
    assert b == "[[Old]] here means Projects/Old. [[Notes/New]] means the other.\n"
    assert (root / "Notes" / "C.md").read_text("utf-8") == NOTES["Notes/C.md"]
    for note in ("Notes/A.md", "Projects/B.md"):  # each changed note kept its earlier version
        [kept] = history.versions(root / note)
        assert history.text(kept.id) == NOTES[note]
    assert {b["from"] for b in index.backlinks("Notes/New.md")} == {"Notes/A.md", "Projects/B.md"}
    with pytest.raises(HistoryError, match="already a note"):
        plan(index, root / "Notes" / "A.md", root / "Notes" / "C.md")


@pytest.fixture
def linked(paths: Paths, use_module: Callable[[str], Path], documents: Path) -> Path:
    use_module("notes")
    vault(documents / "Starlight")
    return documents / "Starlight"


async def test_the_assistant_asks_before_a_rename_changes_other_notes(
    paths: Paths, linked: Path
) -> None:
    user = User(False, True)
    async with Kernel(paths, ui=user) as k:
        refused = await fails(k, "notes.rename", title="Old", new_title="New")
        assert "not confirmed" in refused and (linked / "Notes" / "Old.md").exists()
        assert "change the links to it in 2 other notes (A, B)" in user.questions[-1]
        done = await call(k, "notes.rename", title="Old", new_title="New")
        assert done["to"] == "New" and sorted(done["links_changed_in"]) == ["A", "B"]
        asked = len(user.questions)
        await call(k, "notes.rename", title="C", new_title="D")  # nothing links to it
        assert len(user.questions) == asked
    assert "[[New]]" in (linked / "Notes" / "A.md").read_text("utf-8")


async def test_links_backlinks_read_and_finding_by_alias(paths: Paths, linked: Path) -> None:
    (linked / "Notes" / "C.md").write_text("A link to [[Nowhere]] and [[Old]].\n", encoding="utf-8")
    async with Kernel(paths) as k:
        links = await call(k, "notes.links", title="C")
        assert links["links"] == [
            {"note": "Nowhere", "exists": False},
            {"note": "Old", "exists": True},
        ]
        back = await call(k, "notes.backlinks", title="Old")
        assert {b["note"] for b in back["linked_from"]} == {"A", "B", "C"}  # B: [[Notes/Old]]
        line = next(b["line"] for b in back["linked_from"] if b["note"] == "C")
        assert line == "A link to [[Nowhere]] and [[Old]]."
        read = await call(k, "notes.read", title="Old")
        assert read["linked_from"] == ["A", "B", "C"] and read["links"] == ["Old"]
        by_alias = await call(k, "notes.read", title="other name")
        assert by_alias["title"] == "Old"
