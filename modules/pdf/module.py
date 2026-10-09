"""Read text out of PDF files, and make simple PDFs from text.

This fills the gap left by the files module, which handles text formats only. Reading inside
your Starlight folder is free; reading elsewhere, and writing anywhere outside it, asks first.
Starlight's own folder is off-limits. Scanned/image-only PDFs have no text to extract.

Needs the ``pdf`` extra (``pypdf`` and ``reportlab``); without it, the tools say so.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.safepaths import PathGuard


def _pypdf() -> Any | None:
    try:
        import pypdf
    except ImportError:
        return None
    return pypdf


def _reportlab() -> bool:
    try:
        import reportlab  # noqa: F401
    except ImportError:
        return False
    return True


class Config(BaseModel):
    max_read_chars: int = Field(default=40_000, ge=1000)
    max_file_mb: float = Field(default=25, gt=0)


class PdfError(ToolError):
    pass


class PdfModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.root = ctx.paths.root.resolve()
        self.output = ctx.output_dir
        self.guard = PathGuard(self.root, self.output, error=PdfError, anywhere=ctx.write_anywhere)

    def _resolve(self, path: str) -> tuple[Path, bool]:
        # A bare or relative name goes in the Starlight folder; a full path is used as given.
        found = self.guard.resolve(path)
        return found.path, found.free

    @tool
    async def read_pdf(self, path: str, max_chars: int = 0) -> dict[str, Any]:
        """Read the text from a PDF file. Give a name in your Starlight folder or a full
        path (a path elsewhere asks first). Scanned PDFs with no text layer return nothing."""
        pypdf = _pypdf()
        if pypdf is None:
            raise PdfError("reading PDFs needs the 'pdf' extra (pypdf). Install it to use this.")
        file, free = self._resolve(path)
        if not file.is_file():
            raise PdfError(f"{file} does not exist")
        size = file.stat().st_size
        if size > self.config.max_file_mb * 1024**2:
            raise PdfError(f"that PDF is larger than {self.config.max_file_mb:g} MB")
        if not free and not await self.ctx.ui.confirm(f"Let Starlight read this PDF?\n  {file}"):
            raise PdfError(f"the user did not allow reading {file}")
        limit = max_chars or self.config.max_read_chars
        try:
            reader = pypdf.PdfReader(str(file))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:  # pypdf raises various errors on broken files
            raise PdfError(f"could not read the PDF: {exc}") from exc
        text = "\n\n".join(p.strip() for p in pages if p.strip())
        result: dict[str, Any] = {
            "path": str(file),
            "pages": len(pages),
            "text": text[:limit],
        }
        if not text:
            result["note"] = "no text found (this may be a scanned or image-only PDF)"
        elif len(text) > limit:
            result["note"] = f"only the first {limit} characters"
        return result

    @tool
    async def to_pdf(self, content: str, path: str, title: str = "") -> dict[str, Any]:
        """Make a simple PDF from text and save it. `path` is a name in your Starlight folder
        (saved without asking) or a full path elsewhere (asks first). Blank lines separate
        paragraphs; long lines wrap automatically."""
        if not _reportlab():
            raise PdfError("making PDFs needs the 'pdf' extra (reportlab). Install it to use this.")
        if not content.strip():
            raise PdfError("give some text to put in the PDF")
        found = self.guard.resolve(path)
        file, free = found.path, found.new_free  # always a new file (renamed if taken)
        if file.suffix.lower() != ".pdf":
            # "notes.txt" -> "notes.pdf", but "Rechnung 12.10.2026" and "Protokoll v1.2"
            # keep their dots: a suffix that is not a file type is part of the name.
            is_type = re.fullmatch(r"\.[a-z][a-z0-9]{0,4}", file.suffix.lower())
            file = file.with_suffix(".pdf") if is_type else file.with_name(file.name + ".pdf")
        if not free and not await self.ctx.ui.confirm(
            f"Save a PDF outside your Starlight folder?\n  {file}"
        ):
            raise PdfError(f"the user did not allow saving to {file}")
        if file.exists():
            file = _free_name(file)
        self.ctx.kill_switch.check()
        try:
            file.parent.mkdir(parents=True, exist_ok=True)
            _render_pdf(file, title, content)
        except OSError as exc:
            raise PdfError(f"could not save the PDF: {exc}") from exc
        self.ctx.logger.info("pdf written", path=str(file))
        await self.ctx.publish("files.saved", {"path": str(file), "kind": "pdf"})
        return {"saved": str(file), "pages": "built", "size": _human(file.stat().st_size)}


def _free_name(path: Path) -> Path:
    for number in range(2, 10_000):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise PdfError(f"too many files named like {path.name}")


def _render_pdf(file: Path, title: str, content: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(file), pagesize=A4, title=title or file.stem)
    story: list[Any] = []
    if title.strip():
        story.append(Paragraph(_escape(title), styles["Title"]))
        story.append(Spacer(1, 12))
    for block in content.replace("\r\n", "\n").split("\n\n"):
        text = _escape(block).replace("\n", "<br/>")
        if text.strip():
            story.append(Paragraph(text, styles["BodyText"]))
            story.append(Spacer(1, 8))
    doc.build(story or [Paragraph(_escape(content), styles["BodyText"])])


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _human(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024**2:.1f} MB"
