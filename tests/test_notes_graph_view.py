"""Block F2 of the notes plan (docs/plan-notes-app.md): the whole graph on the Notes page:
filters by folder and tag, memories as points if wanted, caps, SVG up to 300 points and a
canvas beyond, zooming, and a click on a point opening its note."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_graph import web
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL


def test_filters_memories_and_caps(tmp_path: Path) -> None:
    from starlight.notebook.graph import graph

    index = web(tmp_path)
    (Path(index.root) / "Journal").mkdir()
    (Path(index.root) / "Journal" / "Day.md").write_text("Met [[A]]. #work\n", "utf-8")
    (Path(index.root) / "Notes" / "C.md").write_text("Tagged #work/projects\n", "utf-8")
    index.refresh(force=True)
    journal = graph(index, folder="Journal")
    assert [n["id"] for n in journal["nodes"]] == ["Journal/Day.md"]
    assert journal["links"] == []  # (A is not in the Journal folder)
    work = graph(index, tag="work")
    assert [n["id"] for n in work["nodes"]] == ["Journal/Day.md", "Notes/C.md"]
    memories = [
        ("people/anna", "Anna", "Anna helps with [[A]] and knows [[Car]]."),
        ("car", "Car", "The car is due for service."),
    ]
    remembered = graph(index, memories=memories)
    points = {n["id"]: n for n in remembered["nodes"]}
    assert points["memory:people/anna"]["folder"] == "Memories"
    assert points["memory:people/anna"]["title"] == "Anna"
    pairs = {(e["source"], e["target"]) for e in remembered["links"]}
    assert ("Notes/A.md", "memory:people/anna") in pairs
    assert ("memory:car", "memory:people/anna") in pairs  # (a memory names another)
    capped = graph(index, folder="Notes", limit=3)
    assert capped["capped"] and len(capped["nodes"]) == 3 and capped["total"] == 6


def test_the_api_filters_and_adds_memories(app: Any, notebook: Path, tmp_path: Path) -> None:
    memory = tmp_path / "h" / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    (memory / "groceries.md").write_text(
        "---\ntopic: Groceries\n---\nThe list is in [[Shopping]].\n", "utf-8"
    )
    plain = app.get("/api/notes/graph", params={"folder": "Notes"}, headers=LOCAL).json()
    assert {n["folder"] for n in plain["nodes"]} == {"Notes"}
    home = app.get("/api/notes/graph", params={"tag": "home"}, headers=LOCAL).json()
    assert {n["id"] for n in home["nodes"]} == {"Notes/Shopping.md", "Notes/Old ideas.md"}
    query = {"memories": "true"}
    with_memories = app.get("/api/notes/graph", params=query, headers=LOCAL).json()
    assert {"source": "Notes/Shopping.md", "target": "memory:groceries"} in with_memories["links"]


def many_notes(notebook: Path, count: int) -> None:
    folder = notebook / "Notes" / "Many"
    folder.mkdir(parents=True)
    for n in range(count):
        (folder / f"Point {n:03d}.md").write_text(
            f"Next: [[Point {(n + 1) % count:03d}]]\n", "utf-8"
        )


@pytest.mark.parametrize(("scheme", "locale"), [("light", "en-US"), ("dark", "de-DE")])
async def test_the_graph_view(
    static_site: str,
    app: Any,
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    locale: str,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    (notebook / "Notes" / "Garden.md").write_text("See [[Shopping]] and [[Plan]]. #home\n", "utf-8")
    german = locale.startswith("de")
    async with notes_page(static_site, app, locale=locale, scheme=scheme) as page:
        await opened(page, f"{static_site}/notes.html?view=graph")
        await page.wait_for_selector(".graph-stage[data-mode=svg] .node")
        paths = await page.locator(".whole-graph .node").evaluate_all(
            "ns => ns.map(n => n.dataset.path)"
        )
        assert {"Notes/Shopping.md", "Notes/Plan.md", "Notes/Garden.md"} <= set(paths)
        legend = await page.locator(".graph-key").all_text_contents()
        assert "Notes" in legend and "Journal" in legend
        count = await page.locator(".graph-count").text_content()
        assert ("Notizen" if german else "notes") in count
        colours = await page.locator(".whole-graph .node circle").evaluate_all(
            "cs => new Set(cs.map(c => c.getAttribute('fill'))).size"
        )
        assert colours >= 2  # (by folder)

        named = "() => document.querySelector('.graph-stage').classList.contains('named')"
        if not await page.evaluate(named):
            for _ in range(6):
                await page.locator(".graph-zoom button").first.click()  # +
            await page.wait_for_function(named)  # names show when zoomed in
        await page.locator(".graph-zoom button").nth(2).click()  # fit

        await page.locator(".graph-bar select").first.select_option("Journal")
        await page.wait_for_function(
            "() => [...document.querySelectorAll('.whole-graph .node')]"
            ".every(n => n.dataset.path.startsWith('Journal/'))"
        )
        await page.locator(".graph-bar select").first.select_option("")
        await page.locator(".graph-bar select").nth(1).select_option("home")
        await page.wait_for_function(
            "() => document.querySelectorAll('.whole-graph .node').length === 3"
        )
        await page.locator('.whole-graph .node[data-path="Notes/Garden.md"] circle').click()
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Garden'"
        )
        assert page.errors == []  # type: ignore[attr-defined]


async def test_many_points_are_drawn_on_a_canvas(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    many_notes(notebook, 320)
    async with notes_page(static_site, app) as page:
        await opened(page, f"{static_site}/notes.html?view=graph")
        await page.wait_for_selector(".graph-stage[data-mode=canvas] canvas")
        assert (await page.locator(".graph-count").text_content()).startswith("32")
        drawn = await page.evaluate(
            "() => { const c = document.querySelector('.graph-stage canvas');"
            " const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;"
            " let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i]) n++; return n; }"
        )
        assert drawn > 1000  # (points and lines were painted)
        box = await page.locator(".graph-stage").bounding_box()
        assert box is not None
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        for _ in range(5):
            await page.mouse.wheel(0, -400)  # zoom in at the pointer
        await page.wait_for_function(
            "() => document.querySelector('.graph-stage').classList.contains('named')"
        )
        assert page.errors == []  # type: ignore[attr-defined]
