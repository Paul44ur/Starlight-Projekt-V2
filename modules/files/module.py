"""Save the files Starlight makes for you, and read them back.

* files go to your Starlight folder (by default "Starlight" in your Documents, see
  ``output.folder`` in config/starlight.yaml); saving there needs no question
* anywhere else Starlight asks you first and shows the full path, unless you listed the
  folder under ``trusted_folders``
* an existing file is never overwritten silently: the new one gets a free name such as
  "letter (2).txt", or you are asked before a file is replaced
* Starlight's own folder (settings, data, memory, logs) is off-limits, also for reading
* text formats only: a Word file or a PDF written as plain text would not open, so those
  are refused with a suggestion (.md, .html, .csv, .txt)
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.output import known_folders

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # not allowed in Windows file names
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)

# Not plain text: written as text, these would be broken files.
_DOCUMENTS = {".doc", ".docx", ".odt", ".pdf", ".pages", ".epub"}
_SHEETS = {".xls", ".xlsx", ".xlsm", ".ods", ".numbers"}
_SLIDES = {".ppt", ".pptx", ".odp", ".key"}
_OTHER_BINARY = {
    ".zip", ".7z", ".rar", ".gz", ".tar", ".exe", ".dll", ".msi", ".lnk", ".iso",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".tif", ".tiff",
    ".mp3", ".mp4", ".wav", ".avi", ".mov", ".mkv", ".flac", ".ogg",
    ".sqlite", ".db", ".gguf", ".bin", ".pyc",
}  # fmt: skip
# Windows runs these when they are double-clicked: worth a word in the question.
_RUNNABLE = {
    ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta",
    ".reg", ".scr", ".com", ".pif", ".msc", ".py", ".pyw", ".url",
}  # fmt: skip


class Config(BaseModel):
    # Replacing an existing file asks first, also in your Starlight folder.
    ask_before_replacing: bool = True
    # More folders where saving and reading need no question, e.g. ["D:/Work/Reports"].
    trusted_folders: list[str] = Field(default_factory=list)
    max_file_mb: float = Field(default=10, gt=0)  # largest file it saves or reads
    max_read_chars: int = Field(default=40_000, ge=1000)  # longer files are cut off


class FileError(ToolError):
    """A request the module turns down. The message goes back to the AI."""


@dataclass(frozen=True)
class Target:
    path: Path  # absolute, resolved
    free: bool  # in the Starlight folder or a trusted folder: no question needed


def clean_name(part: str) -> str:
    """A file or folder name Windows accepts ("Report: June?" -> "Report_ June_")."""
    name = _INVALID.sub("_", part).rstrip(" .")
    if not name:
        return "_"
    return f"_{name}" if _RESERVED.match(name) else name


def binary_hint(suffix: str) -> str | None:
    """Why a file type cannot be written as text, and what to use instead."""
    suffix = suffix.lower()
    if suffix in _DOCUMENTS:
        return "save the text as .md, .html or .txt instead (Word opens .html files)"
    if suffix in _SHEETS:
        return "save the table as .csv instead (Excel opens it)"
    if suffix in _SLIDES:
        return "save it as .md or .html instead"
    if suffix in _OTHER_BINARY:
        return "only text files can be saved"
    return None


def size_text(size: int) -> str:
    if size < 1024:
        return f"{size} bytes"
    if size < 1024**2:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024**2:.1f} MB"


def free_name(path: Path) -> Path:
    """ "letter.txt" -> "letter (2).txt", the first name that is not taken."""
    for number in range(2, 10_000):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileError(f"too many files named like {path.name}")


def decode(data: bytes) -> str:
    """UTF-8 (with or without BOM), else Windows' own encoding (older files, umlauts)."""
    for cut in range(4):  # a cut-off read may end inside a UTF-8 character
        try:
            return data[: len(data) - cut].decode("utf-8-sig")
        except UnicodeDecodeError:
            continue
    return data.decode("cp1252", errors="replace")


class FilesModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.root = ctx.paths.root.resolve()  # Starlight's own folder: off-limits
        self.output = ctx.output_dir
        self.trusted = [self._expand(folder) for folder in self.config.trusted_folders]
        ctx.logger.info("ready", folder=str(self.output))

    # -- paths ---------------------------------------------------------------------------

    @staticmethod
    def _expand(text: str) -> Path:
        return Path(os.path.expandvars(text.strip())).expanduser().resolve()

    @staticmethod
    def _relative_parts(text: str) -> list[str]:
        parts = [p for p in re.split(r"[\\/]+", text) if p not in ("", ".")]
        if ".." in parts:
            raise FileError(
                "'..' is not allowed in a name; give a full path for a folder outside "
                "the Starlight folder"
            )
        return [clean_name(p) for p in parts]

    def _target(self, text: str) -> Target:
        """A file name inside the Starlight folder, or a full path anywhere else."""
        raw = os.path.expandvars(text.strip())
        if not raw:
            raise FileError("give a file name, e.g. 'notes.txt'")
        given = Path(raw).expanduser()
        if given.is_absolute():
            normal = Path(os.path.normpath(given))
            path = Path(normal.anchor, *(clean_name(p) for p in normal.parts[1:]))
        else:
            parts = self._relative_parts(raw)
            if not parts:
                raise FileError("give a file name, e.g. 'notes.txt'")
            path = self.output.joinpath(*parts)
        path = path.resolve()
        if path == self.root or path.is_relative_to(self.root):
            raise FileError(
                f"{path} is inside Starlight's own folder ({self.root}), which holds its "
                f"settings and data. Save it in the Starlight folder ({self.output}) or "
                "somewhere else."
            )
        if path.is_dir():
            raise FileError(f"{path} is a folder; give a file name")
        free = any(path.is_relative_to(f) for f in (self.output, *self.trusted))
        return Target(path, free)

    def _where(self, path: Path) -> str:
        if path.is_relative_to(self.output):
            return path.relative_to(self.output).as_posix()
        return str(path)

    # -- tools ---------------------------------------------------------------------------

    @tool
    async def save(
        self,
        path: str,
        content: str,
        if_exists: Literal["rename", "replace", "append"] = "rename",
    ) -> dict[str, Any]:
        """Save text you made as a file: a letter, notes, a report, a table (.csv), a web
        page (.html), code. `path` is a file name such as "letter.txt" or
        "reports/june.md", saved in the user's Starlight folder without asking; or a full
        path elsewhere (e.g. on the Desktop, see files.locations), which asks the user
        first. If the file exists: "rename" (default) saves under a free name like
        "letter (2).txt", "replace" overwrites it (asks first), "append" adds to its end.
        Text formats only: not .docx, .xlsx or .pdf. For a CSV that Excel opens in
        German-speaking and most European countries, separate the columns with ";"."""
        self.ctx.kill_switch.check()
        target = self._target(path)
        hint = binary_hint(target.path.suffix)
        if hint is not None:
            raise FileError(f"{target.path.name} is not a text format: {hint}")
        dest = target.path
        exists = dest.exists()
        mode = if_exists if exists else "new"
        if mode == "rename":
            dest = free_name(dest)
        text = content.replace("\r\n", "\n").replace("\r", "\n")
        if mode == "append" and dest.stat().st_size and not _ends_with_newline(dest):
            text = "\n" + text
        bom = dest.suffix.lower() == ".csv" and mode != "append"  # Excel needs it for ä, ö, ü
        encoding = "utf-8-sig" if bom else "utf-8"
        size = len(text.encode(encoding))
        if size > self.config.max_file_mb * 1024**2:
            raise FileError(
                f"too large ({size_text(size)}); the limit is {self.config.max_file_mb:g} MB"
            )

        question = self._save_question(dest, mode, size, target.free)
        if question is not None and not await self.ctx.ui.confirm(question):
            raise FileError(f"the user did not allow this, so nothing was saved to {dest}")
        self.ctx.kill_switch.check()  # the question may have taken a while

        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if mode == "append":
                with dest.open("a", encoding=encoding) as handle:
                    handle.write(text)
            else:
                _write_new(dest, text, encoding)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            if isinstance(exc, PermissionError):
                reason += " (is the file open in another program, e.g. Excel?)"
            raise FileError(f"could not save {dest}: {reason}") from exc

        self.ctx.logger.info("saved", path=str(dest), bytes=size, mode=mode)
        if self.ctx.stats is not None:
            self.ctx.stats.record("files.saved", 1, mode=mode)
        await self.ctx.publish("files.saved", {"path": str(dest), "bytes": size, "mode": mode})
        result: dict[str, Any] = {"saved": str(dest), "size": size_text(size)}
        if mode == "rename":
            result["note"] = f"{target.path.name} already existed, so this is {dest.name}"
        elif mode in ("replace", "append"):
            result["note"] = "replaced the old file" if mode == "replace" else "added to the end"
        return result

    def _save_question(self, dest: Path, mode: str, size: int, free: bool) -> str | None:
        if mode == "replace" and (not free or self.config.ask_before_replacing):
            question = f"Replace this file with a new version ({size_text(size)})?\n  {dest}"
        elif free:
            return None
        elif mode == "append":
            question = f"Add {size_text(size)} to the end of this file?\n  {dest}"
        else:
            question = f"Save a file outside your Starlight folder ({size_text(size)})?\n  {dest}"
        if dest.suffix.lower() in _RUNNABLE:
            question += f"\n  Note: Windows can run {dest.suffix} files when they are opened."
        return question

    @tool
    async def read(self, path: str) -> dict[str, Any]:
        """Read a text file: one in the Starlight folder (a name such as "letter.txt"), or
        a full path elsewhere, which asks the user first. Long files are cut off."""
        self.ctx.kill_switch.check()
        target = self._target(path)
        file = target.path
        if not file.is_file():
            raise FileError(f"{file} does not exist")
        if binary_hint(file.suffix) is not None:
            raise FileError(f"{file.name} is not a text file")
        size = file.stat().st_size
        if not target.free and not await self.ctx.ui.confirm(
            f"Let Starlight read this file ({size_text(size)})?\n  {file}"
        ):
            raise FileError(f"the user did not allow reading {file}")
        limit = min(int(self.config.max_file_mb * 1024**2), self.config.max_read_chars * 4)
        with file.open("rb") as handle:
            data = handle.read(limit)
        if b"\x00" in data[:8192]:
            raise FileError(f"{file.name} looks like a binary file, not text")
        text = decode(data).replace("\r\n", "\n").replace("\r", "\n")
        cut = len(data) < size or len(text) > self.config.max_read_chars
        result: dict[str, Any] = {
            "path": str(file),
            "content": text[: self.config.max_read_chars],
            "size": size_text(size),
        }
        if cut:
            result["note"] = f"only the first {self.config.max_read_chars} characters"
        return result

    @tool(name="list")
    def list_files(self, folder: str = "") -> dict[str, Any]:
        """The files in the Starlight folder (or one of its subfolders), newest first."""
        base = self.output.joinpath(*self._relative_parts(folder))
        if not base.is_dir():
            return {"folder": str(base), "files": []}
        found: list[tuple[float, Path, int]] = []
        for number, file in enumerate(base.rglob("*")):
            if number >= 20_000:
                break
            if file.is_file() and not _is_temp(file):
                stat = file.stat()
                found.append((stat.st_mtime, file, stat.st_size))
        found.sort(key=lambda item: item[0], reverse=True)
        shown = found[:100]
        result: dict[str, Any] = {
            "folder": str(base),
            "files": [
                {
                    "name": self._where(file),
                    "size": size_text(size),
                    "modified": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                }
                for mtime, file, size in shown
            ],
        }
        if len(found) > len(shown):
            result["more"] = len(found) - len(shown)
        return result

    @tool
    def locations(self) -> dict[str, Any]:
        """Where files can go: the Starlight folder and trusted folders (no question), and
        the user's Desktop, Documents and Downloads (saving there asks first)."""
        return {
            "starlight_folder": str(self.output),
            "trusted_folders": [str(f) for f in self.trusted],
            **{name: str(path) for name, path in known_folders().items()},
            "home": str(Path.home()),
        }


def _ends_with_newline(file: Path) -> bool:
    with file.open("rb") as handle:
        handle.seek(-1, os.SEEK_END)
        return handle.read(1) in (b"\n", b"\r")


def _is_temp(file: Path) -> bool:
    return file.name.startswith(".") and file.name.endswith(".tmp")


def _write_new(dest: Path, text: str, encoding: str) -> None:
    """Write a whole file at once: a crash never leaves half a file behind."""
    temp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temp.open("w", encoding=encoding) as handle:
            handle.write(text)
        os.replace(temp, dest)
    finally:
        temp.unlink(missing_ok=True)
