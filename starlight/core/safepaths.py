"""Where modules may read and write your files.

One rule for every module that touches paths you name in the chat (files, pdf, filesearch,
office, docs, ...): Starlight's own folder - its settings, secrets index and data - is
off-limits, a bare name lands in your Starlight folder (``output.folder``), and a path
elsewhere is used as given but is not "free": the module asks before writing there. With
``output.write_anywhere`` a *new* file may be created anywhere without asking
(``UserPath.new_free``); changing or reading an existing file elsewhere still asks.

Three of these are safety limits you can switch (Settings -> Safety limits, core/safety.py):
``output.protect_starlight_folder``, ``output.ask_outside`` and ``output.ask_runnable``.
The kernel hands this module its running output settings (use_rules), so a change on the
page applies at once; without a running kernel all three are on.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from starlight.core.tools import ToolError

_rules: object | None = None  # the running Starlight's OutputConfig (use_rules)


def use_rules(output: object | None) -> None:
    """Follow these output settings (the kernel's, while it runs; None: all protections on)."""
    global _rules
    _rules = output


def protects_starlight() -> bool:
    """Starlight's own folder is off-limits (output.protect_starlight_folder)."""
    return bool(getattr(_rules, "protect_starlight_folder", True))


def asks_outside() -> bool:
    """Reading or changing a file outside the free folders asks (output.ask_outside)."""
    return bool(getattr(_rules, "ask_outside", True))


def asks_runnable() -> bool:
    """Programs and scripts: saving one elsewhere asks, opening one is refused."""
    return bool(getattr(_rules, "ask_runnable", True))


def expand(text: str) -> Path:
    """A path as typed: %VARS%/$VARS and ~ expanded, made absolute."""
    return Path(os.path.expandvars(text.strip())).expanduser().resolve()


def inside(path: Path, folder: Path) -> bool:
    return path == folder or path.is_relative_to(folder)


@dataclass(frozen=True)
class UserPath:
    path: Path
    free: bool  # inside the Starlight folder or a trusted folder: no need to ask first
    new_free: bool = False  # a new file may be created here without asking first


class PathGuard:
    """Resolves the paths a user gives a module, keeping Starlight's own folder out."""

    def __init__(
        self,
        root: Path,
        output: Path,
        trusted: Iterable[Path] = (),
        error: type[ToolError] = ToolError,
        anywhere: bool = False,
    ) -> None:
        self.root = root.resolve()  # Starlight's own folder
        self.output = output.resolve()  # the Starlight folder for your files
        self.trusted = [t.resolve() for t in trusted]
        self.error = error
        self.anywhere = anywhere  # output.write_anywhere: new files anywhere without asking

    def is_starlight(self, path: Path) -> bool:
        """Inside Starlight's own folder, while that is off-limits."""
        return protects_starlight() and inside(path, self.root)

    def refuse_starlight(self, path: Path) -> None:
        if self.is_starlight(path):
            raise self.error(
                f"{path} is inside Starlight's own folder ({self.root}), which holds its "
                f"settings and data and is off-limits. Use the Starlight folder "
                f"({self.output}) or somewhere else."
            )

    def in_free_folder(self, path: Path) -> bool:
        """In the Starlight folder or a trusted one."""
        return any(inside(path, folder) for folder in (self.output, *self.trusted))

    def is_free(self, path: Path) -> bool:
        """No question needed: the Starlight folder, a trusted one, or anywhere while
        output.ask_outside is off."""
        if not asks_outside():
            return not self.is_starlight(path)
        return self.in_free_folder(path)

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
        free = self.is_free(path)
        return UserPath(path, free, free or self.anywhere)
