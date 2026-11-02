"""Flashcards with spaced repetition (docs/plan-notes-app.md, block G3).

A card is a line ``Question::Answer`` in a note tagged #flashcards (or #lernkarten,
#karteikarten); ``Question:::Answer`` makes two cards, the second asked the other way round.
A tag under it names the deck (#flashcards/spanish); without one, the note's title is the
deck. Not cards: lines in code, tables, headings and tasks, the Templates folder, and notes
with a ``source`` property (clipped from the web: outside content).

When each card comes back is kept in ``data/notebook/flashcards.json`` (next to the index),
by deck and question. So a card keeps its progress when its note moves or its answer
changes; a new question is a new card. When a note is renamed, the cards of a deck named
after it follow (``renamed``, called by rename.py).

The schedule is a small form of SM-2, by days:

* "again": not known. The card comes back today, and its gap starts over.
* "hard", "good", "easy": known. The gap grows: a new card comes back after 1, 2 or 4 days,
  then by 1.2 times (hard), by its ease (good, 2.5 at first) or by 1.3 times more (easy).
* The ease drops by 0.2 after "again" and 0.15 after "hard" (never below 1.3) and grows by
  0.15 after "easy". The gap is at most ten years.

New cards come at most ``new_per_day`` a day (the module's setting); due ones always come.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlight.core.jsonfile import load_json, save_json
from starlight.notebook import markdown
from starlight.notebook.templates import FOLDER as TEMPLATES

TAGS = ("flashcards", "lernkarten", "karteikarten")
FILE = "flashcards.json"
GRADES = ("again", "hard", "good", "easy")
GRADE_WORDS = {
    "1": "again", "2": "hard", "3": "good", "4": "easy",
    "wrong": "again", "nochmal": "again", "falsch": "again", "nicht gewusst": "again",
    "schwer": "hard", "gut": "good", "richtig": "good", "leicht": "easy", "einfach": "easy",
}  # fmt: skip
START_EASE = 2.5
MIN_EASE = 1.3
MAX_GAP = 3650  # days
FIRST_GAPS = {"hard": 1, "good": 2, "easy": 4}  # days, for a card seen the first time
MAX_NOTE_BYTES = 1024**2
NEW_PER_DAY = 20

_LOCK = threading.Lock()  # one change of the file at a time (the page and the chat)


@dataclass(frozen=True)
class Card:
    id: str
    deck: str  # as written
    question: str
    answer: str
    path: str  # its note ("Notes/Spanish.md")
    line: int
    reverse: bool = False  # the second card of a ":::" line: asked the other way round

    @property
    def title(self) -> str:
        return Path(self.path).stem

    @property
    def key(self) -> tuple[str, str]:
        return plain(self.deck), plain(self.question)


def plain(text: str) -> str:
    """A deck or question as compared: spaces tidied, no case."""
    return " ".join(text.split()).casefold()


def card_id(deck: str, question: str) -> str:
    """A card's id: "c" and eight letters and digits, made from its deck and question."""
    made = f"{plain(deck)}\n{plain(question)}"
    return "c" + hashlib.sha256(made.encode("utf-8")).hexdigest()[:8]


def deck_of(tags: list[str], title: str) -> str | None:
    """The deck of a note with these tags (as written): what follows the first flashcards
    tag that has a part under it ("spanish" for #flashcards/spanish), else the note's
    title; None for a note without a flashcards tag."""
    found = None
    for tag in tags:
        head, _, rest = tag.partition("/")
        if head.casefold() in TAGS:
            if rest.strip("/"):
                return rest.strip("/")
            found = title
    return found


def note_cards(text: str, path: str) -> list[Card]:
    """The cards in one note (none when it has no flashcards tag)."""
    note = markdown.parse(text)
    deck = deck_of(note.tag_names(), Path(path).stem)
    if deck is None:
        return []
    found: list[Card] = []
    for card in note.cards:
        found.append(Card(card_id(deck, card.question), deck, card.question, card.answer,
                          path, card.line))  # fmt: skip
        if card.both_ways:
            found.append(Card(card_id(deck, card.answer), deck, card.answer, card.question,
                              path, card.line, reverse=True))  # fmt: skip
    return found


def _read(file: Path) -> str | None:
    from starlight.text import decode_text

    try:
        if file.stat().st_size > MAX_NOTE_BYTES:
            return None
        return decode_text(file.read_bytes())
    except OSError:
        return None


def deck_notes(index: Any) -> list[tuple[str, str]]:
    """The notes with a flashcards tag that count (see the module's notes): their paths
    and texts, by path. The index is brought up to date first."""
    index.refresh()
    outside = index.with_property("source")
    paths = sorted({p for tag in TAGS for p in index.with_tag(tag)})
    root = Path(index.root)
    found = []
    for path in paths:
        if path in outside or path.split("/")[0] == TEMPLATES:
            continue
        text = _read(root / path)
        if text is not None:
            found.append((path, text))
    return found


def collect(index: Any) -> list[Card]:
    """Every card in the notes, by note and line; a card that is there twice (the same
    question in the same deck) counts once, where it is first."""
    seen: set[str] = set()
    found: list[Card] = []
    for path, text in deck_notes(index):
        for card in note_cards(text, path):
            if card.id not in seen:
                seen.add(card.id)
                found.append(card)
    return found


# -- when a card comes back --------------------------------------------------------------


def grade_word(word: str) -> str:
    """The grade (again, hard, good or easy) from what was said: English, German or 1 to 4."""
    said = " ".join(word.split()).casefold()
    grade = said if said in GRADES else GRADE_WORDS.get(said)
    if grade is None:
        raise ValueError(f"grade it again, hard, good or easy, not {word!r}")
    return grade


