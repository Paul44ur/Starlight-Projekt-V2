"""Block G3 of the notes plan (docs/plan-notes-app.md): flashcards. Lines "Question::Answer"
in notes tagged #flashcards; a small SM-2 schedule kept in data/notebook/flashcards.json;
"quiz me" in the chat (flashcards.due, .grade, .decks, .add); a Flashcards view on the Notes
page; and a ready-made daily automation."""

from __future__ import annotations

import datetime as dt
import json
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from tests.conftest import REPO
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_home_devices import call, fails
from tests.test_notes_page import notebook as notebook  # (the fixture)
from tests.test_notes_page import notes_page, opened
from tests.test_remote import LOCAL, client, home

TODAY = dt.date(2026, 11, 2)
SPANISH = """---
tags: [flashcards/Spanish]
---
# Spanish words

- hola::hello
- adiós:::goodbye ^c1
> el perro :: the dog
| la casa::the house | not a card |
`std::vector`::a growing array
- [ ] perro::not a card, a task
## gato::not a card, a heading
see https://example.org/a::b
::no question
no answer::

```
code::not a card
```
"""


def test_the_lines_that_are_cards() -> None:
    from starlight.notebook import markdown

    cards = markdown.parse(SPANISH).cards
    assert [(c.question, c.answer, c.both_ways) for c in cards] == [
        ("hola", "hello", False),
        ("adiós", "goodbye", True),  # (the block id is not part of the answer)
        ("el perro", "the dog", False),
        ("`std::vector`", "a growing array", False),  # ("::" in code does not count)
    ]
    assert [c.line for c in cards] == [5, 6, 7, 9]


def test_decks_and_both_ways() -> None:
    from starlight.notebook import flashcards

    cards = flashcards.note_cards(SPANISH, "Notes/Spanish.md")
    assert [(c.deck, c.question, c.answer, c.reverse) for c in cards][:3] == [
        ("Spanish", "hola", "hello", False),
        ("Spanish", "adiós", "goodbye", False),
        ("Spanish", "goodbye", "adiós", True),  # (":::": asked the other way round too)
    ]
    assert len({c.id for c in cards}) == len(cards) == 5
    assert flashcards.card_id("spanish", "Hola ") == cards[0].id  # (no case, tidy spaces)
    assert flashcards.deck_of(["flashcards"], "Biology") == "Biology"  # (the note's title)
    assert flashcards.deck_of(["Lernkarten/Englisch/Verben"], "x") == "Englisch/Verben"
    assert flashcards.deck_of(["karteikarten", "work"], "Kapitel 1") == "Kapitel 1"
    assert flashcards.deck_of(["work", "flash"], "x") is None
    assert flashcards.note_cards("# Plain\nhola::hello\n", "Notes/Plain.md") == []  # no tag


def test_the_schedule() -> None:
    from starlight.notebook.flashcards import gaps, schedule

    assert gaps(None, TODAY) == {"again": 0, "hard": 1, "good": 2, "easy": 4}  # a new card
    state, days = None, []
    for _ in range(5):
        state = schedule(state, "good", TODAY)
        days.append(state["gap"])
    assert days == [2, 5, 13, 33, 83]  # by the ease, 2.5
    assert state["due"] == "2027-01-24" and state["reps"] == 5 and state["first"] == "2026-11-02"
    lapsed = schedule(state, "again", TODAY + dt.timedelta(days=83))
    assert (lapsed["gap"], lapsed["ease"], lapsed["lapses"], lapsed["reps"]) == (0, 2.3, 1, 0)
    assert lapsed["due"] == lapsed["last"] == "2027-01-24"  # again: back the same day
    assert lapsed["first"] == "2026-11-02"
    known = gaps({"gap": 10, "ease": 2.5}, TODAY)
    assert known == {"again": 0, "hard": 12, "good": 25, "easy": 33}
    assert schedule({"gap": 10, "ease": 2.5}, "hard", TODAY)["ease"] == 2.35
    assert schedule({"gap": 10, "ease": 2.5}, "easy", TODAY)["ease"] == 2.65
    worst = {"gap": 1, "ease": 1.3}
    assert schedule(worst, "again", TODAY)["ease"] == 1.3  # (never below 1.3)
    assert schedule({"gap": 3000, "ease": 3}, "easy", TODAY)["gap"] == 3650  # (ten years)
    odd = {"gap": "x", "ease": None, "reps": "many", "first": 5}  # (a file edited by hand)
    assert schedule(odd, "good", TODAY)["gap"] == 2


