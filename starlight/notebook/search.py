"""Searching the notes (docs/plan-notes-app.md, block E1).

What you can type:
    milk bread          notes with both words (the last may be the start of a word: "bre")
    "oat milk"          these words, one after the other
    -cake  -"old list"  notes without that word or those words
    tag:#work           notes with that tag (or one below it: #work/projects); also tag:work
    path:Clippings      notes in that folder, or whose path starts so (pfad: works too)
    -tag:done  -path:Journal
Upper and lower case do not matter, nor do accents ("muller" finds "Müller"; without
FTS5 accents count).

The full-text index (SQLite FTS5) finds and ranks the notes, words in the title counting
more; without FTS5 the same words are found by a plain scan. Each result has a few words of
the note around the first hit, and where the hits are in it and in the title, so the page
can mark them.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

WORD = re.compile(r"\w+")
TOKEN = re.compile(r'(-?)(?:(tag|path|pfad|schlagwort)\s*:\s*)?(?:"([^"]*)"?|(\S+))', re.I)
FILTERS = {"tag": "tag", "schlagwort": "tag", "path": "path", "pfad": "path"}
AROUND = 70  # characters of the note shown on each side of the first hit


@dataclass
class Query:
    words: list[str] = field(default_factory=list)  # all of them (the last as a prefix)
    phrases: list[str] = field(default_factory=list)  # "exact words"
    without: list[str] = field(default_factory=list)  # -word, -"some words"
    tags: list[str] = field(default_factory=list)
    not_tags: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    not_paths: list[str] = field(default_factory=list)

    @property
    def terms(self) -> list[str]:
        """What is looked for in the text (and marked in the results)."""
        return [*self.phrases, *self.words]

    @property
    def empty(self) -> bool:
        return not (self.words or self.phrases or self.tags or self.paths)


def parse(text: str) -> Query:
    """The parts of a search (see the module's notes)."""
    query = Query()
    for match in TOKEN.finditer(text):
        negative, kind, quoted, plain = match.groups()
        value = quoted if quoted is not None else plain or ""
        kind = FILTERS.get((kind or "").lower(), "")
        if kind == "tag":
            tag = value.strip().lstrip("#").strip("/")
            if tag:
                (query.not_tags if negative else query.tags).append(tag)
        elif kind == "path":
            path = value.strip().replace("\\", "/").strip("/")
            if path:
                (query.not_paths if negative else query.paths).append(path)
        elif quoted is not None:
            phrase = " ".join(WORD.findall(value))
            if phrase:
                (query.without if negative else query.phrases).append(phrase)
        else:
            words = WORD.findall(value)
            if negative:
                query.without.extend(words)
            else:
                query.words.extend(words)
    return query


def fold(text: str) -> str:
    """``text`` in lower case and without accents, letter for letter (the same length, so a
    place in one is the same place in the other)."""
    out = []
    for char in text:
        base = unicodedata.normalize("NFD", char)[:1] or char
        out.append((base.lower() or base)[:1])
    return "".join(out)


def _pattern(term: str, prefix: bool) -> re.Pattern[str]:
    """Where ``term`` (folded) stands in folded text: at the start of a word; a phrase with
    any spaces or marks between its words; the whole word unless ``prefix``."""
    words = [re.escape(fold(w)) for w in term.split()]
    end = "" if prefix else r"(?!\w)"
    return re.compile(r"(?<!\w)" + r"\W+".join(words) + end)


def marks(text: str, query: Query) -> list[tuple[int, int]]:
    """Where the query's words are in ``text``: (start, end), in order, not overlapping."""
    folded = fold(text)
    found: list[tuple[int, int]] = []
    for term in query.terms:
        for hit in _pattern(term, prefix=True).finditer(folded):
            found.append((hit.start(), hit.end()))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(found):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


# the marks at the start of a line: "# " headings, "- [ ] " lists and tasks, "> " quotes
LINE_MARKS = re.compile(
    r"(?m)^[ \t]*(?:#{1,6}[ \t]+|(?:[-*+]|\d{1,9}[.)])[ \t]+(?:\[.\][ \t]+)?|>[ \t]?)"
)

INLINE_MARKS = re.compile(r"\*\*|__|`+")  # bold and code marks
CALLOUT = re.compile(r"\[![\w-]+\][+-]?[ \t]*")  # > [!tip]
INLINE_LINK = re.compile(r"!?\[\[([^\]|\n]*\|)?([^\]\n]*)\]\]")  # [[a|b]]: b; [[a]]: a


def snippet(body: str, query: Query) -> tuple[str, list[tuple[int, int]]]:
    """A few words of ``body`` around the first hit, and where the hits are in them."""
    body = LINE_MARKS.sub("", body)
    body = INLINE_LINK.sub(lambda m: m.group(2), CALLOUT.sub("", INLINE_MARKS.sub("", body)))
    hits = marks(body, query)
    at = hits[0][0] if hits else 0
    start, end = max(0, at - AROUND), min(len(body), at + AROUND)
    if start:  # (whole words)
        space = body.find(" ", start, at)
        start = space + 1 if space >= 0 else start
    if end < len(body):
        space = body.rfind(" ", at, end)
        end = space if space > at else end
    text = " ".join(body[start:end].split())
    text = ("…" if start else "") + text + ("…" if end < len(body) else "")
    return text, marks(text, query)


@dataclass(frozen=True)
class Result:
    path: str
    title: str
    snippet: str
    marks: list[tuple[int, int]]  # in the snippet
    title_marks: list[tuple[int, int]]

    def public(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "snippet": self.snippet,
            "marks": [list(m) for m in self.marks],
            "title_marks": [list(m) for m in self.title_marks],
        }


def _fts_quote(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def fts_query(query: Query) -> str:
    """The FTS5 MATCH text: every word and phrase quoted (so nothing in it is an operator),
    the last plain word as a prefix, then NOT for each left-out one. Empty when nothing is
    looked for (NOT cannot stand alone)."""
    parts = [_fts_quote(w) for w in query.words[:-1]]
    if query.words:
        parts.append(_fts_quote(query.words[-1]) + "*")
    parts += [_fts_quote(p) for p in query.phrases]
    if not parts:
        return ""
    return " ".join(parts) + "".join(f" NOT {_fts_quote(w)}" for w in query.without)


def _contains(folded: str, query: Query) -> bool:
    """The plain scan's test: every term there (the last word as a prefix), none left out."""
    wanted = [(_pattern(p, False), True) for p in query.phrases]
    wanted += [(_pattern(w, n == len(query.words) - 1), True) for n, w in enumerate(query.words)]
    wanted += [(_pattern(w, False), False) for w in query.without]
    return all(bool(p.search(folded)) == present for p, present in wanted)


def search(index: Any, text: str, limit: int = 20) -> list[Result]:
    """The notes that match ``text`` (see the module's notes), best first. ``index`` is the
    notebook index (``starlight.notebook.index.Index``), brought up to date by the caller."""
    query = parse(text)
    if query.empty:
        return []
    allowed = _allowed(index, query)
    match = fts_query(query) if index.fts else ""
    if match:  # FTS5 finds, leaves out and ranks
        paths = [p for p in index.fts_paths(match) if allowed is None or p in allowed]
        found = index.bodies(paths[:limit])
        rows = [(p, *found[p]) for p in paths[:limit] if p in found]
    else:  # a plain scan (no FTS5, or only tag: and path:)
        words = [w.casefold() for term in query.terms for w in term.split()]
        paths = index.scan_paths(words) if words else sorted(allowed or ())
        found = index.bodies([p for p in paths if allowed is None or p in allowed])
        rows = [
            (p, *found[p]) for p in found if _contains(fold(f"{found[p][0]}\n{found[p][1]}"), query)
        ]
        rows.sort(key=lambda r: _rank(r, query) if query.terms else (0, 0, r[0].casefold()))
    results = []
    for path, title, body in rows[:limit]:
        shown, where = snippet(body, query)
        results.append(Result(path, title, shown, where, marks(title, query)))
    return results


def _rank(row: tuple[str, str, str], query: Query) -> tuple[int, int, str]:
    """Without FTS5: words in the title first, then how often they are in the note."""
    title, body = fold(row[1]), fold(row[2])
    in_title = sum(bool(_pattern(t, True).search(title)) for t in query.terms)
    count = sum(len(_pattern(t, True).findall(body)) for t in query.terms)
    return (-in_title, -count, row[0].casefold())


def _allowed(index: Any, query: Query) -> set[str] | None:
    """The notes the tag: and path: parts allow (None: all of them)."""
    if not (query.tags or query.not_tags or query.paths or query.not_paths):
        return None
    paths = {info.path for info in index.notes()}
    for tag in query.tags:
        paths &= set(index.with_tag(tag))
    for tag in query.not_tags:
        paths -= set(index.with_tag(tag))

    def under(path: str, start: str) -> bool:  # ("Clip" is enough for Clippings/...)
        return fold(path).startswith(fold(start))

    if query.paths:
        paths = {p for p in paths if any(under(p, s) for s in query.paths)}
    return {p for p in paths if not any(under(p, s) for s in query.not_paths)}