def _half_up(value: float) -> int:
    return int(value + 0.5)


def _number(value: Any, default: float) -> float:
    """A number kept in the file, or ``default`` for what is not one (a hand-edited file)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number and abs(number) != float("inf") else default


def schedule(entry: dict[str, Any] | None, grade: str, today: dt.date) -> dict[str, Any]:
    """The card's state after this answer (see the module's notes)."""
    old = entry or {}
    gap = min(MAX_GAP, max(0, int(_number(old.get("gap"), 0))))
    ease = max(MIN_EASE, _number(old.get("ease"), START_EASE))
    reps = max(0, int(_number(old.get("reps"), 0)))
    lapses = max(0, int(_number(old.get("lapses"), 0)))
    first = old.get("first") if isinstance(old.get("first"), str) else None
    if grade == "again":
        gap, ease, reps, lapses = 0, max(MIN_EASE, ease - 0.2), 0, lapses + 1
    else:
        hard = max(gap + 1, _half_up(gap * 1.2))
        good = max(hard + 1, _half_up(gap * ease))
        easy = max(good + 1, _half_up(gap * ease * 1.3))
        gap = FIRST_GAPS[grade] if gap == 0 else {"hard": hard, "good": good, "easy": easy}[grade]
        ease += {"hard": -0.15, "good": 0.0, "easy": 0.15}[grade]
        ease, reps = max(MIN_EASE, ease), reps + 1
    gap = min(gap, MAX_GAP)
    return {
        "due": (today + dt.timedelta(days=gap)).isoformat(),
        "gap": gap,
        "ease": round(ease, 2),
        "reps": reps,  # known in a row
        "lapses": lapses,  # times not known
        "first": first or today.isoformat(),  # the day it was first studied
        "last": today.isoformat(),
    }


def gaps(entry: dict[str, Any] | None, today: dt.date) -> dict[str, int]:
    """In how many days the card comes back after each answer (0: today)."""
    return {grade: schedule(entry, grade, today)["gap"] for grade in GRADES}


# -- the file ----------------------------------------------------------------------------


def state_file(index: Any) -> Path:
    """Where the schedule is kept: data/notebook/flashcards.json, next to the index."""
    return Path(index.db_path).parent / FILE


def load(file: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """deck -> question -> state (both as compared, see ``plain``)."""
    data = load_json(file, {})
    decks = data.get("decks") if isinstance(data, dict) else None
    if not isinstance(decks, dict):
        return {}
    return {
        str(deck): {str(q): s for q, s in cards.items() if isinstance(s, dict)}
        for deck, cards in decks.items()
        if isinstance(cards, dict)
    }


def save(file: Path, decks: dict[str, dict[str, dict[str, Any]]]) -> None:
    kept = {deck: cards for deck, cards in decks.items() if cards}
    save_json(file, {"version": 1, "decks": kept})


def entry_of(decks: dict[str, dict[str, dict[str, Any]]], card: Card) -> dict[str, Any] | None:
    deck, question = card.key
    return decks.get(deck, {}).get(question)


@dataclass
class Plan:
    due: list[Card]  # seen before and due by today, the longest due first
    new: list[Card]  # never seen, as many as may come today
    unseen: int  # all the cards never seen (also those for later days)
    new_left: int  # how many new cards may still come today (in any deck)


def plan(
    cards: list[Card],
    decks: dict[str, dict[str, dict[str, Any]]],
    today: dt.date,
    new_per_day: int = NEW_PER_DAY,
) -> Plan:
    """What to study today: the due cards, then the new ones today still allows."""
    day = today.isoformat()
    started = sum(1 for d in decks.values() for s in d.values() if s.get("first") == day)
    due: list[tuple[str, int, Card]] = []
    new: list[Card] = []
    for number, card in enumerate(cards):
        entry = entry_of(decks, card)
        if entry is None:
            new.append(card)
        elif str(entry.get("due", "")) <= day:
            due.append((str(entry.get("due", "")), number, card))
    due.sort(key=lambda d: (d[0], d[1]))
    left = max(0, new_per_day - started)
    return Plan([c for _, _, c in due], new[:left], len(new), left)


def grade(file: Path, card: Card, said: str, today: dt.date) -> dict[str, Any]:
    """Keep how well ``card`` was known; returns its new state."""
    chosen = grade_word(said)
    with _LOCK:
        decks = load(file)
        deck, question = card.key
        state = schedule(decks.get(deck, {}).get(question), chosen, today)
        decks.setdefault(deck, {})[question] = state
        save(file, decks)
    return state


def renamed(index: Any, old_title: str, new: Path) -> None:
    """A note was renamed: its cards in a deck named after it (no deck tag) take their
    progress along to the new name. Nothing else changes; a problem is only logged (the
    rename itself is done)."""
    try:
        text = _read(new)
        if text is None:
            return
        note = markdown.parse(text)
        before, after = deck_of(note.tag_names(), old_title), deck_of(note.tag_names(), new.stem)
        if before is None or after is None or plain(before) == plain(after):
            return
        questions = {plain(c.question) for c in note_cards(text, new.name)}
        file = state_file(index)
        with _LOCK:
            decks = load(file)
            old_deck = decks.get(plain(before), {})
            moving = {q: s for q, s in old_deck.items() if q in questions}
            if not moving:
                return
            target = decks.setdefault(plain(after), {})
            for question, state in moving.items():
                target.setdefault(question, state)
                del old_deck[question]
            save(file, decks)
    except (OSError, ValueError, AttributeError):
        from starlight.core.logging import get_logger

        get_logger("notebook.flashcards").exception("could not move the cards' progress")
