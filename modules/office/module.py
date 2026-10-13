"""Word and Excel files: read their text and tables, and make new ones.

`write_docx` turns simple text into a Word document (lines starting with "# " become
headings, "- " bullet points); `write_xlsx` makes a spreadsheet from rows; `append_rows`
adds rows to an existing sheet. New files go in your Starlight folder; a path anywhere else
asks first, and an existing file is only changed after you say yes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.safepaths import PathGuard

Cell = str | int | float | bool | None


class OfficeError(ToolError):
    pass


def _docx() -> Any:
    try:
        import docx
    except ImportError:
        return None
    return docx


def _openpyxl() -> Any:
    try:
        import openpyxl
    except ImportError:
        return None
    return openpyxl


class Config(BaseModel):
    max_file_mb: int = Field(default=50, ge=1)
    max_rows: int = Field(default=500, ge=10)


class OfficeModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    @property
    def guard(self) -> PathGuard:
        return PathGuard(
            self.ctx.paths.root,
            self.ctx.output_dir,
            error=OfficeError,
            anywhere=self.ctx.write_anywhere,
        )

    def _lib(self, name: str) -> Any:
        lib = _docx() if name == "docx" else _openpyxl()
        if lib is None:
            package = "python-docx" if name == "docx" else "openpyxl"
            raise OfficeError(f"this needs the 'office' extra ({package}). Install it to use this.")
        return lib

    def _existing(self, path: str, suffix: str) -> Path:
        found = self.guard.resolve(path).path
        if found.suffix.lower() != suffix:
            raise OfficeError(f"{found.name} is not a {suffix} file")
        if not found.is_file():
            raise OfficeError(f"{found} does not exist")
        if found.stat().st_size > self.config.max_file_mb * 1024**2:
            raise OfficeError(f"{found.name} is larger than {self.config.max_file_mb} MB")
        return found

    async def _target(self, path: str, suffix: str, replace: bool = False) -> Path:
        name = path if path.lower().endswith(suffix) else f"{path}{suffix}"
        found = self.guard.resolve(name)
        target = found.path
        if target.exists() and not replace:
            raise OfficeError(f"{target.name} already exists; give another name")
        if not found.new_free or target.exists():  # replacing always asks
            verb = "Replace" if target.exists() else "Save"
            if not await self.ctx.ui.confirm(f"{verb} {target}?"):
                raise OfficeError("not confirmed, nothing was written")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    # -- Word ----------------------------------------------------------------------------

    @tool
    def read_docx(self, path: str, max_chars: int = 0) -> dict[str, Any]:
        """The text of a Word document (headings, paragraphs and tables). A name is looked
        up in your Starlight folder; give a full path for elsewhere."""
        docx = self._lib("docx")
        file = self._existing(path, ".docx")
        document = docx.Document(str(file))
        parts: list[str] = []
        for paragraph in document.paragraphs:
            text = paragraph.text.strip()
            if not text:
                continue
            style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
            parts.append(f"# {text}" if style.startswith(("heading", "title")) else text)
        for index, table in enumerate(document.tables, 1):
            parts.append(f"[Table {index}]")
            parts += [" | ".join(cell.text.strip() for cell in row.cells) for row in table.rows]
        text = "\n".join(parts)
        if max_chars > 0 and len(text) > max_chars:
            text = text[:max_chars] + "…"
        return {"file": str(file), "characters": len(text), "text": text}

    @tool
    async def write_docx(self, name: str, content: str, title: str = "") -> dict[str, Any]:
        """Make a Word document from text: "# " lines are headings, "## " sub-headings,
        "- " bullet points, blank lines separate paragraphs. Saved in your Starlight folder
        unless `name` is a full path elsewhere (a new file there is saved without asking
        while output.write_anywhere is on)."""
        docx = self._lib("docx")
        if not content.strip():
            raise OfficeError("give the text for the document")
        target = await self._target(name, ".docx")
        document = docx.Document()
        if title.strip():
            document.add_heading(title.strip(), level=0)
        paragraph: list[str] = []

        def flush() -> None:
            if paragraph:
                document.add_paragraph(" ".join(paragraph))
                paragraph.clear()

        for raw in content.splitlines():
            line = raw.rstrip()
            if not line.strip():
                flush()
            elif line.startswith("#"):
                flush()
                level = min(len(line) - len(line.lstrip("#")), 4)
                document.add_heading(line.lstrip("#").strip(), level=level)
            elif line.lstrip().startswith(("- ", "* ", "• ")):
                flush()
                document.add_paragraph(line.lstrip()[2:].strip(), style="List Bullet")
            else:
                paragraph.append(line.strip())
        flush()
        document.save(str(target))
        await self.ctx.publish("files.saved", {"path": str(target)})
        return {"saved": str(target)}

    # -- Excel ---------------------------------------------------------------------------

    @tool
    def read_xlsx(self, path: str, sheet: str = "", max_rows: int = 0) -> dict[str, Any]:
        """The cells of an Excel sheet as rows (the first sheet unless `sheet` is given),
        plus the names of all sheets. Formulas show their last calculated value."""
        openpyxl = self._lib("xlsx")
        file = self._existing(path, ".xlsx")
        book = openpyxl.load_workbook(str(file), read_only=True, data_only=True)
        try:
            names = list(book.sheetnames)
            if sheet and sheet not in names:
                raise OfficeError(f"no sheet '{sheet}'; sheets: {', '.join(names)}")
            worksheet = book[sheet or names[0]]
            limit = min(max_rows or self.config.max_rows, self.config.max_rows)
            rows: list[list[Cell]] = []
            total = 0
            for row in worksheet.iter_rows(values_only=True):
                if all(v is None for v in row):
                    continue
                total += 1
                if len(rows) < limit:
                    rows.append([_plain(v) for v in row])
        finally:
            book.close()
        return {"file": str(file), "sheets": names, "sheet": worksheet.title, "rows": rows,
                "total_rows": total, "shown": len(rows)}  # fmt: skip

    @tool
    async def write_xlsx(
        self, name: str, rows: list[list[Cell]], sheet: str = "Sheet1", bold_header: bool = True
    ) -> dict[str, Any]:
        """Make an Excel file from rows, e.g. [["Item", "Price"], ["Milk", 1.29]]. The
        first row is bold (a header) unless `bold_header` is false. Values starting with
        "=" are written as text, not formulas."""
        openpyxl = self._lib("xlsx")
        if not rows:
            raise OfficeError("give at least one row")
        target = await self._target(name, ".xlsx")
        book = openpyxl.Workbook()
        worksheet = book.active
        worksheet.title = sheet[:31] or "Sheet1"
        for row in rows:
            worksheet.append([_safe_cell(worksheet, v) for v in row])
        if bold_header:
            for cell in worksheet[1]:
                cell.font = openpyxl.styles.Font(bold=True)
        _fit_columns(worksheet)
        book.save(str(target))
        await self.ctx.publish("files.saved", {"path": str(target)})
        return {"saved": str(target), "rows": len(rows)}

    @tool
    async def append_rows(
        self, path: str, rows: list[list[Cell]], sheet: str = ""
    ) -> dict[str, Any]:
        """Add rows to the end of a sheet in an existing Excel file. Asks first unless the
        file is in your Starlight folder."""
        openpyxl = self._lib("xlsx")
        file = self._existing(path, ".xlsx")
        if not rows:
            raise OfficeError("give at least one row")
        if not self.guard.is_free(file) and not await self.ctx.ui.confirm(
            f"Add {len(rows)} row(s) to {file}?"
        ):
            raise OfficeError("not confirmed, nothing was changed")
        book = openpyxl.load_workbook(str(file))
        if sheet and sheet not in book.sheetnames:
            raise OfficeError(f"no sheet '{sheet}'; sheets: {', '.join(book.sheetnames)}")
        worksheet = book[sheet] if sheet else book.active
        for row in rows:
            worksheet.append([_safe_cell(worksheet, v) for v in row])
        book.save(str(file))
        return {"file": str(file), "sheet": worksheet.title, "added": len(rows),
                "total_rows": worksheet.max_row}  # fmt: skip


def _plain(value: Any) -> Cell:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)  # dates, times, decimals


def _safe_cell(worksheet: Any, value: Cell) -> Any:
    """Text starting with "=" would be saved as a formula and run when the file is opened
    (formula injection): save it as text, exactly as written. In an .xlsx file only "="
    makes a formula, so "+43 664 1234567" or "- Rückruf" are saved unchanged."""
    if isinstance(value, str) and value.startswith("="):
        from openpyxl.cell.cell import Cell as SheetCell

        cell = SheetCell(worksheet, value=value)
        cell.data_type = "s"
        cell.quotePrefix = True  # Excel shows and edits it as text, too
        return cell
    return value


def _fit_columns(worksheet: Any) -> None:
    for column in worksheet.columns:
        width = max((len(str(c.value)) for c in column if c.value is not None), default=8)
        worksheet.column_dimensions[column[0].column_letter].width = min(max(width + 2, 8), 60)
