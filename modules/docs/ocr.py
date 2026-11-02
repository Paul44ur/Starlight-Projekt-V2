"""Reading the text in pictures and scanned PDF pages (docs/plan-notes-app.md, block E2), so a
photo of a receipt or a scanned letter is findable in the knowledge base.

It uses Tesseract, a free OCR program, when it is installed (Windows: the installer from
the UB Mannheim puts it in C:\\Program Files\\Tesseract-OCR). Without it, pictures are left
out and scanned pages stay without text, as before. Limits: pictures up to a size, a number
of scanned pages per PDF, and a time per picture.
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol

PICTURES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
WINDOWS_DEFAULT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
QUIET = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # (no console window flashing up)


class Reader(Protocol):
    def read(self, picture: Path) -> str: ...


def find_tesseract(command: str = "") -> str | None:
    """The Tesseract program: ``command`` when given, else on PATH, else where its Windows
    installer puts it. None when it is not there."""
    if command:
        return command if Path(command).is_file() or shutil.which(command) else None
    found = shutil.which("tesseract")
    if found:
        return found
    if sys.platform == "win32" and WINDOWS_DEFAULT.is_file():
        return str(WINDOWS_DEFAULT)
    return None


class Tesseract:
    """Tesseract run as a program (no Python package needed)."""

    def __init__(self, command: str, languages: str = "deu+eng", seconds: float = 60) -> None:
        self.command = command
        self.seconds = seconds
        installed = self._languages()
        wanted = [lang for lang in languages.split("+") if lang.strip()]
        self.languages = "+".join(lang for lang in wanted if lang in installed)

    def _languages(self) -> set[str]:
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            listed = subprocess.run(
                [self.command, "--list-langs"],
                capture_output=True, timeout=20, creationflags=QUIET, check=False,
            )  # fmt: skip
            lines = listed.stdout.decode("utf-8", errors="replace").splitlines()
            return {line.strip() for line in lines[1:] if line.strip()}
        return set()

    def read(self, picture: Path) -> str:
        args = [self.command, str(picture), "stdout"]
        if self.languages:
            args += ["-l", self.languages]
        done = subprocess.run(
            args, capture_output=True, timeout=self.seconds, creationflags=QUIET, check=False
        )
        return done.stdout.decode("utf-8", errors="replace").strip()


def read_picture(reader: Reader, data: bytes, suffix: str) -> str:
    """The text in a picture given as bytes (from a PDF page), through a temporary file."""
    with tempfile.TemporaryDirectory(prefix="starlight-ocr-") as folder:
        picture = Path(folder) / f"page{suffix or '.png'}"
        picture.write_bytes(data)
        return reader.read(picture)
