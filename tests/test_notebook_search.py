"""Block E1 of the notes plan (docs/plan-notes-app.md): searching the notes with words,
"exact words", -word, tag: and path:, through FTS5 or a plain scan, with the hits marked."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.permissions import Taint, request_taint
from starlight.notebook.index import Index, has_fts5
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_home_devices import call, fails
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL

NOTES = {
    "Notes/Shopping.md": "---\ntags: [home]\n---\n# Shopping\n- oat milk\n- bread\n"
    "Call Herr Müller about the cake.\n",
    "Notes/Milk facts.md": "Milk is good. Oat milk too. #food\n",
    "Clippings/Bread.md": "---\nsource: https://bake.example/\n---\nHow to bake bread fast.\n",
    "Journal/2026-11-01.md": "Bought milk and bread. #home/kitchen\n",
    "Notes/Code.md": "Use `milk()` in code? No: plain milk only.\n",
}


def write_notes(root: Path) -> Path:
    for relative, text in NOTES.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


def test_the_search_language() -> None:
    from starlight.notebook.search import fts_query, parse

    query = parse(
        'tag:#work/x pfad:"My folder" "oat  milk" -cake -"old list" -tag:done '
        "-path:Journal milk bre Schlagwort:home"
    )
    assert query.words == ["milk", "bre"] and query.phrases == ["oat milk"]
    assert query.without == ["cake", "old list"]
    assert query.tags == ["work/x", "home"] and query.not_tags == ["done"]
    assert query.paths == ["My folder"] and query.not_paths == ["Journal"]
    assert fts_query(query) == '"milk" "bre"* "oat milk" NOT "cake" NOT "old list"'
    assert fts_query(parse('"; DROP TABLE files; --')) == '"DROP TABLE files"'  # words only
    assert fts_query(parse("-cake")) == "" and parse("-cake").empty  # (NOT cannot stand alone)
    assert parse("C++ e-mail").words == ["C", "e", "mail"]


def test_marks_snippets_and_letters_without_accents() -> None:
    from starlight.notebook.search import fold, marks, parse, snippet

    assert fold("Müller ÄPFEL ß") == "muller apfel ß"  # (the same length)
    query = parse('milk "oat milk" mul')
    assert marks("Oat milk, milky way, Müller", query) == [(0, 8), (10, 14), (21, 24)]
    text, where = snippet("# Title\n- [ ] buy " + "x " * 60 + "oat milk today " + "y " * 60, query)
    assert text.startswith("…") and text.endswith("…") and "oat milk today" in text
    assert [text[a:b] for a, b in where] == ["oat milk"]
    plain, _ = snippet(
        "# Heading\n- [x] milk\n> [!tip] quoted **bold** [[Plan|plan]]", parse("milk")
    )
    assert plain == "Heading milk quoted bold plan"  # (the Markdown marks are left out)


@pytest.mark.parametrize("fts", [True, False])
def test_searching_with_and_without_fts5(tmp_path: Path, fts: bool) -> None:
    from starlight.notebook.search import search

    if fts and not has_fts5():
        pytest.skip("this Python's SQLite has no FTS5")
    index = Index(tmp_path / "index.sqlite", write_notes(tmp_path / "Starlight"), fts=fts)
    index.refresh()

    def paths(text: str) -> list[str]:
        return [r.path for r in search(index, text)]

    assert paths("milk")[0] == "Notes/Milk facts.md"  # in the title: first
    assert set(paths("milk")) == {
        "Notes/Milk facts.md", "Journal/2026-11-01.md", "Notes/Shopping.md", "Notes/Code.md",
    }  # fmt: skip
    assert set(paths('"oat milk"')) == {"Notes/Milk facts.md", "Notes/Shopping.md"}
    assert set(paths("milk -bread")) == {"Notes/Milk facts.md", "Notes/Code.md"}
    assert paths('mil -"oat milk" -code') == ["Journal/2026-11-01.md"]
    assert set(paths("tag:#home")) == {"Journal/2026-11-01.md", "Notes/Shopping.md"}
    assert paths("tag:home/kitchen") == ["Journal/2026-11-01.md"]
    assert paths("tag:home -tag:home/kitchen bread") == ["Notes/Shopping.md"]
    assert paths("path:Clip") == ["Clippings/Bread.md"]
    assert set(paths("bread -path:Journal -path:clippings")) == {"Notes/Shopping.md"}
    assert paths("path:Notes cake") == ["Notes/Shopping.md"]
    assert paths("bre")[:1] == ["Clippings/Bread.md"] and len(paths("bre")) == 3
    assert paths("nothing") == [] and paths("") == [] and paths("-milk") == []
    assert paths("muller") == (["Notes/Shopping.md"] if fts else [])  # accents: FTS5 only
    [hit] = search(index, "oat milk -good")
    assert hit.path == "Notes/Shopping.md" and hit.snippet.startswith("Shopping oat milk")
    assert [hit.snippet[a:b] for a, b in hit.marks] == ["oat", "milk"]
    [facts] = search(index, "milk tag:food")
    assert facts.title == "Milk facts" and facts.title_marks == [(0, 4)]


async def test_notes_find_uses_the_search_and_guards_clippings(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    use_module("notes")
    write_notes(documents / "Starlight")
    async with Kernel(paths) as k:
        found = await call(k, "notes.find", text='"oat milk" -good')
        assert found["matches"] == [
            {"note": "Shopping", "path": "Notes/Shopping.md", "line": 5, "text": "- oat milk"}
        ]
        journal = await call(k, "notes.find", text="milk path:Journal")
        assert [m["path"] for m in journal["matches"]] == ["Journal/2026-11-01.md"]
        assert "untrusted" not in journal
        taint = Taint()
        token = request_taint.set(taint)
        try:
            clipped = await call(k, "notes.find", text="bake")
        finally:
            request_taint.reset(token)
        assert clipped["untrusted"] is True and taint.sources == ["notes.find"]
        assert "what should I look for" in await fails(k, "notes.find", text="-milk")


def test_the_search_api_marks_the_hits(app: Any, notebook: Path) -> None:
    hits = app.get("/api/notes/search", params={"q": "milk -bread"}, headers=LOCAL).json()
    assert [h["path"] for h in hits["hits"]] == ["Recipes/Pancakes.md"]  # (Shopping has both)
    hits = app.get("/api/notes/search", params={"q": '"milk" path:Notes'}, headers=LOCAL).json()
    [shopping] = hits["hits"]
    assert shopping["path"] == "Notes/Shopping.md"
    assert [shopping["snippet"][a:b] for a, b in shopping["marks"]] == ["milk"]


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
async def test_the_page_marks_the_hits(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch, locale: str
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    write_notes(notebook)
    async with notes_page(static_site, app, locale=locale) as page:
        await opened(page, f"{static_site}/notes.html")
        await page.locator("#search").fill("milk -bread")
        await page.wait_for_selector(".card-note mark")
        cards = await page.locator(".card-note").evaluate_all(
            "cs => cs.map(c => [c.querySelector('.t').textContent,"
            " [...c.querySelectorAll('mark')].map(m => m.textContent)])"
        )
        assert cards[0] == ["Milk facts", ["Milk", "Milk", "milk"]]  # (the title counts more)
        assert ["Code", ["milk", "milk"]] in cards
        assert "Shopping" not in [c[0] for c in cards]  # (it has bread)
        tips = await page.locator(".search-tips").text_content()
        assert tips.startswith("Suchtipps:" if locale == "de-DE" else "Search tips:")
        assert 'tag:#work  path:Journal  "exact words"  -word' in tips
        await page.locator(".card-note", has_text="Code").click()
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Code'"
        )
        assert page.errors == []  # type: ignore[attr-defined]
