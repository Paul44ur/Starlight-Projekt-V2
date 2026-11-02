"""Block E2 of the notes plan (docs/plan-notes-app.md): related notes (by meaning through the
knowledge base, else by links and tags), mentions without a link with one-click linking,
and the text in pictures and scanned PDFs (OCR, here with a fake reader)."""

from __future__ import annotations

import os
import stat
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_home_devices import call, fails
from tests.test_knowledge import User
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL

GARDEN = {
    "Notes/Garden.md": "---\naliases: [Garten]\ntags: [home, plants]\n---\n# Garden\n"
    "Tomatoes, roses and the compost heap. See [[Tools]].\n",
    "Notes/Tools.md": "# Tools\nFor the garden: a spade and a rake.\n",
    "Notes/Roses.md": "---\ntags: [plants]\n---\nRoses and tomatoes like the compost heap.\n",
    "Notes/Diary.md": "Worked in the garten today. The gardener came.\n"
    "```\nGarden in code\n```\nhttps://x.example/Garden and `Garden`\n",
    "Notes/Car.md": "The car needs new tyres and an oil change at the garage.\n",
    "Notes/Linked.md": "My [[Garden]] and the garden again.\n",
    "Clippings/Page.md": "---\nsource: https://x.example/\n---\nA garden page from the web.\n",
}


def plant(root: Path) -> Path:
    for relative, text in GARDEN.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(text, encoding="utf-8")
    return root


def test_related_by_links_and_tags_and_the_mentions(tmp_path: Path) -> None:
    from starlight.notebook import related
    from starlight.notebook.index import Index

    index = Index(tmp_path / "index.sqlite", plant(tmp_path / "Starlight"))
    index.refresh()
    found = related.by_structure(index, "Notes/Garden.md")
    assert [(r["path"], r["why"]) for r in found] == [
        ("Notes/Linked.md", ["links here"]),
        ("Notes/Tools.md", ["linked from here"]),
        ("Notes/Roses.md", ["#plants"]),
    ]
    mentions = related.mentions(index, "Notes/Garden.md")
    assert [(m.path, m.line, m.text[m.start : m.end]) for m in mentions] == [
        ("Notes/Diary.md", 0, "garten"),  # an alias; "gardener", code and the address are not
        ("Notes/Tools.md", 1, "garden"),
    ]  # (Linked.md links already; a clipping stays as it was)
    assert related.spans("a [[Garden]] b `Garden` garden", ["Garden"]) == [(24, 30)]


def test_one_click_linking_keeps_a_version_and_refuses_a_changed_line(tmp_path: Path) -> None:
    from starlight.notebook import NoteConflict, related
    from starlight.notebook.history import History
    from starlight.notebook.index import Index

    root = plant(tmp_path / "Starlight")
    (root / "Journal").mkdir()
    (root / "Journal" / "Garden.md").write_text("Another note called Garden.\n", "utf-8")
    index = Index(tmp_path / "index.sqlite", root)
    index.refresh()
    history = History(tmp_path / "history.sqlite")
    diary = root / "Notes" / "Diary.md"
    line = "Worked in the garten today. The gardener came."
    related.link_mention(
        index, history, source="Notes/Diary.md", line=0, expected=line, target="Notes/Garden.md"
    )
    assert diary.read_text("utf-8").startswith(
        "Worked in the [[Garden|garten]] today. The gardener came.\n"
    )
    assert len(history.versions(diary)) == 1
    with pytest.raises(NoteConflict, match="changed meanwhile"):
        related.link_mention(
            index, history, source="Notes/Diary.md", line=0, expected=line,
            target="Notes/Garden.md",
        )  # fmt: skip
    index.refresh(force=True)
    tools = "For the garden: a spade and a rake."
    related.link_mention(  # (seen from Journal, "Garden" alone would be Journal/Garden)
        index, history, source="Notes/Tools.md", line=1, expected=tools,
        target="Notes/Garden.md",
    )  # fmt: skip
    assert "For the [[Garden|garden]]: a spade" in (root / "Notes" / "Tools.md").read_text("utf-8")
    assert related.link_name(index, "Notes/Garden.md", "Journal/x.md") == "Notes/Garden"


class FakeOcr:
    def __init__(self) -> None:
        self.read_files: list[str] = []

    def read(self, picture: Path) -> str:
        self.read_files.append(picture.name)
        if picture.name == "receipt.png":
            return "Receipt from the bakery: 12.50 EUR"
        return f"Scanned letter from the tax office, page {len(self.read_files)}"