def test_grades_in_words() -> None:
    from starlight.notebook.flashcards import grade_word

    assert [grade_word(w) for w in ("Good", "3", "nochmal", " leicht ", "falsch", "Schwer")] == [
        "good", "good", "again", "easy", "again", "hard",
    ]  # fmt: skip
    with pytest.raises(ValueError, match="again, hard, good or easy"):
        grade_word("maybe")


def test_what_to_study_today() -> None:
    from starlight.notebook.flashcards import Card, plan

    cards = [Card(f"c{i}", "D", f"q{i}", "a", "Notes/D.md", i) for i in range(6)]
    decks = {
        "d": {
            "q0": {"due": "2026-11-05"},  # later
            "q1": {"due": "2026-11-02", "first": "2026-11-02"},  # due, started today
            "q2": {"due": "2026-10-20"},  # due longest
        }
    }
    today = plan(cards, decks, TODAY, new_per_day=2)
    assert [c.question for c in today.due] == ["q2", "q1"]
    assert [c.question for c in today.new] == ["q3"]  # (one new card was started today)
    assert today.unseen == 3 and today.new_left == 1
    assert plan(cards, decks, TODAY, new_per_day=0).new == []


def test_the_cards_in_the_notebook(tmp_path: Path) -> None:
    from starlight.notebook import flashcards
    from starlight.notebook.index import Index

    root = tmp_path / "Starlight"
    for name, text in {
        "Notes/Spanish.md": SPANISH,
        "Notes/More Spanish.md": "#flashcards/spanish\nhola::hi\nel gato::the cat\n",
        "Notes/Biology.md": "---\ntags: [lernkarten]\n---\nCell::the smallest unit of life\n",
        "Clippings/Page.md": "---\nsource: https://x.example\ntags: [flashcards]\n---\nq::a\n",
        "Templates/Deck.md": "#flashcards\n{{title}}::answer\n",
        "Notes/Plain.md": "hola::hello\n",
    }.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    index = Index(tmp_path / "data" / "notebook" / "index.sqlite", root)
    cards = flashcards.collect(index)
    assert [(c.deck, c.question, c.answer, c.title) for c in cards] == [  # (by path)
        ("Biology", "Cell", "the smallest unit of life", "Biology"),
        ("spanish", "hola", "hi", "More Spanish"),
        ("spanish", "el gato", "the cat", "More Spanish"),
        ("Spanish", "adiós", "goodbye", "Spanish"),  # (its "hola" is there already: once)
        ("Spanish", "goodbye", "adiós", "Spanish"),
        ("Spanish", "el perro", "the dog", "Spanish"),
        ("Spanish", "`std::vector`", "a growing array", "Spanish"),
    ]  # (not: the clipped page, the template, the note without the tag)
    assert flashcards.state_file(index) == tmp_path / "data" / "notebook" / "flashcards.json"


