"""Knowledge: a private index over your own folders (text, Word, Excel, PDF) with answers
that name the file and page, quick notes that are part of it, and YouTube transcripts."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.core.model import Message, ModelResponse


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


class Reader:
    """A model that answers with the first excerpt's number and remembers the prompt."""

    name = "fake"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        self.prompts.append(messages[-1].content)
        return ModelResponse(content="It says so in [1].")


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("docs", "notes", "transcripts"):
        use_module(name)
    (paths.module_configs / "docs.yaml").write_text("reindex_minutes: 0\nchunk_chars: 300\n")
    return paths


@pytest.fixture
def library(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("library")
    (folder / "rental contract.txt").write_text(
        "Rental contract for the flat in Vienna.\n\n"
        "Pets: keeping a cat or a dog requires written permission from the landlord.\n\n"
        "The rent is due on the third working day of each month."
    )
    (folder / "pasta.md").write_text("# Pasta\nBoil water, add salt, cook spaghetti 9 minutes.")
    (folder / "setup.exe").write_bytes(b"MZ not a document")
    hidden = folder / ".git"
    hidden.mkdir()
    (hidden / "secret.txt").write_text("landlord permission pets cat dog")
    return folder


async def call(k: Kernel, name: str, /, **args: Any) -> Any:
    result = await k.dispatcher.call(name, args)
    assert result.ok, result.error
    return result.output


async def fails(k: Kernel, name: str, /, **args: Any) -> str:
    result = await k.dispatcher.call(name, args)
    assert not result.ok, result.output
    return str(result.error)


async def test_ask_your_own_documents_with_sources(project: Paths, library: Path) -> None:
    model = Reader()
    async with Kernel(project, ui=User(True), provider=model) as k:
        assert "empty" in await fails(k, "docs.ask", question="pets?")
        added = await call(k, "docs.add_folder", path=str(library))
        assert added["read"] == 2 and added["files"] == 2  # not the .exe, not .git
        answer = await call(k, "docs.ask", question="landlord permission pets cat dog")
        assert answer["answer"] == "It says so in [1]."
        assert answer["sources"][0]["file"].endswith("rental contract.txt")
        assert "written permission" in model.prompts[-1]
        hits = (await call(k, "docs.search", query="spaghetti boil water salt"))["results"]
        assert hits[0]["file"].endswith("pasta.md")

        # Only changed files are read again; deleted ones are forgotten.
        assert (await call(k, "docs.reindex"))["read"] == 0
        (library / "pasta.md").unlink()
        (library / "pasta2.md").write_text("Risotto needs stirring.")
        again = await call(k, "docs.reindex")
        assert again["read"] == 1 and again["removed"] == 1
    assert str(library) in (project.module_configs / "docs.yaml").read_text()


async def test_word_excel_and_pdf_are_read_with_pages(project: Paths, library: Path) -> None:
    docx = pytest.importorskip("docx")
    openpyxl = pytest.importorskip("openpyxl")
    pytest.importorskip("pypdf")
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    document = docx.Document()
    document.add_paragraph("Insurance policy number HX-4471 covers water damage.")
    document.save(str(library / "insurance.docx"))
    book = openpyxl.Workbook()
    book.active.append(["Car", "Service due", "2026-11-15"])
    book.save(str(library / "car.xlsx"))
    pdf = canvas.Canvas(str(library / "manual.pdf"))
    pdf.drawString(72, 720, "Page one: unpacking.")
    pdf.showPage()
    pdf.drawString(72, 720, "Page two: the filter must be cleaned monthly.")
    pdf.save()
    async with Kernel(project, ui=User(True), provider=Reader()) as k:
        await call(k, "docs.add_folder", path=str(library))
        results = (await call(k, "docs.search", query="filter cleaned monthly", limit=10))[
            "results"
        ]
        best = next(r for r in results if r["file"].endswith("manual.pdf"))  # best first
        assert best["page"] == 2  # the page it is on
        policy = await call(k, "docs.search", query="insurance policy water damage HX-4471")
        assert policy["results"][0]["file"].endswith("insurance.docx")
        car = await call(k, "docs.search", query="car service due")
        assert car["results"][0]["file"].endswith("car.xlsx")


async def test_starlights_own_folder_and_declining(project: Paths, library: Path) -> None:
    async with Kernel(project, ui=User(False)) as k:
        assert "off-limits" in await fails(k, "docs.add_folder", path=str(project.root))
        assert "not confirmed" in await fails(k, "docs.add_folder", path=str(library))
        assert (await call(k, "docs.folders"))["files"] == 0


def test_chunks_overlap_and_respect_size() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "modules"))
    try:
        from docs.module import chunks  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    text = " ".join(f"Sentence number {i} is here." for i in range(200))
    parts = list(chunks(text, 400))
    assert all(len(p) <= 400 for p in parts) and len(parts) > 5
    assert "Sentence number 199" in parts[-1]
    joined = " ".join(parts)
    assert all(f"number {i} " in joined for i in range(200))  # nothing lost


# -- notes -------------------------------------------------------------------------------


async def test_notes_write_add_find_and_feed_the_knowledge_base(
    project: Paths, documents: Path
) -> None:
    async with Kernel(project, ui=User(True), provider=Reader()) as k:
        await call(k, "notes.note", title="WiFi", text="Guest password is on the router sticker")
        await call(k, "notes.append", title="shopping", text="eggs")  # creates it
        await call(k, "notes.append", title="shop", text="milk")  # by part of the title
        shopping = (await call(k, "notes.read", title="shopping"))["text"]
        assert "eggs" in shopping and "milk" in shopping
        found = (await call(k, "notes.find", text="router"))["matches"]
        assert found[0]["note"] == "WiFi"
        assert [n["title"] for n in (await call(k, "notes.list"))["notes"]] == ["shopping", "WiFi"]
        assert "no note" in await fails(k, "notes.read", title="nothing")
        notes_folder = documents / "Starlight" / "Notes"
        assert (notes_folder / "WiFi.md").read_text().startswith("# WiFi")
        # notes are part of the knowledge base
        await call(k, "docs.reindex")
        hits = (await call(k, "docs.search", query="guest password router sticker"))["results"]
        assert hits[0]["file"].endswith("WiFi.md")
        await call(k, "notes.delete", title="WiFi")
        assert not (notes_folder / "WiFi.md").exists()


# -- transcripts -------------------------------------------------------------------------


def test_video_ids_from_links() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "modules"))
    try:
        from transcripts.module import video_id  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)
    for link in (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        "dQw4w9WgXcQ",
    ):
        assert video_id(link) == "dQw4w9WgXcQ", link
    with pytest.raises(Exception, match="YouTube link"):
        video_id("https://example.org/video")


async def test_summary_uses_the_transcript_with_time_marks(project: Paths) -> None:
    model = Reader()
    async with Kernel(project, provider=model) as k:
        module = k.modules.records["transcripts"].instance
        assert module is not None
        module._download = lambda vid: (  # type: ignore[attr-defined]
            "en",
            [(0.0, "Welcome to the video."), (45.0, "Step one: remove the cover."),
             (125.0, "Step two: clean the filter.")],
        )  # fmt: skip
        transcript = await call(k, "transcripts.youtube", url="https://youtu.be/dQw4w9WgXcQ")
        assert "[00:00] Welcome" in transcript["transcript"]
        assert "[02:05] Step two" in transcript["transcript"]
        await call(k, "transcripts.ask", url="https://youtu.be/dQw4w9WgXcQ", question="Filter?")
    assert "Step two: clean the filter." in model.prompts[-1]
