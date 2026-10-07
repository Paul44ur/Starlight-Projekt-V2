"""The pdf module: make a PDF from text and read it back (round trip), and ask before
touching a file outside the Starlight folder. Needs the 'pdf' extra, which CI installs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from starlight.core import Kernel, Paths

pytest.importorskip("pypdf")
pytest.importorskip("reportlab")


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else False

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        return None


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("pdf")
    return paths


async def test_round_trip_in_starlight_folder(project: Paths) -> None:
    user = User()  # no confirmations needed inside the Starlight folder
    async with Kernel(project, ui=user) as k:
        written = await k.dispatcher.call(
            "pdf.to_pdf",
            {"content": "Hello Paul.\n\nThis is a test PDF.", "path": "note.pdf", "title": "Note"},
        )
        assert written.ok and written.output["saved"].endswith("note.pdf")
        back = await k.dispatcher.call("pdf.read_pdf", {"path": "note.pdf"})
    assert back.ok and back.output["pages"] >= 1
    assert "Hello Paul" in back.output["text"]
    assert user.questions == []  # the Starlight folder is free


async def test_write_outside_asks_first(
    project: Paths, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside") / "x.pdf"
    async with Kernel(project, ui=User(False)) as k:  # denies
        result = await k.dispatcher.call("pdf.to_pdf", {"content": "secret", "path": str(outside)})
    assert not result.ok and "did not allow" in str(result.error)
    assert not outside.exists()


async def test_read_missing(project: Paths) -> None:
    async with Kernel(project, ui=User()) as k:
        result = await k.dispatcher.call("pdf.read_pdf", {"path": "nope.pdf"})
    assert not result.ok and "does not exist" in str(result.error)


async def test_starlight_folder_is_off_limits(project: Paths) -> None:
    async with Kernel(project, ui=User(True)) as k:
        result = await k.dispatcher.call(
            "pdf.to_pdf", {"content": "x", "path": str(project.root / "config" / "x.pdf")}
        )
    assert not result.ok and "off-limits" in str(result.error)
