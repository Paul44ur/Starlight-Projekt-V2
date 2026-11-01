"""Block B2 of the notes plan (docs/plan-notes-app.md): the notebook index
(starlight/notebook/index.py) and how Starlight keeps it up to date."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from starlight.core import Kernel, Paths
from starlight.notebook import History, write_note
from starlight.notebook.index import Index, has_fts5
from tests.conftest import REPO
from tests.test_home_devices import call

NOTES = {
    "Notes/Shopping.md": (
        "---\naliases: [Einkauf]\ntags: [home]\n---\n# Shopping\n"
        "- [ ] Buy milk due: 2026-11-05 #errands\n- [x] Buy bread\nSee [[Plan]] and [[Pantry]].\n"
    ),
    "Notes/Plan.md": "---\ntitle: The big plan\nstatus: open\n---\nWork on #work/projects.\n",
    "Projects/Plan.md": "A second plan. Linked from [[Shopping]] and [[Einkauf|my list]].\n",
    "Journal/2026-11-01.md": "Äpfel gekauft. Müller angerufen. #work\nback to [[Notes/Plan]]\n",
    "code/script.md": "# not a note #nottag\n",
    ".hidden/secret.md": "hidden #nottag\n",
    "Notes/picture.png": "not markdown",
}


def vault(root: Path, notes: dict[str, str] = NOTES) -> Path:
    for relative, text in notes.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


@pytest.fixture(params=[True, False], ids=["fts5", "scan"])
def index(request: pytest.FixtureRequest, tmp_path: Path) -> Index:
    if request.param and not has_fts5():
        pytest.skip("this Python's SQLite has no FTS5")
    built = Index(
        tmp_path / "data" / "index.sqlite", vault(tmp_path / "Starlight"), fts=request.param
    )
    built.refresh()
    return built


def test_every_note_is_indexed_but_code_and_hidden_folders_are_not(index: Index) -> None:
    paths = [n.path for n in index.notes()]
    assert paths == [
        "Journal/2026-11-01.md",
        "Notes/Plan.md",
        "Notes/Shopping.md",
        "Projects/Plan.md",
    ]
    plan = index.note("Notes/Plan.md")
    assert plan is not None and plan.title == "The big plan" and plan.name == "Plan"
    assert index.note("Notes/Shopping.md").aliases == ["Einkauf"]  # type: ignore[union-attr]
    assert [n.path for n in index.notes("Notes")] == ["Notes/Plan.md", "Notes/Shopping.md"]
    assert index.properties("Notes/Plan.md") == {"title": "The big plan", "status": "open"}
    assert index.mode == ("fts5" if index.fts else "scan")


def test_tags_tasks_and_search(index: Index) -> None:
    assert dict(index.tags()) == {"work": 1, "home": 1, "errands": 1, "work/projects": 1}
    assert index.with_tag("work") == ["Journal/2026-11-01.md", "Notes/Plan.md"]  # nested too
    assert index.with_tag("#WORK/projects") == ["Notes/Plan.md"]
    assert [t["text"] for t in index.tasks()] == ["Buy milk #errands", "Buy bread"]
    [milk] = index.tasks(open_only=True)
    assert milk["due"] == "2026-11-05" and milk["path"] == "Notes/Shopping.md"

    hits = index.search("milk")
    assert [h.path for h in hits] == ["Notes/Shopping.md"] and "milk" in hits[0].snippet
    assert [h.path for h in index.search("plan second")] == ["Projects/Plan.md"]  # all words
    assert [h.path for h in index.search("äpfel")] == ["Journal/2026-11-01.md"]  # no case
    assert [h.path for h in index.search("Müll")] == ["Journal/2026-11-01.md"]  # a word start
    assert {h.path for h in index.search("plan")} >= {"Notes/Plan.md", "Projects/Plan.md"}
    assert index.search("nottag") == [] and index.search("  ...  ") == []
    assert index.search('"; DROP TABLE files; --') == []  # words only, never SQL


def test_links_and_backlinks(index: Index) -> None:
    links = {link["target"]: link["to"] for link in index.links("Notes/Shopping.md")}
    assert links == {"Plan": "Notes/Plan.md", "Pantry": None}  # same folder first; not yet made
    assert index.resolve("Plan", "Projects/Other.md") == "Projects/Plan.md"
    back = index.backlinks("Notes/Shopping.md")
    assert [(b["from"], b["target"]) for b in back] == [
        ("Projects/Plan.md", "Shopping"),
        ("Projects/Plan.md", "Einkauf"),  # by its alias
    ]
    assert [b["from"] for b in index.backlinks("Notes/Plan.md")] == [
        "Journal/2026-11-01.md",
        "Notes/Shopping.md",
    ]
    assert index.backlinks("Projects/Plan.md") == []  # "[[Plan]]" from Notes means Notes/Plan


def test_refresh_reads_again_only_what_changed(tmp_path: Path) -> None:
    root = vault(tmp_path / "Starlight")
    index = Index(tmp_path / "index.sqlite", root, refresh_every=60)
    first = index.refresh()
    assert (first.added, first.total, first.capped) == (4, 4, False)
    assert index.refresh().skipped  # looked a moment ago
    plan = root / "Notes" / "Plan.md"
    plan.write_text("Now about #gardening\n", encoding="utf-8")
    os.utime(plan, ns=(time.time_ns(), time.time_ns() + 5_000_000))
    (root / "Projects" / "Plan.md").unlink()
    (root / "Notes" / "New.md").write_text("fresh", encoding="utf-8")
    again = index.refresh(force=True)
    assert (again.added, again.changed, again.removed) == (1, 1, 1)
    assert (
        index.with_tag("gardening") == ["Notes/Plan.md"] and index.with_tag("work/projects") == []
    )
    assert index.note("Projects/Plan.md") is None
    assert index.refresh(force=True) == type(again)(total=4)  # nothing changed: nothing read


def test_starlights_own_writes_update_the_index_at_once(tmp_path: Path) -> None:
    root = vault(tmp_path / "Starlight")
    index = Index(tmp_path / "index.sqlite", root, refresh_every=3600)
    index.refresh()
    history = History(tmp_path / "history.sqlite")
    history.listeners.append(index.touch)
    note = root / "Notes" / "Shopping.md"
    write_note(note, "Only #cheese now\n", "test", history)
    assert index.with_tag("cheese") == ["Notes/Shopping.md"]  # no refresh needed
    write_note(root / "Notes" / "Made.md", "brand new #idea", "test", history)
    assert index.with_tag("idea") == ["Notes/Made.md"]
    history.trash(note)
    assert index.note("Notes/Shopping.md") is None
    history.restore_deleted(note)
    assert index.with_tag("cheese") == ["Notes/Shopping.md"]
    index.touch(tmp_path / "elsewhere.md")  # outside the notebook: ignored
    index.touch(root / "code" / "x.md")


def test_caps_on_files_and_note_size(tmp_path: Path) -> None:
    root = vault(tmp_path / "Starlight")
    (root / "Notes" / "Big.md").write_text("#big " + "x" * 3000, encoding="utf-8")
    index = Index(tmp_path / "index.sqlite", root, max_files=3, max_note_bytes=2000)
    done = index.refresh()
    assert done.capped and done.total == 3 and len(index.notes()) == 3
    roomy = Index(tmp_path / "other.sqlite", root, max_note_bytes=2000)
    roomy.refresh()
    big = roomy.note("Notes/Big.md")
    assert big is not None and big.too_big and roomy.with_tag("big") == []  # listed, not read


def test_the_index_is_a_cache_that_rebuilds_itself(tmp_path: Path) -> None:
    root = vault(tmp_path / "Starlight")
    db = tmp_path / "index.sqlite"
    if has_fts5():
        Index(db, root, fts=True).refresh()
    scan = Index(db, root, fts=False)  # FTS5 gone (another Python): built again for a scan
    assert scan.notes() == [] and scan.refresh().added == 4
    assert [h.path for h in scan.search("milk")] == ["Notes/Shopping.md"]
    db.write_bytes(b"this is not a database at all" * 100)  # broken: thrown away, rebuilt
    broken = Index(db, root, fts=False)
    assert broken.refresh().added == 4


def test_a_big_folder_is_indexed_quickly(tmp_path: Path) -> None:
    root = tmp_path / "Starlight"
    words = " ".join(f"word{i}" for i in range(150))
    notes = {
        f"Notes/{i // 100}/Note {i}.md": f"# Note {i}\n{words} #t{i % 7} [[Note {i + 1}]]\n"
        for i in range(2000)
    }
    vault(root, notes)
    index = Index(tmp_path / "index.sqlite", root)
    started = time.monotonic()
    assert index.refresh().added == 2000
    first = time.monotonic() - started
    started = time.monotonic()
    assert index.refresh(force=True).changed == 0
    again = time.monotonic() - started
    assert first < 30 and again < 3, (first, again)
    assert len(index.backlinks("Notes/0/Note 5.md")) == 1


def test_memory_reads_its_properties_through_the_notebook_parser(paths: Paths) -> None:
    from starlight.memory.store import MemoryStore

    source = (REPO / "starlight" / "memory" / "store.py").read_text("utf-8")
    assert "from starlight.notebook.markdown import split" in source and "FRONT" not in source
    store = MemoryStore(paths.memory)
    paths.memory.mkdir(parents=True, exist_ok=True)
    (paths.memory / "car.md").write_text(
        "---\ntopic: Car\ndate: 2026-10-06\ntags: [car]\n---\nA blue Golf.\n", encoding="utf-8"
    )
    (paths.memory / "odd.md").write_text("---\ntopic: [broken\n---\nKeep me.\n", encoding="utf-8")
    car = store.get("car")
    assert car is not None and (car.topic, car.date, car.tags) == ("Car", "2026-10-06", ["car"])
    odd = store.get("odd")
    assert odd is not None and "Keep me." in odd.content and "topic: [broken" in odd.content


async def test_the_kernel_keeps_the_notebook_index(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    use_module("notes")
    async with Kernel(paths) as k:
        index = k.notebook_index
        assert index is k.notebook_index and index.root == documents / "Starlight"
        index.refresh()
        await call(k, "notes.note", title="Ideas", text="A garden #plans")
        assert index.with_tag("plans") == ["Notes/Ideas.md"]  # told at once, no refresh
        await call(k, "notes.append", title="Ideas", text="and #trees")
        assert index.with_tag("trees") == ["Notes/Ideas.md"]