@pytest.fixture
def scans(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from PIL import Image

    folder = tmp_path_factory.mktemp("Scans")  # (outside the test's Starlight folder)
    Image.new("RGB", (60, 40), "white").save(folder / "receipt.png")
    pages = [Image.new("RGB", (120, 80), "white") for _ in range(3)]
    pages[0].save(folder / "letter.pdf", "PDF", save_all=True, append_images=pages[1:])
    (folder / "huge.png").write_bytes(b"\x89PNG\r\n\x1a\n" + os.urandom(2 * 1024**2))
    return folder


async def test_ocr_text_from_pictures_and_scanned_pages(
    paths: Paths, use_module: Callable[[str], Path], scans: Path
) -> None:
    pytest.importorskip("pypdf")
    use_module("docs")
    (paths.module_configs / "docs.yaml").write_text(
        "reindex_minutes: 0\ninclude_notes: false\nocr_max_mb: 1\nocr_max_pages: 2\n"
    )
    async with Kernel(paths, ui=User(True)) as k:
        docs: Any = k.modules.records["docs"].instance
        docs.ocr = None  # without Tesseract: pictures are left out
        await call(k, "docs.add_folder", path=str(scans))
        assert (await call(k, "docs.folders"))["files"] == 1  # only the PDF, with no text
        fake = docs.ocr = FakeOcr()
        (scans / "letter.pdf").touch()  # (changed: read again)
        os.utime(scans / "letter.pdf", (1, 1))
        await call(k, "docs.reindex")
        assert fake.read_files.count("receipt.png") == 1
        assert len(fake.read_files) == 3  # the receipt and two of the three pages (the limit)
        assert "huge.png" not in fake.read_files  # over ocr_max_mb
        bakery = (await call(k, "docs.search", query="receipt bakery EUR"))["results"]
        assert bakery[0]["file"].endswith("receipt.png")
        letter = (await call(k, "docs.search", query="scanned letter tax office"))["results"]
        assert letter[0]["file"].endswith("letter.pdf") and letter[0]["page"] in (1, 2)


@pytest.mark.skipif(sys.platform == "win32", reason="a shell script stands in for Tesseract")
def test_tesseract_is_run_as_a_program(tmp_path: Path) -> None:
    from modules.docs.ocr import Tesseract, find_tesseract

    program = tmp_path / "tesseract"
    program.write_text(
        '#!/bin/sh\nif [ "$1" = "--list-langs" ]; then printf "List of languages:\\neng\\nosd\\n";'
        ' else echo "read $1 $2 $3 $4"; fi\n'
    )
    program.chmod(program.stat().st_mode | stat.S_IEXEC)
    assert find_tesseract(str(program)) == str(program)
    assert find_tesseract(str(tmp_path / "missing")) is None
    reader = Tesseract(str(program), "deu+eng")
    assert reader.languages == "eng"  # (only the installed ones)
    assert reader.read(Path("photo.png")) == "read photo.png stdout -l eng"


async def test_similar_files_and_related_notes_by_meaning(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    for name in ("docs", "notes"):
        use_module(name)
    (paths.module_configs / "docs.yaml").write_text("reindex_minutes: 0\nchunk_chars: 300\n")
    root = plant(documents / "Starlight")
    async with Kernel(paths) as k:
        before = await call(k, "notes.related", title="Garden")
        assert before["by"] == "links and tags"  # (the knowledge base has not read it yet)
        await call(k, "docs.reindex")
        similar = await call(k, "docs.similar", path=str(root / "Notes" / "Garden.md"))
        assert Path(similar["similar"][0]["file"]).name == "Roses.md"  # tomatoes, compost
        assert "Car.md" in Path(similar["similar"][-1]["file"]).name or len(similar["similar"]) < 6
        assert "not in the knowledge base" in await fails(
            k, "docs.similar", path=str(root / "Nothing.md")
        )
        meaning = await call(k, "notes.related", title="Garden")
        assert meaning["by"] == "meaning" and meaning["related"][0]["title"] == "Roses"
        said = await call(k, "notes.mentions", title="Garden")
        assert [(m["note"], m["line"]) for m in said["mentions"]] == [("Diary", 1), ("Tools", 2)]


def test_the_api_for_related_mentions_and_linking(app: Any, notebook: Path) -> None:
    plant(notebook)
    query = {"path": "Notes/Garden.md"}
    found = app.get("/api/note/related", params=query, headers=LOCAL).json()
    assert found["by"] == "links and tags" and found["related"][0]["path"] == "Notes/Linked.md"
    [diary, tools] = app.get("/api/note/mentions", params=query, headers=LOCAL).json()["mentions"]
    assert diary["path"] == "Notes/Diary.md" and diary["text"][diary["start"] : diary["end"]] == (
        "garten"
    )
    body = {"path": "Notes/Garden.md", "source": "Notes/Tools.md", "line": 1,
            "expected": tools["text"]}  # fmt: skip
    assert app.post("/api/note/link-mention", json=body, headers=LOCAL).status_code == 200
    assert "[[Garden|garden]]" in (notebook / "Notes" / "Tools.md").read_text("utf-8")
    again = app.post("/api/note/link-mention", json=body, headers=LOCAL)
    assert again.status_code == 409
    after = app.get("/api/note/mentions", params=query, headers=LOCAL).json()["mentions"]
    assert [m["path"] for m in after] == ["Notes/Diary.md"]


@pytest.mark.parametrize(("scheme", "locale"), [("light", "en-US"), ("dark", "de-DE")])
async def test_the_page_shows_related_notes_and_links_a_mention(
    static_site: str,
    app: Any,
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    locale: str,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    plant(notebook)
    german = locale.startswith("de")
    async with notes_page(static_site, app, locale=locale, scheme=scheme) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FGarden.md")
        await page.wait_for_selector("#related li button")
        related = await page.locator("#related button").evaluate_all(
            "bs => bs.map(b => [b.firstChild.textContent, b.querySelector('small').textContent])"
        )
        assert related[0] == ["Linked", "verlinkt hierher" if german else "links here"]
        assert ["Roses", "#plants"] in related
        await page.wait_for_selector("#mentions li.mention")
        marks = await page.locator("#mentions mark").all_text_contents()
        assert marks == ["garten", "garden"]
        assert await page.locator("#mentions .link-it").first.text_content() == (
            "Verlinken" if german else "Link"
        )
        await page.locator("#mentions li.mention", has_text="spade").locator(".link-it").click()
        await page.wait_for_function(
            "() => [...document.querySelectorAll('#backlinks button')]"
            ".some(b => b.firstChild.textContent === 'Tools')"
        )
        assert "[[Garden|garden]]" in (notebook / "Notes" / "Tools.md").read_text("utf-8")
        await page.wait_for_function(
            "() => document.querySelectorAll('#mentions li.mention').length === 1"
        )
        assert page.errors == []  # type: ignore[attr-defined]
