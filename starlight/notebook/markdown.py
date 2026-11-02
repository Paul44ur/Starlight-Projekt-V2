"""Reading a note's Markdown (docs/plan-notes-app.md, section 2). Pure functions, no files.

What it finds: the properties block at the top (``--- … ---``), headings and sections,
links (``[[Note]]``, ``[[Note|text]]``, ``[[Note#Heading]]``, ``[[Note#^id]]``, embeds
``![[…]]`` and Markdown links to local files), tags (``#tag``, ``#work/projects`` and the
``tags`` property), block ids (``^id``), tasks with their plain-word fields (``due:``,
``priority:``, ``repeat:``, ``start:``, ``done:``, ``status:``, and the German keys),
``query`` blocks and flashcard lines (``Question::Answer``; ``:::`` asks both ways; block G3
uses them in notes tagged #flashcards).

Code is not text: nothing inside fenced code blocks or `inline code` counts, and neither does
anything inside a web address (``https://example.org/#top`` has no tag) or an escaped
character (``\\#1``). Positions are kept: masked parts are replaced by spaces of the same
length, so what is found is cut from the original line.

Property edits (``set_property``, ``remove_property``) change only the lines of that one
property; comments, the order of keys, the line endings and the rest of the note stay.

Dates in tasks: ``natural_date`` reads "tomorrow", "Freitag", "in 3 days", "24.12." and ISO
dates; ``iso_dates`` writes a note's typed dates as ISO dates (Starlight and the Notes page
do that when they save).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import yaml

MAX_PROPERTIES = 64 * 1024  # a properties block bigger than this is not read

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)")
COMMENT = re.compile(r"%%.*?%%")  # a comment that stays in the file and is never shown
WIKILINK = re.compile(r"(?<!\\)(!?)\[\[([^\[\]\n]+?)\]\]")
MDLINK = re.compile(
    r"(?<!\\)(!?)\[([^\]\n]*)\]\(\s*(<[^>\n]*>|[^()\s]*(?:\([^()\s]*\)[^()\s]*)*)"
    r"(?:\s+\"[^\"\n]*\")?\s*\)"
)
URL = re.compile(r"\b(?:[A-Za-z][A-Za-z0-9+.\-]*://|mailto:|www\.)[^\s<>]+")
ESCAPED = re.compile(r"\\.")
TAG = re.compile(r"(?<![\w#&/\\])#([\w/\-]+)")
BLOCK_ID = re.compile(r"(?:^|\s)\^([A-Za-z0-9\-]+)[ \t]*$")
TASK = re.compile(r"^([ \t]*)(?:[-*+]|\d{1,9}[.)])[ \t]+\[(.)\](?=[ \t]|$)(.*)$")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# a flashcard line: "Question::Answer", or ":::" for both ways (exactly two or three colons)
CARD = re.compile(r"(?<!:):{2,3}(?!:)")
CARD_LEAD = re.compile(r"^[ \t]*(?:>[ \t]*)*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)?")  # list, quote

# Task fields in plain words: the key as written (English or German) -> the field.
TASK_KEYS = {
    "due": "due",
    "fällig": "due",
    "faellig": "due",
    "start": "start",
    "beginn": "start",
    "done": "done",
    "erledigt": "done",
    "priority": "priority",
    "priorität": "priority",
    "prioritaet": "priority",
    "repeat": "repeat",
    "wiederholen": "repeat",
    "wiederholung": "repeat",
    "status": "stage",  # open or doing (the Kanban columns, block G2); done is the [x]
}
FIELD = re.compile(r"(?<!\S)(" + "|".join(TASK_KEYS) + r"):[ \t]*", re.IGNORECASE)
DATE_FIELDS = ("due", "start", "done")
# a date in more than one word, after a date field's key (natural_date reads it)
NATURAL_DATE = re.compile(
    r"(?:in|nach)[ \t]+\d{1,3}[ \t]+\w+"
    r"|(?:next|nächste[nrs]?|naechste[nrs]?|kommende[nrs]?|this|diese[nrs]?)[ \t]+\w+",
    re.IGNORECASE,
)
STAGE_PHRASE = re.compile(r"in[ \t]+(?:arbeit|progress)\b", re.IGNORECASE)  # two words
STAGES = {
    "open": "open", "offen": "open", "todo": "open", "in arbeit": "doing", "in progress": "doing",
    "doing": "doing", "läuft": "doing", "laeuft": "doing", "aktiv": "doing", "active": "doing",
    "wip": "doing", "started": "doing", "begonnen": "doing", "arbeit": "doing",
}  # fmt: skip
PRIORITIES = {
    "high": "high",
    "hoch": "high",
    "normal": "normal",
    "medium": "normal",
    "mittel": "normal",
    "low": "low",
    "niedrig": "low",
}


# -- the properties block ----------------------------------------------------------------------


class _Loader(yaml.SafeLoader):
    """yaml.safe_load without aliases (``*name``): a few lines of aliases can stand for
    gigabytes once the values are walked (the "billion laughs"), and a note may come from
    outside (a clipped web page)."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("aliases (*name) are not allowed in properties")
        return super().compose_node(parent, index)


