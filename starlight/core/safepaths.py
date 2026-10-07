"""Where modules may read and write your files.

One rule for every module that touches paths you name in the chat (files, pdf, filesearch,
office, docs, ...): Starlight's own folder - its settings, secrets index and data - is
always off-limits, a bare name lands in your Starlight folder (``output.folder``), and a
path elsewhere is used as given but is not "free": the module asks before writing there.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from starlight.core.tools import ToolError


def expand(text: str) -> Path:
    """A path as typed: %VARS%/$VARS and ~ expanded, made absolute."""
    return Path(os.path.expandvars(text.strip())).expanduser().resolve()


def inside(path: Path, folder: Path) -> bool:
    return path == folder or path.is_relative_to(folder)


@dataclass(frozen=True)
class UserPath:
    path: Path
    free: bool  # inside the Starlight folder or a trusted folder: no need to ask first


class PathGuard:
    """Resolves the paths a user gives a module, keeping Starlight's own folder out."""

    def __init__(
        self,
        root: Path,
        output: Path,
        trusted: Iterable[Path] = (),
        error: type[ToolError] = ToolError,
    ) -> None:
        self.root = root.resolve()  # Starlight's own folder
        self.output = output.resolve()  # the Starlight folder for your files
        self.trusted = [t.resolve() for t in trusted]
        self.error = error

    def is_starlight(self, path: Path) -> bool:
        return inside(path, self.root)

    def refuse_starlight(self, path: Path) -> None:
        if self.is_starlight(path):
            raise self.error(
                f"{path} is inside Starlight's own folder ({self.root}), which holds its "
                f"settings and data and is off-limits. Use the Starlight folder "
                f"({self.output}) or somewhere else."
            )

    def is_free(self, path: Path) -> bool:
        return any(inside(path, folder) for folder in (self.output, *self.trusted))

    def resolve(self, text: str, clean: Callable[[str], str] | None = None) -> UserPath:
        """A bare or relative name goes in the Starlight folder ('..' refused); a full path
        is used as given. ``clean`` tidies each name part (e.g. strips illegal characters)."""
        tidy = clean or (lambda part: part)
        raw = os.path.expandvars(text.strip())
        if not raw:
            raise self.error("give a file name, e.g. 'notes.txt'")
        given = Path(raw).expanduser()
        if given.is_absolute():
            normal = Path(os.path.normpath(given))
            path = Path(normal.anchor, *(tidy(p) for p in normal.parts[1:]))
        else:
            parts = [p for p in re.split(r"[\\/]+", raw) if p not in ("", ".")]
            if ".." in parts:
                raise self.error(
                    "'..' is not allowed in a name; give a full path for a folder outside "
                    "the Starlight folder"
                )
            if not parts:
                raise self.error("give a file name, e.g. 'notes.txt'")
            path = self.output.joinpath(*(tidy(p) for p in parts))
        path = path.resolve()
        self.refuse_starlight(path)
        return UserPath(path, self.is_free(path))
