"""Block C2 of the notes plan (docs/plan-notes-app.md): tags and properties sort notes. A
change to one property touches only its lines; tags in code do not count; nested tags count
for their parents; the assistant has tools for both; memories can be found by tag."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.notebook.index import Index
from starlight.notebook.markdown import add_tag, remove_tag
from tests.test_home_devices import call, fails
from tests.test_remote import LOCAL, client, home

NOTE = "---\ntitle: Groceries\n# keep this comment\ntags: [home]\n---\n# List\nmilk #errands\n"


def test_tags_are_added_and_removed_in_the_property_only() -> None:
    added = add_tag(NOTE, "#work/projects")
    assert added == NOTE.replace("tags: [home]\n", "tags:\n- home\n- work/projects\n")
    assert add_tag(added, "WORK/projects") == added  # there already (without case)
    back, removed = remove_tag(added, "work/projects")
    assert removed and back == NOTE.replace("tags: [home]\n", "tags:\n- home\n")
    none, removed = remove_tag(NOTE, "home")
    assert (
        removed
        and "tags" not in none
        and "# keep this comment" in none
        and none.endswith("milk #errands\n")
    )
    same, removed = remove_tag(NOTE, "errands")  # a tag in the text stays: it is written there
    assert not removed and same == NOTE
    assert add_tag("Plain text\n", "idea") == "---\ntags:\n- idea\n---\nPlain text\n"
    assert add_tag("---\ntag: one\n---\n", "two") == "---\ntag:\n- one\n- two\n---\n"
    for bad in ("123", "two words", "", "#"):
        with pytest.raises(ValueError):
            add_tag(NOTE, bad)


def test_nested_tags_count_for_their_parents_and_code_does_not_count(tmp_path: Path) -> None:
    root = tmp_path / "Starlight"
    (root / "Notes").mkdir(parents=True)
    texts = {
        "A.md": "#work/projects/alpha and #home\n",
        "B.md": "---\ntags: [work, Status]\nstatus: open\n---\n#work/projects\n",
        "C.md": "`#nottag` and\n```\n#nottag2\n```\n#work\n",
    }
    for name, text in texts.items():
        (root / "Notes" / name).write_text(text, encoding="utf-8")
    index = Index(tmp_path / "index.sqlite", root)
    index.refresh()
    tree = {entry["tag"]: (entry["count"], entry["depth"]) for entry in index.tag_tree()}
    assert tree == {
        "home": (1, 0),
        "Status": (1, 0),
        "work": (3, 0),
        "work/projects": (2, 1),
        "work/projects/alpha": (1, 2),
    }
    assert dict(index.property_names()) == {"status": 1, "tags": 1}


@pytest.fixture
def tagged(paths: Paths, use_module: Callable[[str], Path], documents: Path) -> Path:
    use_module("notes")
    folder = documents / "Starlight" / "Notes"
    folder.mkdir(parents=True)
    (folder / "Groceries.md").write_text(NOTE, encoding="utf-8")
    (folder / "Work.md").write_text("Plans for #work/projects.\n", encoding="utf-8")
    return folder


async def test_the_assistants_tag_and_property_tools(paths: Paths, tagged: Path) -> None:
    note = tagged / "Groceries.md"
    async with Kernel(paths) as k:
        tags = await call(k, "notes.tags")
        assert {t["tag"]: t["count"] for t in tags["tags"]} == {
            "errands": 1, "home": 1, "work": 1, "work/projects": 1,
        }  # fmt: skip
        added = await call(k, "notes.tag", title="Groceries", tag="#work")
        assert added["changed"]["version"]  # the note as it was is kept
        assert (await call(k, "notes.tag", title="Groceries", tag="work"))["already"] is True
        found = await call(k, "notes.by_tag", tag="work")
        assert [n["title"] for n in found["notes"]] == ["Groceries", "Work"]
        await call(k, "notes.untag", title="Groceries", tag="work")
        assert "tags: [home]" not in note.read_text("utf-8")  # (rewritten as a list)
        assert "errands" in await fails(k, "notes.untag", title="Groceries", tag="errands")

        await call(k, "notes.set_property", title="Groceries", name="rating", value="4")
        await call(k, "notes.set_property", title="Groceries", name="due", value="2026-11-05")
        await call(k, "notes.set_property", title="Groceries", name="done", value="no")
        await call(k, "notes.set_property", title="Groceries", name="shops", value="Rewe, Aldi")
        props = (await call(k, "notes.properties", title="Groceries"))["properties"]
        assert props["rating"] == 4 and props["due"] == "2026-11-05" and props["done"] is False
        assert props["shops"] == ["Rewe", "Aldi"] and props["title"] == "Groceries"
        await call(k, "notes.set_property", title="Groceries", name="rating", value="")
        assert "rating" not in (await call(k, "notes.properties", title="Groceries"))["properties"]
    text = note.read_text("utf-8")
    assert "# keep this comment\n" in text and text.endswith("---\n# List\nmilk #errands\n")
    assert "due: 2026-11-05\n" in text  # a date, written as one


async def test_memories_can_be_found_by_tag(paths: Paths) -> None:
    async with Kernel(paths) as k:
        memory = k.memory
        await memory.remember("I like jazz.", "Music", tags=["hobby/music"])
        await memory.remember("I run on Sundays.", "Running", tags=["hobby"])
        await memory.remember("My car is blue.", "Car")
        listed: Any = await call(k, "memory.recall", tag="hobby")
        assert {m["text"] for m in listed} == {"Music: I like jazz.", "Running: I run on Sundays."}
        assert all("tags" in m for m in listed)
        music: Any = await call(k, "memory.recall", tag="#hobby/music")
        assert [m["text"] for m in music] == ["Music: I like jazz."]
        assert "what should I look for" in await fails(k, "memory.recall")


def test_the_property_endpoint_changes_one_property_with_the_stamp(
    tmp_path: Path, documents: Path
) -> None:
    folder = documents / "Starlight" / "Notes"
    folder.mkdir(parents=True)
    note = folder / "Groceries.md"
    note.write_text(NOTE, encoding="utf-8")
    with client(home(tmp_path / "h")) as c:
        read = c.get("/api/note", params={"path": "Notes/Groceries.md"}, headers=LOCAL).json()

        def post(name: str, value: Any, stamp: str) -> Any:
            body = {"path": "Notes/Groceries.md", "name": name, "value": value, "stamp": stamp}
            return c.post("/api/note/property", json=body, headers=LOCAL)

        done = post("done", True, read["stamp"])
        assert done.status_code == 200 and done.json()["properties"]["done"] is True
        assert note.read_text("utf-8") == NOTE.replace(
            "tags: [home]\n", "tags: [home]\ndone: true\n"
        )
        assert post("rating", 5, read["stamp"]).status_code == 409  # an old stamp: refused
        stamp = done.json()["stamp"]
        removed = post("title", None, stamp)
        assert removed.status_code == 200 and "title" not in removed.json()["properties"]
        assert post("x", {"no": "objects"}, removed.json()["stamp"]).status_code == 400
        facets = c.get("/api/notes/facets", headers=LOCAL).json()
        assert {t["tag"] for t in facets["tags"]} == {"home", "errands"}
        assert {p["name"] for p in facets["properties"]} == {"tags", "done"}
    assert note.read_text("utf-8").endswith("---\n# List\nmilk #errands\n")
