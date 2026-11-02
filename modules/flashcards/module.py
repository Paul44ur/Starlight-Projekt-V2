"""Flashcards with spaced repetition: "quiz me on Spanish", "how many cards are due?",
"make a card: What is the capital of Peru? Lima".

A card is a line ``Question::Answer`` in a note tagged #flashcards (``:::`` asks both ways;
#flashcards/spanish names the deck, else the note's title does). Starlight keeps when each
card comes back (starlight.notebook.flashcards, in data/notebook/flashcards.json): a card
known well comes back later and later, one not known comes back today. The Notes page has a
Cards view to study them the same way.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.config import ConfigError
from starlight.core.safepaths import inside
from starlight.notebook import NoteConflict, append_note, flashcards, markdown, write_note
from starlight.notebook.flashcards import Card, plain
from starlight.notebook.write import read_for_change

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.IGNORECASE)
HOW = (
    "Write lines like 'Question::Answer' in a note tagged #flashcards ('Question:::Answer' "
    "asks both ways; #flashcards/spanish puts them in a deck called spanish), or add them "
    "with flashcards.add."
)


class Config(BaseModel):
    new_per_day: int = Field(default=20, ge=0, le=1000)  # due cards always come
    folder: str = "Notes/Flashcards"  # where flashcards.add makes a new deck's note


class FlashcardsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    def _today(self) -> dt.date:
        return dt.date.today()

    def _index(self) -> Any:
        try:
            index = self.ctx.notebook
        except ConfigError as exc:  # (output.folder is set wrong)
            raise ToolError(str(exc)) from exc
        if index is None:
            raise ToolError("the Starlight folder cannot be used; see output.folder")
        return index

    def _study(self) -> tuple[Any, list[Card], dict[str, Any], Path]:
        index = self._index()
        file = flashcards.state_file(index)
        try:
            state = flashcards.load(file)
        except OSError as exc:  # (locked: never act as if it were empty)
            raise ToolError(str(exc)) from exc
        return index, flashcards.collect(index), state, file

    @staticmethod
    def _deck(cards: list[Card], deck: str) -> list[Card]:
        """The cards of one deck, by its name, a part of it, or its note's title."""
        wanted = plain(deck)
        if not wanted:
            return cards
        exact = [c for c in cards if plain(c.deck) == wanted]
        exact = exact or [c for c in cards if plain(c.title) == wanted]
        if exact:
            return exact
        names = sorted({c.deck for c in cards if wanted in plain(c.deck)}, key=plain)
        if len(names) == 1:
            return [c for c in cards if c.deck == names[0]]
        if names:
            raise ToolError(f"'{deck}' fits several decks: {', '.join(names[:10])}")
        raise ToolError(f"there is no deck '{deck}'; see flashcards.decks")

    @staticmethod
    def _card(cards: list[Card], id: str) -> Card:
        """A card by its id (from flashcards.due), or by its question if only one has it."""
        wanted = id.strip().lower()
        for card in cards:
            if card.id == wanted:
                return card
        same = [c for c in cards if plain(c.question) == plain(id)]
        if len(same) == 1:
            return same[0]
        if same:
            raise ToolError(f"{len(same)} cards ask '{id}'; use the id from flashcards.due")
        raise ToolError(f"there is no card '{id}'; use the id from flashcards.due")

    def _view(self, card: Card, state: dict[str, Any], today: dt.date) -> dict[str, Any]:
        entry = flashcards.entry_of(state, card)
        return {
            "id": card.id,
            "question": card.question,
            "answer": card.answer,
            "deck": card.deck,
            "note": card.title,
            "path": card.path,
            "new": entry is None,
            "in_days": flashcards.gaps(entry, today),  # after again, hard, good, easy
        }

    @tool
    def decks(self) -> dict[str, Any]:
        """Your flashcard decks: how many cards each has, how many are due today and how
        many new ones may come today."""
        _index, cards, state, _file = self._study()
        today = flashcards.plan(cards, state, self._today(), self.config.new_per_day)
        due = {c.id for c in today.due}
        found: dict[str, dict[str, Any]] = {}
        unseen: dict[str, int] = {}
        for card in cards:
            key = plain(card.deck)
            deck = found.setdefault(
                key, {"deck": card.deck, "cards": 0, "due": 0, "new": 0, "notes": []}
            )
            deck["cards"] += 1
            deck["due"] += card.id in due
            unseen[key] = unseen.get(key, 0) + (flashcards.entry_of(state, card) is None)
            if card.title not in deck["notes"]:
                deck["notes"].append(card.title)
        for key, deck in found.items():
            deck["new"] = min(unseen[key], today.new_left)
        listed = sorted(found.values(), key=lambda d: plain(d["deck"]))
        result: dict[str, Any] = {
            "count": len(listed),
            "decks": listed,
            "due": len(today.due),
            "new": len(today.new),
        }
        if not listed:
            result["how"] = HOW
        return result

    @tool
    def due(self, deck: str = "", limit: int = 20) -> dict[str, Any]:
        """Flashcards to study now: the due ones first, then new ones. `deck`: only that
        deck ("" for all). To quiz the user: ask one question at a time and wait for their
        answer before you say the right one; then grade it with flashcards.grade (again
        when it was wrong; hard, good or easy when it was right)."""
        _index, cards, state, _file = self._study()
        day = self._today()
        today = flashcards.plan(self._deck(cards, deck), state, day, self.config.new_per_day)
        picked = (today.due + today.new)[: max(1, min(limit, 500))]
        result: dict[str, Any] = {
            "deck": deck.strip() or "all",
            "due": len(today.due),
            "new": len(today.new),
            "total": len(today.due) + len(today.new),
            "count": len(picked),
            "cards": [self._view(c, state, day) for c in picked],
        }
        if not cards:
            result["how"] = HOW
        return result

    @tool
    def grade(self, id: str, grade: str) -> dict[str, Any]:
        """How well a flashcard was known, after asking it: again (not known: it comes back
        today), hard, good or easy (known: it comes back later). `id`: from flashcards.due."""
        _index, cards, _state, file = self._study()
        card = self._card(cards, id)
        day = self._today()
        try:
            state = flashcards.grade(file, card, grade, day)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc
        except OSError as exc:
            raise ToolError(f"could not keep it: {exc}") from exc
        left = flashcards.plan(cards, flashcards.load(file), day, self.config.new_per_day)
        return {
            "graded": flashcards.grade_word(grade),
            "question": card.question,
            "next": state["due"],
            "in_days": state["gap"],
            "left_today": len(left.due) + len(left.new),
            "next_in_days": flashcards.gaps(state, day),  # (when it is asked again today)
        }

    # -- adding a card -----------------------------------------------------------------

    def _new_note(self, root: Path, deck: str) -> Path:
        name = _BAD.sub("", deck).strip().strip(".")[:80]
        if _RESERVED.match(name):
            name = f"_{name}"
        if not name:
            raise ToolError("which deck should the card go to?")
        folder = root
        for part in self.config.folder.replace("\\", "/").split("/"):
            clean = _BAD.sub("", part).strip().strip(".")
            if clean:
                folder = folder / clean
        path = folder / f"{name}.md"
        if not inside(path.resolve(), root.resolve()):
            raise ToolError("the flashcards folder must be inside the Starlight folder")
        return path

    @tool
    def add(self, deck: str, question: str, answer: str, both_ways: bool = False) -> dict[str, Any]:
        """Add a flashcard to a deck: a line "question :: answer" in the deck's note (a new
        note in Notes/Flashcards when there is none). `both_ways`: also asked the other way
        round (the answer first)."""
        deck, question, answer = (" ".join(x.split()) for x in (deck, question, answer))
        if not question or not answer:
            raise ToolError("a card needs a question and an answer")
        if "::" in question or "::" in answer:
            raise ToolError("a question or answer cannot contain '::'")
        if not deck:
            raise ToolError("which deck should the card go to?")
        line = f"- {question} {':::' if both_ways else '::'} {answer}"
        read = markdown.parse(line).cards
        if len(read) != 1 or (read[0].question, read[0].answer) != (question, answer):
            raise ToolError(f"that would not read as a card: {line}")
        index = self._index()
        root = Path(index.root)
        notes = flashcards.deck_notes(index)
        target = None  # the deck's note (the first, if several make up the deck)
        for path, text in notes:
            found = flashcards.deck_of(markdown.parse(text).tag_names(), Path(path).stem)
            if target is None and found is not None and plain(found) == plain(deck):
                target = path
        known = {c.id for path, text in notes for c in flashcards.note_cards(text, path)}
        asked = [question, answer] if both_ways else [question]
        if any(flashcards.card_id(deck, q) in known for q in asked):
            raise ToolError(f"the deck {deck} already has a card that asks this")
        if target is None:
            file = self._new_note(root, deck)
            if file.exists():
                raise ToolError(
                    f"there is a note {file.stem} without the #flashcards tag; tag it, or name "
                    "another deck"
                )
            file.parent.mkdir(parents=True, exist_ok=True)
            text = f"---\ntags: [flashcards]\n---\n# {file.stem}\n\n{line}\n"
            written = write_note(file, text, "flashcards.add", self.ctx.history)
        else:
            file = root / target
            try:
                now = read_for_change(file)
                gap = "" if not now.text or now.text.endswith("\n") else "\n"
                written = append_note(file, gap + line + "\n", "flashcards.add",
                                      self.ctx.history, expected=now.stamp)  # fmt: skip
            except NoteConflict as exc:
                raise ToolError(str(exc)) from exc
        return {
            "added": question,
            "deck": deck,
            "note": file.stem,
            "both_ways": both_ways,
            "changed": written.changed(file),
        }
