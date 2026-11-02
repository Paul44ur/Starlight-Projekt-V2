"""Notes that belong together (docs/plan-notes-app.md, block E2).

* ``by_structure``: the notes most related to one by what connects them: links either way
  and shared tags. (By meaning, through the knowledge base's vectors, is ``docs.similar``;
  this is what is used without them.)
* ``mentions``: notes that name this one (its name or an alias) in their text without
  linking to it, line by line.
* ``link_mention``: turns one such mention into a link (``[[Name]]``, or ``[[Name|as
  written]]``), changing only that line; a version first, refused when the line changed.

Names in code, in links and in web addresses are not mentions, and neither is anything in
a note's properties or in a clipping (a note with ``source:``: outside content stays as it
was). Names shorter than three letters are left out (too many false hits).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlight.notebook import markdown, search
from starlight.notebook.history import History
from starlight.notebook.write import NoteConflict, Written, read_for_change, write_note

LINK_WEIGHT = 3  # a link either way counts this much
TAG_WEIGHT = 2  # each shared tag
MAX_MENTIONS = 50


def by_structure(index: Any, path: str, limit: int = 5) -> list[dict[str, Any]]:
    """The notes most connected to ``path``: [{path, title, score, why}], best first."""
    scores: dict[str, int] = {}
    why: dict[str, list[str]] = {}

    def add(other: str | None, points: int, reason: str) -> None:
        if not other or other == path or not other.casefold().endswith(".md"):
            return
        scores[other] = scores.get(other, 0) + points
        if reason not in why.setdefault(other, []):
            why[other].append(reason)

    for link in index.links(path):
        add(link["to"], LINK_WEIGHT, "linked from here")
    for link in index.backlinks(path):
        add(link["from"], LINK_WEIGHT, "links here")
    for tag in index.tags_of(path):
        for other in index.with_tag(tag):
            add(other, TAG_WEIGHT, "#" + tag)
    titles = {info.path: info.title for info in index.notes()}
    ranked = sorted(scores, key=lambda p: (-scores[p], p.casefold()))
    return [
        {"path": p, "title": titles.get(p, Path(p).stem), "score": scores[p], "why": why[p]}
        for p in ranked[:limit]
        if p in titles
    ]


def by_meaning(index: Any, path: str, similar: Any, limit: int = 5) -> list[dict[str, Any]]:
    """``docs.similar``'s answer for the note ``path`` as related notes (only notes in the
    notebook; other files of the knowledge base are left out)."""
    root = Path(index.root).resolve()
    titles = {info.path: info.title for info in index.notes()}
    found: list[dict[str, Any]] = []
    for item in (similar or {}).get("similar", []) if isinstance(similar, dict) else []:
        try:
            other = Path(str(item.get("file", ""))).resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if other != path and other in titles and other.casefold().endswith(".md"):
            score = item.get("score")
            found.append(
                {"path": other, "title": titles[other], "score": score, "why": ["similar"]}
            )
            if len(found) >= limit:
                break
    return found


def names(index: Any, path: str) -> list[str]:
    """What a mention of ``path`` may say: its name and its aliases (three letters or more)."""
    info = index.note(path)
    found = [Path(path).stem, *(info.aliases if info else [])]
    return [n for n in dict.fromkeys(found) if len(search.fold(n).strip()) >= 3]


MASKS = (markdown.INLINE_CODE, markdown.COMMENT, markdown.WIKILINK, markdown.MDLINK, markdown.URL)


def spans(line: str, said: list[str]) -> list[tuple[int, int]]:
    """Where any of ``said`` stands in ``line`` as whole words, outside code, links and web
    addresses: (start, end), in order."""
    masked = line
    for pattern in MASKS:
        masked = pattern.sub(lambda m: " " * (m.end() - m.start()), masked)
    folded = search.fold(masked)
    found = sorted(
        (hit.start(), hit.end())
        for name in said
        for hit in search.word_pattern(name, prefix=False).finditer(folded)
    )
    kept: list[tuple[int, int]] = []
    for start, end in found:  # (the longer of two at one place)
        if kept and start < kept[-1][1]:
            continue
        kept.append((start, end))
    return kept


@dataclass(frozen=True)
class Mention:
    path: str  # the note that names it
    title: str
    line: int  # from 0
    text: str  # the line as it is
    start: int  # where the name is in it
    end: int

    def public(self) -> dict[str, Any]:
        return self.__dict__.copy()


def mentions(index: Any, path: str) -> list[Mention]:
    """The lines of other notes that name ``path`` without linking to it."""
    from starlight.text import decode_text

    said = names(index, path)
    if not said:
        return []
    skip = {path, *(b["from"] for b in index.backlinks(path)), *index.with_property("source")}
    candidates: dict[str, str] = {}  # path -> title
    for name in said:
        for result in search.search(index, f'"{name}"', limit=200):
            if result.path not in skip:
                candidates.setdefault(result.path, result.title)
    found: list[Mention] = []
    for other, title in sorted(candidates.items(), key=lambda c: c[0].casefold()):
        try:
            text = decode_text((Path(index.root) / other).read_bytes())
        except OSError:
            continue
        note = markdown.parse(text)
        code = {n for block in note.code for n in range(block.line, block.end + 1)}
        for number, line in enumerate(text.splitlines()):
            if number < note.body_line or number in code:
                continue
            where = spans(line, said)
            if where:
                found.append(Mention(other, title, number, line, *where[0]))
                if len(found) >= MAX_MENTIONS:
                    return found
    return found


def link_name(index: Any, path: str, source: str) -> str:
    """How a link from ``source`` to ``path`` is written: its name, or its path when the name
    alone would lead elsewhere."""
    name = Path(path).stem
    return name if index.resolve(name, source) == path else re.sub(r"\.md$", "", path)


def link_mention(
    index: Any,
    history: History | None,
    *,
    source: str,
    line: int,
    expected: str,
    target: str,
) -> Written:
    """Link the first mention of ``target`` on ``line`` (from 0) of the note ``source``
    (paths in the notebook). The line must still read ``expected``."""
    file = Path(index.root) / source
    read = read_for_change(file)
    lines = read.text.splitlines(keepends=True)
    if not 0 <= line < len(lines) or lines[line].rstrip("\r\n") != expected.rstrip("\r\n"):
        raise NoteConflict(f"that line of {file.stem} was changed meanwhile; look again")
    body = lines[line].rstrip("\r\n")
    where = spans(body, names(index, target))
    if not where:
        raise NoteConflict(f"{Path(target).stem} is not named on that line of {file.stem}")
    start, end = where[0]
    name, said = link_name(index, target, source), body[start:end]
    link = f"[[{name}]]" if said == name else f"[[{name}|{said}]]"
    lines[line] = body[:start] + link + body[end:] + lines[line][len(body) :]
    return write_note(
        file, read.bom + "".join(lines), "notes.link mention", history,
        expected=read.stamp, newline=read.newline,
    )  # fmt: skip