def test_a_renamed_note_keeps_its_cards_progress(tmp_path: Path) -> None:
    from starlight.notebook import History, flashcards
    from starlight.notebook.index import Index
    from starlight.notebook.rename import plan, rename

    root = tmp_path / "Starlight"
    (root / "Notes").mkdir(parents=True)
    bio, words = root / "Notes" / "Biology.md", root / "Notes" / "Words.md"
    bio.write_text("#flashcards\nCell::unit\nDNA::code\n", encoding="utf-8")
    words.write_text("#flashcards/Words\nhola::hello\n", encoding="utf-8")
    index = Index(tmp_path / "data" / "notebook" / "index.sqlite", root)
    history = History(tmp_path / "data" / "history" / "history.sqlite")
    file = flashcards.state_file(index)
    for card in flashcards.collect(index):
        flashcards.grade(file, card, "good", TODAY)
    before = json.loads(file.read_text("utf-8"))["decks"]
    assert sorted(before) == ["biology", "words"]
    rename(index, history, plan(index, bio, bio.with_name("Cells.md")))
    rename(index, history, plan(index, words, words.with_name("Vocabulary.md")))
    after = json.loads(file.read_text("utf-8"))["decks"]
    assert after == {"cells": before["biology"], "words": before["words"]}  # (a tag: stays)
    cards = flashcards.collect(index)
    states = flashcards.load(file)
    assert all(flashcards.entry_of(states, c) is not None for c in cards)


# -- the assistant ---------------------------------------------------------------------------


@pytest.fixture
def decks(paths: Paths, use_module: Callable[[str], Path], documents: Path) -> Path:
    use_module("flashcards")
    notes = documents / "Starlight" / "Notes"
    notes.mkdir(parents=True)
    (notes / "Spanish.md").write_text(SPANISH, encoding="utf-8")
    (notes / "Biology.md").write_text(
        "---\ntags: [lernkarten]\n---\n# Biology\n\n- Cell::the smallest unit of life\n",
        encoding="utf-8",
    )
    return notes


async def test_quiz_me(paths: Paths, decks: Path) -> None:
    async with Kernel(paths) as k:
        listed = await call(k, "flashcards.decks")
        assert [(d["deck"], d["cards"], d["due"], d["new"]) for d in listed["decks"]] == [
            ("Biology", 1, 0, 1), ("Spanish", 5, 0, 5),
        ]  # fmt: skip
        due = await call(k, "flashcards.due", deck="span", limit=2)  # (part of its name)
        assert (due["deck"], due["total"], due["count"]) == ("span", 5, 2)
        first = due["cards"][0]
        assert (first["question"], first["answer"], first["note"], first["new"]) == (
            "hola", "hello", "Spanish", True,
        )  # fmt: skip
        assert first["in_days"] == {"again": 0, "hard": 1, "good": 2, "easy": 4}
        graded = await call(k, "flashcards.grade", id=first["id"], grade="gut")
        tomorrow2 = (dt.date.today() + dt.timedelta(days=2)).isoformat()
        assert graded["graded"] == "good" and graded["next"] == tomorrow2
        assert graded["left_today"] == 5  # (4 Spanish and 1 Biology)
        again = await call(k, "flashcards.grade", id="adiós", grade="again")  # (by its question)
        assert again["next"] == dt.date.today().isoformat() and again["in_days"] == 0
        due = await call(k, "flashcards.due", deck="Spanish")
        assert [c["question"] for c in due["cards"]][:2] == ["adiós", "goodbye"]  # due first
        assert (due["due"], due["new"]) == (1, 3)
        state = json.loads((paths.data / "notebook" / "flashcards.json").read_text("utf-8"))
        assert sorted(state["decks"]["spanish"]) == ["adiós", "hola"]
        assert "no deck 'French'" in await fails(k, "flashcards.due", deck="French")
        assert "no card" in await fails(k, "flashcards.grade", id="c00000000", grade="good")
        assert "again, hard, good or easy" in await fails(
            k, "flashcards.grade", id=first["id"], grade="so-so"
        )
        by_title = await call(k, "flashcards.due", deck="biology")
        assert [c["question"] for c in by_title["cards"]] == ["Cell"]


async def test_new_cards_a_day(paths: Paths, decks: Path) -> None:
    (paths.module_configs / "flashcards.yaml").write_text("new_per_day: 2\n", encoding="utf-8")
    async with Kernel(paths) as k:
        due = await call(k, "flashcards.due")
        assert (due["new"], due["count"]) == (2, 2)
        for card in due["cards"]:
            await call(k, "flashcards.grade", id=card["id"], grade="easy")
        after = await call(k, "flashcards.due")
        assert after["total"] == 0 and after["cards"] == []
        assert (await call(k, "flashcards.decks"))["new"] == 0