def plain(value: Any, depth: int = 0) -> Any:
    """A property value as JSON can carry it: dates as ISO text, keys as text."""
    if depth > 20:
        return str(value)
    if isinstance(value, dt.datetime):
        return value.isoformat(timespec="minutes" if not value.second else "seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): plain(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [plain(v, depth + 1) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@dataclass(frozen=True)
class Front:
    properties: dict[str, Any]  # the properties (empty when none or unreadable)
    body: str  # the text after the properties block
    body_line: int  # the line (0-based) where the body starts
    present: bool = False  # the note has a properties block
    error: str = ""  # why it could not be read ("" when it could, or there is none)


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _bare(line: str) -> str:
    return line.rstrip("\r\n").rstrip()


def _block_end(lines: list[str]) -> int | None:
    """The line closing the properties block at the top, None when there is none."""
    if not lines or _bare(lines[0]) != "---":
        return None
    for number in range(1, len(lines)):
        if _bare(lines[number]) in ("---", "..."):
            return number
    return None


def split(text: str) -> Front:
    """The properties block and the rest. A block that is not a list of keys (a note that
    starts with a horizontal line, say) is not properties: the whole text is the body."""
    text = text.removeprefix("\ufeff")
    lines = _lines(text)
    end = _block_end(lines)
    if end is None:
        return Front({}, text, 0)
    raw = "".join(lines[1:end])
    body = "".join(lines[end + 1 :])
    if len(raw) > MAX_PROPERTIES:
        return Front({}, body, end + 1, True, "the properties are too long to read")
    try:
        loaded = yaml.load(raw, Loader=_Loader)  # a SafeLoader, without aliases
    except (yaml.YAMLError, RecursionError) as exc:
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return Front({}, body, end + 1, True, f"the properties could not be read: {reason}")
    if loaded is None:
        return Front({}, body, end + 1, True)
    if not isinstance(loaded, dict):
        return Front({}, text, 0)
    return Front(plain(loaded), body, end + 1, True)


def _list(value: Any) -> list[str]:
    """A property that is a list: ``[a, b]``, one value, or text with commas."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def aliases(properties: dict[str, Any]) -> list[str]:
    """Other names of the note (``aliases``, or ``alias``)."""
    return _list(properties.get("aliases", properties.get("alias")))


def property_tags(properties: dict[str, Any]) -> list[str]:
    """The ``tags`` property, without "#" (``tags: [a, b]``, ``tags: a, b`` or ``tags: a b``)."""
    raw = properties.get("tags", properties.get("tag"))
    found = []
    for item in _list(raw):
        for word in item.split() if not isinstance(raw, list) else [item]:
            word = word.strip().lstrip("#").strip()
            if word and not re.fullmatch(r"[\d/\-]+", word):
                found.append(word)
    return found


# -- what the body contains ---------------------------------------------------------------------


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    line: int


@dataclass(frozen=True)
class Link:
    target: str  # the note or file as written ("Shopping", "Projects/Plan", "photo.png");
    #              "" for a part of this same note ("[[#Heading]]")
    heading: str = ""  # after "#" ("Heading", or "Part#Subpart")
    block: str = ""  # after "#^"
    text: str = ""  # after "|": what is shown instead of the name
    embed: bool = False  # ![[…]]: shown inside the note
    line: int = 0
    wiki: bool = True  # [[…]] (False: a Markdown link [text](Note.md))


@dataclass(frozen=True)
class Tag:
    name: str  # as written, without "#" ("work/projects")
    line: int  # -1: from the tags property


@dataclass(frozen=True)
class Task:
    line: int
    indent: int  # spaces before the "-" (a tab counts as 4)
    status: str  # " " open, "x" done; other characters as written ("-" cancelled ...)
    text: str  # what to do, without the fields
    due: str = ""
    start: str = ""
    done: str = ""  # the day it was done
    priority: str = ""  # high, normal or low ("" when not given)
    repeat: str = ""  # "every month", "jeden Montag" ... as written
    stage: str = ""  # status: open or doing ("" when not given)
    # where each field is on the line: (field, key starts, value starts, value ends)
    spans: tuple[tuple[str, int, int, int], ...] = field(default=(), compare=False, repr=False)

    @property
    def open(self) -> bool:
        return self.status == " "

    def span(self, name: str) -> tuple[int, int, int] | None:
        """Where the field ``name`` ("due" ...) is on the line, or None."""
        for found, key, start, end in self.spans:
            if found == name:
                return key, start, end
        return None


@dataclass(frozen=True)
class Card:
    line: int
    question: str  # as written (Markdown), without a list or quote mark before it
    answer: str  # without a block id after it
    both_ways: bool = False  # ":::": also asked the other way round


@dataclass(frozen=True)
class CodeBlock:
    line: int  # the opening fence
    end: int  # the closing fence (the last line when it is never closed)
    info: str  # what follows the opening fence ("python", "query" ...)
    text: str  # the lines between the fences


@dataclass(frozen=True)
class Note:
    properties: dict[str, Any]
    error: str  # why the properties could not be read, or ""
    body: str
    body_line: int
    headings: list[Heading] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    tags: list[Tag] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    block_ids: dict[str, int] = field(default_factory=dict)  # id -> line
    code: list[CodeBlock] = field(default_factory=list)
    words: int = 0
    cards: list[Card] = field(default_factory=list)  # Question::Answer lines

    @property
    def aliases(self) -> list[str]:
        return aliases(self.properties)

    @property
    def queries(self) -> list[CodeBlock]:
        """The ``query`` blocks (E3 reads what is in them)."""
        return [b for b in self.code if b.info.split()[:1] == ["query"]]

    def tag_names(self) -> list[str]:
        """Each tag once, as first written (compared without case)."""
        seen: dict[str, str] = {}
        for tag in self.tags:
            seen.setdefault(tag.name.casefold(), tag.name)
        return list(seen.values())


def _blank(match: re.Match[str]) -> str:
    return " " * (match.end() - match.start())


def _link(inner: str, embed: bool, line: int) -> Link | None:
    target, bar, text = inner.partition("|")
    target = target.rstrip("\\").strip()  # (in a table "|" is written "\|")
    target, _, part = target.partition("#")
    heading = block = ""
    if part.startswith("^"):
        block = part[1:].strip()
    else:
        heading = part.strip()
    target = target.strip()
    if target.casefold().endswith(".md"):
        target = target[:-3]
    if not target and not heading and not block:
        return None
    return Link(target, heading, block, text.strip() if bar else "", embed, line)


def _markdown_link(match: re.Match[str], line: int) -> Link | None:
    """``[text](Note.md)`` or ``![](photo.png)``: a link to a local file, like ``[[…]]``.
    Web addresses, mail and links within the page (``#part``) are not."""
    target = match.group(3).strip().removeprefix("<").removesuffix(">").strip()
    if not target or target.startswith(("#", "/", "\\")) or SCHEME.match(target):
        return None
    target = unquote(target)
    target, _, part = target.partition("#")
    found = _link(f"{target}#{part}" if part else target, bool(match.group(1)), line)
    if found is None:
        return None
    return Link(
        found.target, found.heading, found.block, match.group(2).strip(), found.embed, line, False
    )


def _fields(text: str, masked: str) -> tuple[str, dict[str, str], list[tuple[str, int, int, int]]]:
    """A task's plain-word fields (``due: 2026-11-05 priority: high``), the rest, and where
    each field is. Keys are found in ``masked`` (so none inside code counts); values are cut
    from ``text``."""
    found: dict[str, str] = {}
    spans: list[tuple[str, int, int, int]] = []
    matches = list(FIELD.finditer(masked))
    keep, last = [], 0
    for number, match in enumerate(matches):
        name = TASK_KEYS[match.group(1).lower()]
        stop = matches[number + 1].start() if number + 1 < len(matches) else len(text)
        segment = text[match.end() : stop]
        phrase = NATURAL_DATE.match(segment) if name in DATE_FIELDS else None
        if name == "stage":
            phrase = STAGE_PHRASE.match(segment)
        if name == "repeat":  # words, up to the next field, tag or block id
            value = re.split(r"(?<!\S)[#^]\S", segment, maxsplit=1)[0].rstrip()
        elif phrase and (name == "stage" or natural_date(phrase.group(0), dt.date(2026, 1, 1))):
            value = phrase.group(0)  # "in 3 days", "next friday", "nächsten Freitag", "in Arbeit"
        else:  # one word; what follows stays text
            word = re.match(r"\S*", segment)
            value = word.group(0) if word else ""
        if not value or name in found:
            continue  # (an empty field stays text; the first of two counts)
        found[name] = value
        spans.append((name, match.start(), match.end(), match.end() + len(value)))
        keep.append(text[last : match.start()])
        last = match.end() + len(value)
    keep.append(text[last:])
    rest = re.sub(r"[ \t]{2,}", " ", "".join(keep)).strip()
    if "priority" in found:
        found["priority"] = PRIORITIES.get(found["priority"].casefold(), found["priority"])
    if "stage" in found:
        stage = " ".join(found["stage"].split()).casefold()
        found["stage"] = STAGES.get(stage, found["stage"])
    return rest, found, spans


def _keep_shown_text(match: re.Match[str]) -> str:
    """A Markdown link with its address blanked out: ``[text](     )``."""
    shown = match.group(0)[: match.end(2) - match.start() + 1]
    return shown.ljust(match.end() - match.start())


def parse(text: str) -> Note:
    """Everything in a note."""
    front = split(text)
    lines = _lines(text.removeprefix("\ufeff"))
    headings: list[Heading] = []
    links: list[Link] = []
    tags = [Tag(name, -1) for name in property_tags(front.properties)]
    tasks: list[Task] = []
    cards: list[Card] = []
    block_ids: dict[str, int] = {}
    code: list[CodeBlock] = []
    words = 0
    fence: tuple[str, int, str] | None = None  # (marker, line, info) of an open code block
    fenced: list[str] = []
    for number in range(front.body_line, len(lines)):
        line = lines[number].rstrip("\r\n")
        opening = FENCE.match(line)
        if fence is not None:
            marker = fence[0]
            # closed by a fence of the same kind, at least as long, with nothing after it
            if opening and opening.group(1).startswith(marker) and not opening.group(2).strip():
                code.append(CodeBlock(fence[1], number, fence[2], "".join(fenced)))
                fence, fenced = None, []
            else:
                fenced.append(line + "\n")
            continue
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            fence = (opening.group(1), number, opening.group(2).strip())
            continue
        masked = INLINE_CODE.sub(_blank, line)
        masked = COMMENT.sub(_blank, masked)
        for match in WIKILINK.finditer(masked):
            found = _link(line[match.start(2) : match.end(2)], bool(match.group(1)), number)
            if found is not None:
                links.append(found)
        masked = WIKILINK.sub(_blank, masked)
        for match in MDLINK.finditer(masked):
            found = _markdown_link(match, number)
            if found is not None:
                links.append(found)
        masked = MDLINK.sub(_keep_shown_text, masked)  # an address is not text
        masked = URL.sub(_blank, masked)
        masked = ESCAPED.sub(_blank, masked)
        heading = HEADING.match(masked)
        if heading:
            title = line[heading.start(2) : heading.end(2)] if heading.group(2) else ""
            headings.append(Heading(len(heading.group(1)), title.strip(), number))
        for match in TAG.finditer(masked):
            name = match.group(1).rstrip("/-")
            if name and not re.fullmatch(r"[\d/\-]+", name):
                tags.append(Tag(name, number))
        block = BLOCK_ID.search(masked)
        if block:
            block_ids.setdefault(block.group(1), number)
        task = TASK.match(masked)
        if task:
            start = task.start(3)
            text_part = line[start:]
            masked_part = masked[start:]
            if block:  # the block id is not part of what to do
                cut = block.start(1) - 1 - start
                text_part, masked_part = text_part[:cut], masked_part[:cut]
            rest, values, spans = _fields(text_part, masked_part)
            indent = len(task.group(1).expandtabs(4))
            placed = tuple((n, k + start, a + start, b + start) for n, k, a, b in spans)
            tasks.append(Task(number, indent, task.group(2), rest, **values, spans=placed))
        elif not heading and not masked.lstrip().startswith("|"):  # (not in a table either)
            card = _card(line, masked, number, block)
            if card is not None:
                cards.append(card)
        words += len(re.findall(r"\w+", masked))
    if fence is not None:  # never closed: code to the end, as editors show it
        code.append(CodeBlock(fence[1], len(lines) - 1, fence[2], "".join(fenced)))
    return Note(
        front.properties,
        front.error,
        front.body,
        front.body_line,
        headings,
        links,
        tags,
        tasks,
        block_ids,
        code,
        words,
        cards,
    )


def _card(line: str, masked: str, number: int, block: re.Match[str] | None) -> Card | None:
    """The flashcard on a line (``Question::Answer``), or None. The separator counts only
    in text: not in code, a link or a web address (those are masked)."""
    separator = CARD.search(masked)
    if separator is None:
        return None
    end = block.start(1) - 1 if block and block.start(1) > separator.end() else len(line)
    lead = CARD_LEAD.match(line)
    question = line[lead.end() if lead else 0 : separator.start()].strip()
    answer = line[separator.end() : end].strip()
    if not question or not answer:
        return None
    return Card(number, question, answer, len(separator.group(0)) == 3)


def wikilink_spans(text: str) -> list[tuple[int, int, str, bool]]:
    """Where each [[link]] is in ``text`` (not in code): (start, end, inside, embed), as
    offsets into ``text``. For changing links in place (a rename)."""
    found: list[tuple[int, int, str, bool]] = []
    front = split(text)
    offset = 1 if text.startswith("\ufeff") else 0
    fence: str | None = None
    for number, line in enumerate(_lines(text.removeprefix("\ufeff"))):
        start, bare = offset, line.rstrip("\r\n")
        offset += len(line)
        if number < front.body_line:
            continue
        opening = FENCE.match(bare)
        if fence is not None:
            if opening and opening.group(1).startswith(fence) and not opening.group(2).strip():
                fence = None
            continue
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            fence = opening.group(1)
            continue
        masked = COMMENT.sub(_blank, INLINE_CODE.sub(_blank, bare))
        for match in WIKILINK.finditer(masked):
            inside = bare[match.start(2) : match.end(2)]
            found.append((start + match.start(), start + match.end(), inside, bool(match.group(1))))
    return found


# -- dates in tasks ----------------------------------------------------------------------------

WEEKDAY_NAMES = {
    **{d: n for n, d in enumerate(("monday", "tuesday", "wednesday", "thursday", "friday",
                                   "saturday", "sunday"))},
    **{d: n for n, d in enumerate(("montag", "dienstag", "mittwoch", "donnerstag", "freitag",
                                   "samstag", "sonntag"))},
}  # fmt: skip
DAY_WORDS = {
    "today": 0, "heute": 0, "tomorrow": 1, "morgen": 1, "übermorgen": 2, "uebermorgen": 2,
    "yesterday": -1, "gestern": -1,
}  # fmt: skip
UNIT_DAYS = {"day": 1, "days": 1, "tag": 1, "tage": 1, "tagen": 1,
             "week": 7, "weeks": 7, "woche": 7, "wochen": 7}  # fmt: skip
UNIT_MONTHS = {"month": 1, "months": 1, "monat": 1, "monate": 1, "monaten": 1,
               "year": 12, "years": 12, "jahr": 12, "jahre": 12, "jahren": 12}  # fmt: skip


def add_months(day: dt.date, months: int) -> dt.date:
    """``day`` some months later; the 31st becomes the month's last day where it is shorter."""
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    following = dt.date(year + (month == 12), month % 12 + 1, 1)
    return dt.date(year, month, min(day.day, (following - dt.timedelta(days=1)).day))


def natural_date(value: str, today: dt.date) -> dt.date | None:
    """The day a task's date means, counted from ``today``: 2026-11-05, today, tomorrow,
    friday, next friday, in 3 days, in 2 weeks, next week (its Monday), 24.12. or
    24.12.2026; in German too (heute, morgen, übermorgen, Freitag, nächsten Freitag, in 3
    Tagen, nächste Woche). A weekday is the next one after today. None when it is not a
    date."""
    raw = " ".join(value.strip().casefold().split()).rstrip(",;")
    if ISO_DATE.match(raw):
        try:
            return dt.date.fromisoformat(raw)
        except ValueError:
            return None
    if raw in DAY_WORDS:
        return today + dt.timedelta(days=DAY_WORDS[raw])
    match = re.fullmatch(r"(?:in|nach) (\d{1,3}) (\w+)", raw)
    if match:
        count, unit = int(match.group(1)), match.group(2)
        if unit in UNIT_DAYS:
            return today + dt.timedelta(days=count * UNIT_DAYS[unit])
        if unit in UNIT_MONTHS:
            return add_months(today, count * UNIT_MONTHS[unit])
        return None
    match = re.fullmatch(
        r"(next|nächste[nrs]?|naechste[nrs]?|kommende[nrs]?|this|diese[nrs]?) (\w+)", raw
    )
    if match:
        coming, raw = not match.group(1).startswith(("this", "diese")), match.group(2)
        if coming and raw in ("week", "woche"):  # the Monday of next week
            return today + dt.timedelta(days=7 - today.weekday())
        if coming and raw in ("month", "monat"):
            return add_months(today.replace(day=1), 1)
        if raw not in WEEKDAY_NAMES:
            return None
    if raw in WEEKDAY_NAMES:
        return today + dt.timedelta(days=(WEEKDAY_NAMES[raw] - today.weekday()) % 7 or 7)
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", raw)
    if match:
        try:
            day = dt.date(
                int(match.group(3) or today.year), int(match.group(2)), int(match.group(1))
            )
        except ValueError:
            return None
        if not match.group(3) and day < today:  # 24.12. in January: this year's
            day = day.replace(year=day.year + 1)  # (and in late December: next year's)
        return day
    return None


def iso_dates(text: str, today: dt.date, before: str | None = None) -> str:
    """``text`` with the natural dates in its tasks (``due: tomorrow``, ``fällig:
    Freitag``) written as ISO dates (``due: 2026-11-03``), counted from ``today``. Only the
    date is changed, the rest of the line stays. With ``before`` (the note as it was), lines
    that were there already are left alone: their dates were written on another day."""
    lines = _lines(text)
    old = {line.rstrip("\r\n") for line in _lines(before or "")}
    changed = False
    for task in parse(text).tasks:
        line = lines[task.line]
        if line.rstrip("\r\n") in old:
            continue
        for name, _key, start, end in sorted(task.spans, key=lambda s: -s[2]):
            if name not in DATE_FIELDS or ISO_DATE.match(line[start:end]):
                continue
            day = natural_date(line[start:end], today)
            if day is not None:
                line = line[:start] + day.isoformat() + line[end:]
        if line != lines[task.line]:
            lines[task.line], changed = line, True
    return "".join(lines) if changed else text


# -- parts of a note (for embeds) ---------------------------------------------------------------


def section(text: str, heading: str) -> str | None:
    """The part of a note under ``heading`` (with the heading), up to the next heading of the
    same or a higher level. ``"Part#Subpart"`` looks for Subpart inside Part. None: not there.
    Compared without case."""
    note = parse(text)
    lines = _lines(text.removeprefix("\ufeff"))
    after, stop = -1, len(lines)  # look between these lines
    found: Heading | None = None
    for wanted in [w.strip().casefold() for w in heading.split("#") if w.strip()]:
        found = next(
            (h for h in note.headings if after < h.line < stop and h.text.casefold() == wanted),
            None,
        )
        if found is None:
            return None
        after, level = found.line, found.level
        stop = next((h.line for h in note.headings if h.line > after and h.level <= level), stop)
    if found is None:
        return None
    return "".join(lines[found.line : stop]).rstrip() + "\n"


def block(text: str, block_id: str) -> str | None:
    """The line marked ``^block_id``, without the mark; for a mark on a line of its own, the
    paragraph above it. None: not there."""
    note = parse(text)
    number = note.block_ids.get(block_id)
    if number is None:
        return None
    lines = [line.rstrip("\r\n") for line in _lines(text.removeprefix("\ufeff"))]
    own = BLOCK_ID.sub("", lines[number]).rstrip()
    if own.strip():
        return own + "\n"
    first = number
    while first > note.body_line and lines[first - 1].strip():
        first -= 1
    return "\n".join(lines[first:number]).rstrip() + "\n" if first < number else None


# -- changing properties ------------------------------------------------------------------------


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _dump(key: str, value: Any, newline: str) -> list[str]:
    if isinstance(value, str) and ISO_DATE.match(value):
        with contextlib.suppress(ValueError):  # a date is written as a date, unquoted
            value = dt.date.fromisoformat(value)
    elif isinstance(value, list):
        value = [plain(v) for v in value]
    dumped = yaml.safe_dump(
        {key: value}, allow_unicode=True, sort_keys=False, default_flow_style=False, width=1000
    )
    return [line + newline for line in dumped.splitlines()]


def _span(lines: list[str], end: int, key: str) -> tuple[int, int] | None:
    """Where property ``key`` is in the block (its first line, and the line after its last)."""
    own = re.compile(r"^(['\"]?)" + re.escape(key) + r"\1[ \t]*:(?:[ \t]|$)")
    for number in range(1, end):
        if own.match(lines[number].rstrip("\r\n")):
            last = number + 1
            while last < end:
                following = lines[last].rstrip("\r\n")
                if following[:1] in (" ", "\t") or following.startswith("- ") or following == "-":
                    last += 1
                else:
                    break
            return number, last
    return None


def _checked(text: str, key: str) -> tuple[list[str], int | None, str, str]:
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    body = text.removeprefix("\ufeff")
    front = split(body)
    if front.error:
        raise ValueError(front.error + "; fix them first")
    if not key.strip() or "\n" in key or ":" in key:
        raise ValueError(f"{key!r} cannot be the name of a property")
    lines = _lines(body)
    return lines, (_block_end(lines) if front.present else None), bom, _newline(body)


def set_property(text: str, key: str, value: Any) -> str:
    """The note with property ``key`` set to ``value``: only that property's lines change
    (or one is added at the end of the block, or a block is made at the top)."""
    lines, end, bom, newline = _checked(text, key)
    new = _dump(key, value, newline)
    if end is None:
        return bom + "".join(["---" + newline, *new, "---" + newline, *lines])
    span = _span(lines, end, key)
    if span is None:
        lines[end:end] = new
    else:
        lines[span[0] : span[1]] = new
    return bom + "".join(lines)


def remove_property(text: str, key: str) -> str:
    """The note without property ``key`` (unchanged when it has none). A block left empty
    goes too."""
    lines, end, bom, _ = _checked(text, key)
    if end is None:
        return text
    span = _span(lines, end, key)
    if span is None:
        return text
    del lines[span[0] : span[1]]
    end -= span[1] - span[0]
    if not "".join(lines[1:end]).strip():
        del lines[: end + 1]
    return bom + "".join(lines)


# -- tags in the properties --------------------------------------------------------------------

TAG_NAME = re.compile(r"[\w/\-]+")


def clean_tag(tag: str) -> str:
    """A tag as written in the tags property ("#Work/Projects " -> "Work/Projects")."""
    name = tag.strip().lstrip("#").strip().strip("/")
    if not TAG_NAME.fullmatch(name) or re.fullmatch(r"[\d/\-]+", name):
        raise ValueError(f"{tag!r} cannot be a tag: letters, digits, - and / only, not just digits")
    return name


def _tags_key(properties: dict[str, Any]) -> str:
    return "tag" if "tags" not in properties and "tag" in properties else "tags"


def add_tag(text: str, tag: str) -> str:
    """The note with ``tag`` in its tags property (unchanged when it is there already)."""
    name = clean_tag(tag)
    properties = split(text).properties
    current = property_tags(properties)
    if any(t.casefold() == name.casefold() for t in current):
        return text
    return set_property(text, _tags_key(properties), [*current, name])


def remove_tag(text: str, tag: str) -> tuple[str, bool]:
    """The note without ``tag`` in its tags property, and whether it was there. A #tag in
    the text itself stays (it is part of what was written)."""
    name = tag.strip().lstrip("#").strip()
    properties = split(text).properties
    current = property_tags(properties)
    remaining = [t for t in current if t.casefold() != name.casefold()]
    if len(remaining) == len(current):
        return text, False
    key = _tags_key(properties)
    return (set_property(text, key, remaining) if remaining else remove_property(text, key)), True
