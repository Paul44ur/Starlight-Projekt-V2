"""Which file a link means (docs/plan-notes-app.md, section 3).

``[[Shopping]]`` means the note called Shopping, wherever it is; ``[[Recipes/Pancakes]]``
the one in a folder Recipes; ``[[photo.png]]`` that file. Names compare without case (as on
Windows). When several notes have the name: the one in the linking note's own folder, else
the one with the shortest path (then the first in alphabetical order). A note's ``aliases``
are other names for it; its real name wins over another note's alias.

Paths here are relative to the notebook folder and written with "/" ("Notes/Shopping.md").
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import quote


def key(name: str) -> str:
    """A name as compared: without case, the same however its letters were composed."""
    return unicodedata.normalize("NFC", name).strip().casefold()


def _folder(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _stem(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name[:-3] if name.casefold().endswith(".md") else name


def clean(target: str) -> str:
    """A link target as a path: "/" between folders, no leading "./" or "/"."""
    target = target.replace("\\", "/").strip()
    while target.startswith(("./", "/")):
        target = target[2:] if target.startswith("./") else target[1:]
    return target


@dataclass(frozen=True)
class Known:
    """What resolving needs to know about one file of the notebook."""

    path: str  # "Notes/Shopping.md", "Notes/Attachments/photo.png"
    aliases: tuple[str, ...] = ()


class Resolver:
    def __init__(self, files: Iterable[Known]) -> None:
        self.paths: set[str] = set()
        self.by_name: dict[str, list[str]] = {}  # a note's name, or a file's whole name
        self.by_alias: dict[str, list[str]] = {}
        for known in files:
            self.paths.add(known.path)
            name = known.path.rsplit("/", 1)[-1]
            self.by_name.setdefault(key(name), []).append(known.path)
            if name.casefold().endswith(".md"):
                self.by_name.setdefault(key(_stem(known.path)), []).append(known.path)
            for alias in known.aliases:
                self.by_alias.setdefault(key(alias), []).append(known.path)

    def resolve(self, target: str, source: str | None = None) -> str | None:
        """The path ``target`` means, seen from the note ``source``; None when there is no
        such file. An empty target is the source itself (``[[#Heading]]``)."""
        target = clean(target)
        if not target:
            return source
        folder, _, name = target.rpartition("/")
        found = list(dict.fromkeys(self.by_name.get(key(name), [])))
        if folder:  # only those inside a folder of that name (or path)
            wanted = key(folder).split("/")
            found = [p for p in found if key(_folder(p)).split("/")[-len(wanted) :] == wanted]
        elif not found:
            found = list(dict.fromkeys(self.by_alias.get(key(name), [])))
        if not found:
            return None
        here = _folder(source) if source is not None else None
        return min(found, key=lambda p: (_folder(p) != here, p.count("/"), key(p), p))

    def names_of(self, path: str) -> set[str]:
        """The names a link to ``path`` may use (compared as ``key``): its name, its whole
        file name and its aliases."""
        names = {key(path.rsplit("/", 1)[-1]), key(_stem(path))}
        names.update(a for a, paths in self.by_alias.items() if path in paths)
        return names


def retarget(inside: str, new_name: str) -> str:
    """The inside of a [[link]] pointing at ``new_name`` instead: a folder written in the link,
    the part after "#" and the shown text after "|" stay as they were."""
    head = re.match(r"[^#|]*", inside)
    target = head.group(0) if head else ""
    rest = inside[len(target) :]
    escaped = target.endswith("\\")  # "[[Note\\|text]]" in a table
    bare = target[:-1] if escaped else target
    folder, slash, _ = bare.rpartition("/")
    suffix = ".md" if bare.rstrip().casefold().endswith(".md") else ""
    return (folder + slash if slash else "") + new_name + suffix + ("\\" if escaped else "") + rest


def deep_link(path: str, heading: str = "") -> str:
    """A link that opens the note from other programs, a mail or an automation's message
    (block G6): ``starlight://note/Notes/Plan.md``. Starlight.exe opens it (see
    launcher/starlight_launcher.py, link_target); the pages show it as a link to the note."""
    link = "starlight://note/" + quote(path, safe="/")
    return link + ("#" + quote(heading, safe="") if heading else "")
