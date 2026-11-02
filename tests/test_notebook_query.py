"""Block E3 of the notes plan (docs/plan-notes-app.md): a small query language over the notes'
properties, tags, folders and dates. Read into a tree, turned into parameterised SQL (never
run as code), with clear errors, missing properties handled, and a time limit."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.notebook.index import Index
from tests.test_home_devices import call, fails

TODAY = dt.date(2026, 11, 2)
NOTES = {
    "Notes/Alpha.md": "---\nstatus: open\npriority: high\ndue: 2026-11-20\nrating: 4\n"
    "tags: [work]\n---\nalpha\n",
    "Notes/Beta.md": "---\nstatus: done\ndue: 2026-11-01T09:30\nrating: 2\nreviewed: true\n"
    "---\nbeta #work/projects\n",
    "Notes/Gamma.md": "---\nstatus: in progress\nrating: 4.5\ntags: [home, garden]\n"
    "owner: Anna\n---\ngamma " + "word " * 300 + "\n",
    "Journal/2026-11-01.md": "A day without properties.\n",
    "Clippings/Page.md": "---\nsource: https://x.example/\nstatus: open\n---\nFrom the web.\n",
}


def notebook(root: Path) -> Path:
    for relative, text in NOTES.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


@pytest.fixture
def index(tmp_path: Path) -> Index:
    found = Index(tmp_path / "index.sqlite", notebook(tmp_path / "Starlight"))
    found.refresh()
    return found


def test_the_grammar() -> None:
    from starlight.notebook.query import AllOf, AnyOf, Cond, Not, Value, parse

    tree = parse("status is open and tag is work or not (rating < 3)", TODAY)
    assert tree == AnyOf(
        (
            AllOf(
                (
                    Cond("status", "is", Value("open", "text")),
                    Cond("tag", "is", Value("work", "text")),
                )
            ),
            Not(Cond("rating", "<", Value("3", "number", number=3.0))),
        )
    )  # fmt: skip  ("and" binds closer than "or")
    assert parse("status is in progress", TODAY) == Cond(
        "status", "is", Value("in progress", "text")
    )
    assert parse('"due date" != "2026-12-01"', TODAY) == Cond(
        "due date", "is not", Value("2026-12-01", "text")
    )
    assert parse("due > today-7", TODAY) == Cond(
        "due", ">", Value("today-7", "date", day="2026-10-26")
    )
    assert parse("due <= morgen", TODAY).value.day == "2026-11-03"  # type: ignore[union-attr]
    assert parse("done = ja", TODAY).value.yes is True  # type: ignore[union-attr]
    assert parse("rating >= 2,5", TODAY).value.number == 2.5  # type: ignore[union-attr]
    assert parse("Status IST NICHT leer UND nicht tag hat garten", TODAY) == AllOf((
        Cond("Status", "not empty"),
        Not(Cond("tag", "has", Value("garten", "text"))),
    ))  # fmt: skip


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("", "the query is empty"),
        ("status is", "expected a value after 'is'"),
        ("status open", "after 'status' comes is, is not"),
        ("(status is open", "a bracket is not closed"),
        ("status is open)", "did not expect ')'"),
        ("status is open and", "expected a field"),
        ("and status is open", "expected a field"),
        ("due is 2026-02-30", "not a date in the calendar"),
        ("x" * 3000, "too long"),
        ("(" * 40 + "a is b" + ")" * 40, "too many brackets"),
        ("status is open; DROP", None),  # (just a value with a semicolon: fine)
    ],
)
def test_wrong_queries_get_plain_answers(text: str, says: str | None) -> None:
    from starlight.notebook.query import QueryError, parse

    if says is None:
        parse(text, TODAY)
        return
    with pytest.raises(QueryError, match=re.escape(says)):
        parse(text, TODAY)


def names(index: Index, where: str, sort: str = "") -> list[str]:
    from starlight.notebook.query import run

    return [n["name"] for n in run(index, where, sort, today=TODAY)["notes"]]


def test_finding_notes(index: Index) -> None:
    assert names(index, "status is open") == ["Alpha", "Page"]
    assert names(index, "status is OPEN and not folder is clippings") == ["Alpha"]
    assert names(index, "status is not done") == ["2026-11-01", "Alpha", "Gamma", "Page"]
    assert names(index, "tag is work") == ["Alpha", "Beta"]  # (#work/projects is below work)
    assert names(index, "tag is work/projects") == ["Beta"]
    assert names(index, "tags has garden") == ["Gamma"]  # (the property: a list holds it)
    assert names(index, "due < 2026-11-15") == ["Beta"]  # (a date with a time counts by day)
    assert names(index, "due > today") == ["Alpha"]
    assert names(index, "due is 2026-11-01") == ["Beta"]
    assert names(index, "rating >= 4") == ["Alpha", "Gamma"]
    assert names(index, "rating = 4.5") == ["Gamma"]
    assert names(index, "reviewed is yes") == ["Beta"] and names(index, "reviewed is no") == []
    assert names(index, "status is in progress") == ["Gamma"]
    assert names(index, "owner has ann") == ["Gamma"]
    assert names(index, "due is empty") == ["2026-11-01", "Gamma", "Page"]  # missing counts
    assert names(index, "due is not empty") == ["Alpha", "Beta"]
    assert names(index, "nothing is here") == []  # a property no note has
    assert names(index, "folder is notes and words > 200") == ["Gamma"]
    assert names(index, "name has amm") == ["Gamma"] and names(index, "path has journal") == [
        "2026-11-01"
    ]
    assert names(index, "modified > today-30") == names(index, "")  # (all were just written)
    assert names(index, "Status IST offen ODER rating > 4") == ["Gamma"]


def test_sorting_and_fields(index: Index) -> None:
    from starlight.notebook.query import run

    assert names(index, "", "rating desc") == ["Gamma", "Alpha", "Beta", "2026-11-01", "Page"]
    assert names(index, "rating > 0", "due") == ["Beta", "Alpha", "Gamma"]  # (no due: last)
    table = run(index, "tag is work", "name", "status, due, tag, folder, words", today=TODAY)
    assert table["fields"] == ["status", "due", "tag", "folder", "words"]
    assert table["notes"][0] == {
        "name": "Alpha", "path": "Notes/Alpha.md", "status": "open", "due": "2026-11-20",
        "tag": ["work"], "folder": "Notes", "words": 1,
    }  # fmt: skip
    assert table["notes"][1]["tag"] == ["work/projects"]
    assert run(index, "status is done", "", "missing")["notes"][0]["missing"] is None


@pytest.mark.parametrize(
    "where",
    [
        'status is "x\'); DROP TABLE files; --"',
        '"status\') OR 1=1 --" is open',
        "status is %",
        "status has _",
        "name is \"' OR ''='\"",
    ],
)
def test_sql_in_a_query_stays_data(index: Index, where: str) -> None:
    from starlight.notebook.query import run

    assert run(index, where, today=TODAY)["notes"] == []
    assert len(index.notes()) == 5  # (every table is still there)


@pytest.mark.parametrize(
    ("where", "sort", "says"),
    [
        ("tag > 3", "", "tags have no order"),
        ("rating > yes", "", "yes and no have no order"),
        ("words > many", "", "words is a number"),
        ("modified > soon", "", "modified is a date"),
        ("", "tag", "cannot be sorted by tag"),
    ],
)
def test_wrong_comparisons_get_plain_answers(
    index: Index, where: str, sort: str, says: str
) -> None:
    from starlight.notebook.query import QueryError, run

    with pytest.raises(QueryError, match=says):
        run(index, where, sort, today=TODAY)


def test_a_large_folder_and_the_time_limit(tmp_path: Path) -> None:
    from starlight.notebook.query import QueryError, run

    root = tmp_path / "Starlight" / "Notes"
    root.mkdir(parents=True)
    for n in range(2500):
        status = ("open", "done", "waiting")[n % 3]
        (root / f"Note {n:04d}.md").write_text(
            f"---\nstatus: {status}\nrating: {n % 10}\n---\nText {n}\n", encoding="utf-8"
        )
    index = Index(tmp_path / "index.sqlite", tmp_path / "Starlight")
    index.refresh()
    found = run(index, "status is open and rating >= 5", "rating desc", limit=1000)
    assert found["count"] == 417 and found["notes"][0]["name"] == "Note 0009"
    with pytest.raises(QueryError, match="took longer than 0 seconds"):
        run(index, "status is open or rating > 3", "rating", seconds=0)


async def test_notes_query_for_the_assistant(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    use_module("notes")
    notebook(documents / "Starlight")
    async with Kernel(paths) as k:
        table: Any = await call(
            k, "notes.query", where="status is open", sort="name", fields="status, due"
        )
        assert [n["name"] for n in table["notes"]] == ["Alpha", "Page"]
        assert table["untrusted"] is True  # (the clipping's title came from the web)
        mine = await call(k, "notes.query", where="tag is work", fields="status")
        assert "untrusted" not in mine and mine["count"] == 2
        assert "after 'status' comes is" in await fails(k, "notes.query", where="status open")