async def test_adding_cards(paths: Paths, decks: Path) -> None:
    async with Kernel(paths) as k:
        added = await call(k, "flashcards.add", deck="spanish", question="el sol", answer="the sun")
        assert added["note"] == "Spanish" and added["changed"]["version"] is not None
        text = (decks / "Spanish.md").read_text("utf-8")
        assert text.endswith("```\n- el sol :: the sun\n")
        made = await call(k, "flashcards.add", deck="Capitals", question="Peru?", answer="Lima",
                          both_ways=True)  # fmt: skip
        new = decks / "Flashcards" / "Capitals.md"
        assert made["note"] == "Capitals" and made["changed"]["new"] is True
        assert new.read_text("utf-8") == (
            "---\ntags: [flashcards]\n---\n# Capitals\n\n- Peru? ::: Lima\n"
        )
        listed = {d["deck"]: d["cards"] for d in (await call(k, "flashcards.decks"))["decks"]}
        assert listed == {"Biology": 1, "Capitals": 2, "Spanish": 6}
        assert "already has a card" in await fails(
            k, "flashcards.add", deck="Spanish", question="Hola", answer="hi"
        )
        assert "cannot contain '::'" in await fails(
            k, "flashcards.add", deck="x", question="a::b", answer="c"
        )
        assert "would not read as a card" in await fails(
            k, "flashcards.add", deck="x", question="[ ] task", answer="c"
        )
        (decks / "Flashcards" / "Plain.md").write_text("# Plain\n", encoding="utf-8")
        assert "without the #flashcards tag" in await fails(
            k, "flashcards.add", deck="Plain", question="q", answer="a"
        )


async def test_no_cards_yet(
    paths: Paths, use_module: Callable[[str], Path], documents: Path
) -> None:
    use_module("flashcards")
    async with Kernel(paths) as k:
        listed = await call(k, "flashcards.decks")
        assert listed["count"] == 0 and "#flashcards" in listed["how"]
        assert (await call(k, "flashcards.due"))["cards"] == []


async def test_the_daily_automation(paths: Paths, use_module: Callable[[str], Path]) -> None:
    use_module("automations")
    async with Kernel(paths) as k:
        templates = await call(k, "automations.templates")
        assert "flashcards.due" in templates["daily-flashcards"]
        assert "only if total > 0" in templates["daily-flashcards"]


# -- the Notes page -----------------------------------------------------------------------


@pytest.fixture
def cards_app(tmp_path: Path, notebook: Path) -> Iterator[Any]:
    root = home(tmp_path / "h")
    for name in ("notes", "flashcards"):
        shutil.copytree(REPO / "modules" / name, root / "modules" / name)
    (notebook / "Notes" / "Spanish.md").write_text(SPANISH, encoding="utf-8")
    with client(root) as c:
        yield c


def test_the_flashcards_api(cards_app: Any, tmp_path: Path) -> None:
    decks = cards_app.get("/api/notes/flashcards", headers=LOCAL).json()
    assert [d["deck"] for d in decks["decks"]] == ["Spanish"] and decks["new"] == 5
    due = cards_app.get("/api/notes/flashcards/due", params={"deck": "Spanish"}, headers=LOCAL)
    cards = due.json()["cards"]
    assert len(cards) == 5 and cards[0]["answer"] == "hello"
    graded = cards_app.post("/api/notes/flashcards/grade", json={"id": cards[0]["id"],
                            "grade": "easy"}, headers=LOCAL)  # fmt: skip
    assert graded.status_code == 200 and graded.json()["in_days"] == 4
    state = tmp_path / "h" / "data" / "notebook" / "flashcards.json"
    assert json.loads(state.read_text("utf-8"))["decks"]["spanish"]["hola"]["gap"] == 4
    bad = cards_app.post("/api/notes/flashcards/grade", json={"id": cards[0]["id"],
                         "grade": "meh"}, headers=LOCAL)  # fmt: skip
    assert bad.status_code == 400 and "again, hard, good or easy" in bad.json()["detail"]
    empty = cards_app.post("/api/notes/flashcards/grade", json={}, headers=LOCAL)
    assert empty.status_code == 400


