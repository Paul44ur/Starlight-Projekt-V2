"""Block F1 of the notes plan (docs/plan-notes-app.md): graph data from the notebook index, the
d3-force bundle (graph.js, built in bundles/graph), and the small graph next to each note."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from starlight.notebook.index import Index
from tests.conftest import REPO
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL

VENDOR = REPO / "starlight" / "web" / "static" / "vendor"
BUILD = REPO / "bundles" / "graph"
WEB = {
    "Notes/A.md": "[[B]] and [[B]] again, [[C]], ![[pic.png]], [[Missing]], [[A]] itself",
    "Notes/B.md": "Back to [[A]], on to [[D]]",
    "Notes/C.md": "Nothing here.",
    "Notes/D.md": "See [[E]].",
    "Notes/E.md": "The end.",
    "Notes/Alone.md": "No links.",
    "Notes/pic.png": "",
}


def test_the_shipped_graph_bundle_is_the_one_the_build_made() -> None:
    data = (VENDOR / "graph.js").read_bytes()
    assert (VENDOR / "graph.sha256").read_text("utf-8").split() == [
        hashlib.sha256(data).hexdigest(), "graph.js",
    ]  # fmt: skip
    assert b"\r\n" not in data and 5_000 < len(data) < 60_000
    package = json.loads((BUILD / "package.json").read_text("utf-8"))
    pinned = {**package["dependencies"], **package["devDependencies"]}
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in pinned.values()), pinned
    lock = json.loads((BUILD / "package-lock.json").read_text("utf-8"))["packages"]
    licences = (VENDOR / "graph-LICENSES.txt").read_text("utf-8")
    listed = re.findall(r"^(\S+) (\d+\.\d+\.\d+) \((\S+)\)$", licences, re.MULTILINE)
    assert {name for name, _, _ in listed} == set(package["dependencies"])
    for name, version, kind in listed:
        assert lock[f"node_modules/{name}"]["version"] == version == pinned[name], name
        assert kind == "ISC", name
    assert licences.count("Permission to use, copy, modify, and/or distribute") == len(listed)


def test_the_web_app_serves_the_graph_bundle(app: Any) -> None:
    served = app.get("/vendor/graph.js", headers=LOCAL)
    assert served.status_code == 200 and served.headers["content-type"].startswith(
        "text/javascript"
    )
    assert hashlib.sha256(served.content).hexdigest() in (VENDOR / "graph.sha256").read_text()
    assert "ISC" in app.get("/vendor/graph-LICENSES.txt", headers=LOCAL).text
    assert app.get("/vendor/graph.sha256", headers=LOCAL).status_code == 404  # (not listed)


def web(tmp_path: Path) -> Index:
    root = tmp_path / "Starlight"
    for relative, text in WEB.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    index = Index(tmp_path / "index.sqlite", root)
    index.refresh()
    return index


def test_graph_data(tmp_path: Path) -> None:
    from starlight.notebook.graph import graph

    index = web(tmp_path)
    whole = graph(index)
    assert [(n["title"], n["links"]) for n in whole["nodes"]] == [
        ("A", 2),
        ("Alone", 0),
        ("B", 2),
        ("C", 1),
        ("D", 2),
        ("E", 1),
    ]  # fmt: skip  (each link once; no pictures, missing notes or links to itself)
    assert [(e["source"], e["target"]) for e in whole["links"]] == [
        ("Notes/A.md", "Notes/B.md"), ("Notes/A.md", "Notes/C.md"),
        ("Notes/B.md", "Notes/D.md"), ("Notes/D.md", "Notes/E.md"),
    ]  # fmt: skip
    near = graph(index, "Notes/A.md")
    assert [(n["title"], n["depth"]) for n in near["nodes"]] == [("A", 0), ("B", 1), ("C", 1)]
    assert len(near["links"]) == 2
    two = graph(index, "Notes/A.md", depth=2)
    assert [n["title"] for n in two["nodes"]] == ["A", "B", "C", "D"]
    capped = graph(index, limit=2)
    assert capped["capped"] and capped["total"] == 6
    assert [n["title"] for n in capped["nodes"]] == ["A", "B"]  # (the most links first)
    assert capped["links"] == [{"source": "Notes/A.md", "target": "Notes/B.md"}]
    assert graph(index, "Notes/Nothing.md")["nodes"] == []


def test_the_graph_api(app: Any, notebook: Path) -> None:
    around = app.get("/api/notes/graph", params={"around": "Notes/Shopping.md"}, headers=LOCAL)
    data = around.json()
    assert [n["id"] for n in data["nodes"]] == ["Notes/Plan.md", "Notes/Shopping.md"]
    assert data["links"] == [{"source": "Notes/Plan.md", "target": "Notes/Shopping.md"}]
    whole = app.get("/api/notes/graph", headers=LOCAL).json()
    assert whole["total"] == 5 and not whole["capped"]
    bad = app.get("/api/notes/graph", params={"around": "../secret.md"}, headers=LOCAL)
    assert bad.status_code == 400


@pytest.mark.parametrize(("scheme", "locale"), [("light", "en-US"), ("dark", "de-DE")])
async def test_the_small_graph_next_to_a_note(
    static_site: str,
    app: Any,
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    locale: str,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    (notebook / "Notes" / "Garden.md").write_text("See [[Shopping]] and [[Plan]].\n", "utf-8")
    async with notes_page(static_site, app, locale=locale, scheme=scheme) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FShopping.md")
        await page.wait_for_selector("#mini-graph svg .node")
        nodes = await page.locator("#mini-graph .node").evaluate_all(
            "ns => ns.map(n => [n.dataset.path, n.classList.contains('here'),"
            " n.querySelector('text').textContent])"
        )
        assert sorted(nodes) == [
            ["Notes/Garden.md", False, "Garden"],
            ["Notes/Plan.md", False, "Plan"],
            ["Notes/Shopping.md", True, "Shopping"],
        ]
        assert await page.locator("#mini-graph .edge").count() == 3
        boxes = await page.locator("#mini-graph circle").evaluate_all(
            "cs => cs.map(c => [+c.getAttribute('cx'), +c.getAttribute('cy')])"
        )
        assert all(0 <= x <= 240 and 0 <= y <= 180 for x, y in boxes)  # (inside the picture)
        await page.locator('#mini-graph .node[data-path="Notes/Plan.md"]').click()
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Plan'"
        )
        await opened(page, f"{static_site}/notes.html?path=Notes%2FOld%20ideas.md")
        await page.wait_for_selector("#mini-graph .none")
        assert await page.locator("#mini-graph .none").text_content() == (
            "Noch keine Links" if locale == "de-DE" else "No links yet"
        )
        assert page.errors == []  # type: ignore[attr-defined]