def test_the_api_says_when_the_module_is_off(tmp_path: Path, notebook: Path) -> None:
    root = home(tmp_path / "h")
    shutil.copytree(REPO / "modules" / "notes", root / "modules" / "notes")
    with client(root) as c:
        off = c.get("/api/notes/flashcards", headers=LOCAL)
        assert off.status_code == 503 and "flashcards module is off" in off.json()["detail"]


@pytest.mark.parametrize(("scheme", "locale"), [("light", "en-US"), ("dark", "de-DE")])
async def test_studying_on_the_page(
    static_site: str,
    cards_app: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    locale: str,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    german = locale.startswith("de")
    state = tmp_path / "h" / "data" / "notebook" / "flashcards.json"
    async with notes_page(static_site, cards_app, locale=locale, scheme=scheme) as page:
        await opened(page, f"{static_site}/notes.html")
        await page.locator('.quick button[data-view="cards"]').click()
        await page.wait_for_selector(".deck-row")
        facts = await page.locator(".deck-facts").text_content()
        assert facts == ("5 Karten · 0 fällig · 5 neu" if german else "5 cards · 0 due · 5 new")
        await page.locator('.deck-row[data-deck="Spanish"] button').click()
        await page.wait_for_selector(".study-q")
        assert await page.locator(".study-q").text_content() == "hola"
        assert await page.locator(".study-a").count() == 0  # (not shown yet)
        await page.keyboard.press("Space")  # Space shows the answer
        await page.wait_for_selector(".study-a")
        assert await page.locator(".study-a").text_content() == "hello"
        labels = await page.locator(".study-controls .grade").all_text_contents()
        assert labels == (
            ["Nochmalheute", "Schwerin 1 Tag", "Gutin 2 Tagen", "Leichtin 4 Tagen"]
            if german
            else ["Againtoday", "Hardin 1 day", "Goodin 2 days", "Easyin 4 days"]
        )
        await page.keyboard.press("3")  # 3: good
        await page.wait_for_function(
            "() => document.querySelector('.study-q')?.textContent === 'adiós'"
        )
        assert json.loads(state.read_text("utf-8"))["decks"]["spanish"]["hola"]["gap"] == 2
        await page.locator(".study-controls button").click()  # (the Show answer button)
        await page.locator('.study-controls [data-grade="again"]').click()  # not known
        await page.wait_for_function(
            "() => document.querySelector('.study-q')?.textContent === 'goodbye'"
        )
        for _ in range(3):  # the other three
            await page.keyboard.press("Space")
            await page.wait_for_selector(".study-a")
            before = await page.locator(".study-q").text_content()
            await page.keyboard.press("4")
            await page.wait_for_function(
                "(was) => !document.querySelector('.study-a')"
                " || document.querySelector('.study-q')?.textContent !== was",
                arg=before,
            )
        assert await page.locator(".study-q").text_content() == "adiós"  # (again: at the end)
        await page.keyboard.press("Space")
        await page.wait_for_selector(".study-a")
        after_again = await page.locator(".study-controls .grade small").all_text_contents()
        assert after_again[2] == ("in 2 Tagen" if german else "in 2 days")
        await page.keyboard.press("3")
        await page.wait_for_selector(".study-done")
        assert await page.locator(".study-progress .dim").text_content() == (
            "5 von 5" if german else "5 of 5"
        )
        assert await page.locator(".study-card .dim").text_content() == (
            "5 gewusst, 1-mal nochmal" if german else "5 known, 1 again"
        )
        decks = json.loads(state.read_text("utf-8"))["decks"]["spanish"]
        assert decks["adiós"]["lapses"] == 1 and len(decks) == 5
        await page.locator(".study-controls button").click()  # back to the decks
        await page.wait_for_selector(".deck-row")
        assert await page.locator(".deck-row button").is_disabled()  # (nothing left today)
        assert page.errors == []  # type: ignore[attr-defined]
