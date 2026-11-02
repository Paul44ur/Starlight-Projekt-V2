"""Tasks in notes, as to-dos (docs/plan-notes-app.md, block D3).

A task is a line like ``- [ ] Call Tom due: 2026-11-05 priority: high`` anywhere in a note
(``markdown.parse`` reads it). The to-do module lists these next to its own tasks
(``todo.list``, ``todo.due``) and ticks them (``todo.done``, ``todo.reopen``). Ticking changes
only that one line of the note; a version of the note is kept first, and nothing is written
when the task changed meanwhile.

* A task's id is "n" and six letters and digits, made from its note and its text. It stays
  when the task moves or gets a date, and changes with its text. Two tasks with the same
  text in one note are told apart by their order.
* Dates are ISO dates (2026-11-05). Starlight and the Notes page write typed natural dates
  that way when they save (``markdown.iso_dates``). One typed in another program
  (``due: tomorrow``) counts from the day its note was last changed.
* Ticking writes ``done:`` with the day; opening the task again takes it away.
* A repeating task (``repeat: every week``, ``wiederholen: jeden Monat``) gets its next one
  when it is ticked: a copy below it, open, with its dates moved on.
* Not counted: tasks without text, the Templates folder, and notes with a ``source``
  property (clipped from the web: outside content).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from starlight.notebook import markdown
from starlight.notebook.history import History
from starlight.notebook.templates import FOLDER as TEMPLATES
from starlight.notebook.write import NoteConflict, Written, read_for_change, write_note

DONE = ("x", "X")
SIMPLE_REPEATS = {
    "daily": ("day", 1), "täglich": ("day", 1), "weekly": ("week", 1),
    "wöchentlich": ("week", 1), "monthly": ("month", 1), "monatlich": ("month", 1),
    "yearly": ("month", 12), "annually": ("month", 12), "jährlich": ("month", 12),
}  # fmt: skip
REPEAT_UNITS = {
    **dict.fromkeys(("day", "days", "tag", "tage"), ("day", 1)),
    **dict.fromkeys(("week", "weeks", "woche", "wochen"), ("week", 1)),
    **dict.fromkeys(("month", "months", "monat", "monate"), ("month", 1)),
    **dict.fromkeys(("year", "years", "jahr", "jahre"), ("month", 12)),
}
REPEAT = re.compile(r"(?:every|each|jeden|jede|jedes|alle) (?:(\d{1,3}) )?(\w+)")


def _words(text: str) -> str:
    return " ".join(text.split()).casefold()


def task_id(path: str, text: str, occurrence: int = 0) -> str:
    """The id of the ``occurrence``-th task (from 0) with this text in the note ``path``."""
    key = f"{path.casefold()}\n{_words(text)}\n{occurrence}"
    return "n" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:6]


def numbered(path: str, texts: Iterable[str]) -> list[str]:
    """The ids of a note's tasks, given their texts in line order."""
    seen: dict[str, int] = {}
    ids = []
    for text in texts:
        count = seen.get(_words(text), 0)
        seen[_words(text)] = count + 1
        ids.append(task_id(path, text, count))
    return ids


@dataclass(frozen=True)
class NoteTask:
    id: str
    path: str  # the note, in the Starlight folder ("Notes/Plan.md")
    title: str  # the note's title
    line: int  # from 0
    status: str  # " " open, "x" done (other characters: cancelled and so on)
    text: str
    due: str = ""  # an ISO date, or ""
    start: str = ""
    done: str = ""
    priority: str = ""  # high, normal, low or ""
    repeat: str = ""
    stage: str = ""  # status: "doing" (the Kanban's middle column), "open" or ""

    @property
    def open(self) -> bool:
        return self.status == " "

    @property
    def finished(self) -> bool:
        return self.status in DONE

    def view(self) -> dict[str, Any]:
        """The task as the to-do module shows it: like its own, with the note it is in."""
        view: dict[str, Any] = {
            "id": self.id,
            "text": self.text,
            "note": self.title,
            "path": self.path,
            "priority": self.priority if self.priority in ("high", "low") else "normal",
        }
        for name in ("due", "start", "repeat"):
            if getattr(self, name):
                view[name] = getattr(self, name)
        if self.finished:
            view["done"] = True
        elif self.open and self.stage == "doing":
            view["stage"] = "doing"
        return view


def _iso(value: str, written: dt.date) -> str:
    day = markdown.natural_date(value, written) if value else None
    return day.isoformat() if day else ""


def note_tasks(index: Any) -> list[NoteTask]:
    """Every task in the notes (see the module's notes for what is left out), by note and
    line. ``index`` is the notebook index; it is brought up to date first."""
    index.refresh()
    notes = {info.path: info for info in index.notes()}
    outside = index.with_property("source")
    rows: dict[str, list[dict[str, Any]]] = {}
    for row in index.tasks():
        rows.setdefault(row["path"], []).append(row)
    found: list[NoteTask] = []
    for path, tasks in rows.items():
        info = notes.get(path)
        if info is None or path in outside or path.split("/")[0] == TEMPLATES:
            continue
        written = dt.date.fromtimestamp(info.modified)
        ids = numbered(path, (row["text"] for row in tasks))
        for task_id_, row in zip(ids, tasks, strict=True):
            if not row["text"].strip():
                continue
            dates = {name: _iso(row[name], written) for name in markdown.DATE_FIELDS}
            fields = {"priority": row["priority"], "repeat": row["repeat"],
                      "stage": row.get("stage", ""), **dates}  # fmt: skip
            found.append(
                NoteTask(
                    task_id_, path, info.title, row["line"], row["status"], row["text"], **fields
                )
            )
    return found


# -- repeating tasks ---------------------------------------------------------------------------


def repeat_rule(text: str) -> tuple[str, int, int | None] | None:
    """What ``repeat:`` says: (unit, how many, weekday or None). "every 2 weeks" is
    ("week", 2, None), "jeden Montag" ("week", 1, 0), "monthly" ("month", 1, None). None
    when it is not understood."""
    raw = _words(text)
    if raw in SIMPLE_REPEATS:
        unit, count = SIMPLE_REPEATS[raw]
        return unit, count, None
    match = REPEAT.fullmatch(raw)
    if not match:
        return None
    count, word = int(match.group(1) or 1), match.group(2)
    if count < 1:
        return None
    if word in markdown.WEEKDAY_NAMES:
        return "week", count, markdown.WEEKDAY_NAMES[word]
    if word not in REPEAT_UNITS:
        return None
    unit, times = REPEAT_UNITS[word]
    return unit, count * times, None


def next_day(day: dt.date, rule: tuple[str, int, int | None]) -> dt.date:
    """The day after ``day`` that the rule comes back on."""
    unit, count, weekday = rule
    if weekday is not None:
        return day + dt.timedelta(days=(weekday - day.weekday()) % 7 or 7, weeks=count - 1)
    if unit == "day":
        return day + dt.timedelta(days=count)
    if unit == "week":
        return day + dt.timedelta(weeks=count)
    return markdown.add_months(day, count)


def _next_one(body: str, task: markdown.Task, today: dt.date) -> str | None:
    """The open copy of a repeating task, its dates moved on (None: no repeat it reads)."""
    rule = repeat_rule(task.repeat)
    if rule is None:
        return None
    days = {name: markdown.natural_date(getattr(task, name), today) for name in ("due", "start")}
    base = days["due"] or days["start"]
    moved = next_day(base, rule) - base if base else dt.timedelta(0)
    edits: list[tuple[int, int, str]] = []  # (from, to, new), applied from the right
    block = markdown.BLOCK_ID.search(body)
    if block:  # a block id belongs to one line only
        edits.append((block.start(), len(body), ""))
    for name in ("due", "start", "done"):
        span = task.span(name)
        day = days.get(name)
        if span is None:
            continue
        if name == "done":
            edits.append((span[0], span[2], ""))
        elif day is not None:
            edits.append((span[1], span[2], (day + moved).isoformat()))
    for start, end, new in sorted(edits, reverse=True):
        body = body[:start] + new + body[end:]
    status = markdown.TASK.match(body)
    if status is None:
        return None
    rest = re.sub(r"[ \t]{2,}", " ", body[status.start(3) :]).rstrip()
    return body[: status.start(2)] + " " + body[status.end(2) : status.start(3)] + rest


# -- ticking -----------------------------------------------------------------------------------


def _with_field(body: str, name: str, value: str) -> str:
    """``name: value`` added at the end of the task (before its block id, if it has one)."""
    block = markdown.BLOCK_ID.search(body)
    if block:
        caret = block.start(1) - 1
        return f"{body[:caret].rstrip()} {name}: {value} {body[caret:]}"
    return f"{body.rstrip()} {name}: {value}"


def _without(body: str, span: tuple[int, int, int]) -> str:
    left, right = body[: span[0]].rstrip(" \t"), body[span[2] :].lstrip(" \t")
    return f"{left} {right}" if right else left


def _ticked(body: str, task: markdown.Task, done: bool, today: dt.date) -> str:
    status = markdown.TASK.match(body)
    assert status is not None  # (the line is a task: parse found it)
    body = body[: status.start(2)] + ("x" if done else " ") + body[status.end(2) :]
    span = task.span("done")
    if done:
        if span is None:
            return _with_field(body, "done", today.isoformat())
        return body[: span[1]] + today.isoformat() + body[span[2] :]
    return _without(body, span) if span else body


@dataclass(frozen=True)
class Ticked:
    written: Written | None  # None: nothing to change (it was already so)
    path: Path
    text: str  # the task
    done: bool
    next_due: str = ""  # a repeating task's next one: its date ("" when it has none)
    repeated: bool = False  # a next one was added


def _read(file: Path) -> tuple[str, str, str, str]:
    read = read_for_change(file)
    return read.text, read.stamp, read.newline, read.bom


def _apply(
    file: Path,
    read: tuple[str, str, str, str],
    task: markdown.Task,
    done: bool,
    history: History | None,
    reason: str,
    today: dt.date,
) -> Ticked:
    text, before, newline, bom = read
    if task.open != done:  # already as asked
        return Ticked(None, file, task.text, done)
    lines = text.splitlines(keepends=True)
    body = lines[task.line].rstrip("\r\n")
    ending = lines[task.line][len(body) :]
    ticked = _ticked(body, task, done, today)
    copy = _next_one(body, task, today) if done and task.repeat else None
    if copy is None:
        lines[task.line] = ticked + ending
    else:  # the next one goes below
        lines[task.line] = ticked + (ending or newline) + copy + ending
    written = write_note(
        file, bom + "".join(lines), reason, history, expected=before, newline=newline
    )
    next_due = ""
    if copy is not None:
        next_task = markdown.parse(copy).tasks
        next_due = next_task[0].due if next_task else ""
    return Ticked(written, file, task.text, done, next_due, copy is not None)


def tick(
    file: Path,
    history: History | None,
    *,
    line: int,
    expected: str,
    done: bool = True,
    reason: str = "notes.task",
    today: dt.date | None = None,
) -> Ticked:
    """Tick the task on ``line`` (from 0) of the note, or open it again (``done=False``).
    The line must still read ``expected``; else nothing is written (NoteConflict)."""
    read = _read(file)
    lines = read[0].splitlines()
    if not 0 <= line < len(lines) or lines[line] != expected.rstrip("\r\n"):
        raise NoteConflict(f"the task in {file.stem} was changed meanwhile; look again")
    task = next((t for t in markdown.parse(read[0]).tasks if t.line == line), None)
    if task is None:
        raise NoteConflict(f"line {line + 1} of {file.stem} is not a task any more")
    return _apply(file, read, task, done, history, reason, today or dt.date.today())


def tick_by_id(
    file: Path,
    path: str,
    id: str,
    history: History | None,
    *,
    done: bool = True,
    reason: str = "notes.task",
    today: dt.date | None = None,
) -> Ticked:
    """Like ``tick``, finding the task by its id (``path``: the note in the Starlight folder).
    When no task in the note has that id any more, nothing is written (NoteConflict)."""
    read = _read(file)
    tasks = markdown.parse(read[0]).tasks
    for found, task in zip(numbered(path, (t.text for t in tasks)), tasks, strict=True):
        if found == id:
            return _apply(file, read, task, done, history, reason, today or dt.date.today())
    raise NoteConflict(
        f"that task is not in {file.stem} as it was any more (its text changed); look again"
    )


def with_next_ones(text: str, before: str, today: dt.date) -> str:
    """``text`` with the next one of each repeating task ticked since ``before`` (the note as
    it was): ticked in the editor (Ctrl+Enter) or by hand. Each goes below its task."""
    old = {line.rstrip("\r\n") for line in before.splitlines()}
    lines = text.splitlines(keepends=True)
    added = 0
    for task in markdown.parse(text).tasks:
        if task.status not in DONE or not task.repeat:
            continue
        at = task.line + added
        body = lines[at].rstrip("\r\n")
        if body in old or _ticked(body, task, False, today) not in old:
            continue  # ticked before, or not ticked from an open task here
        copy = _next_one(body, task, today)
        if copy is None or (at + 1 < len(lines) and lines[at + 1].rstrip("\r\n") == copy):
            continue
        ending = lines[at][len(body) :]
        lines[at] = body + (ending or "\n")
        lines.insert(at + 1, copy + ending)
        added += 1
    return "".join(lines) if added else text


STAGES = ("open", "doing", "done")


def move(
    file: Path,
    path: str,
    id: str,
    to: str,
    history: History | None,
    *,
    reason: str = "notes.task",
    today: dt.date | None = None,
) -> Ticked:
    """Put a task of a note (found by its id) in a Kanban column: ``open`` (not ticked, no
    status), ``doing`` (not ticked, ``status: doing``) or ``done`` (ticked, with the day; a
    repeating task gets its next one). Only that line changes; a version is kept first."""
    if to not in STAGES:
        raise ValueError(f"a task is open, doing or done, not {to}")
    day = today or dt.date.today()
    text, before, newline, bom = _read(file)
    tasks = markdown.parse(text).tasks
    task = next(
        (t for found, t in zip(numbered(path, (t.text for t in tasks)), tasks, strict=True)
         if found == id), None,
    )  # fmt: skip
    if task is None:
        raise NoteConflict(
            f"that task is not in {file.stem} as it was any more (its text changed); look again"
        )
    lines = text.splitlines(keepends=True)
    original = lines[task.line].rstrip("\r\n")
    ending = lines[task.line][len(original) :]
    body = original
    span = task.span("stage")
    if to == "doing":
        body = (
            body[: span[1]] + "doing" + body[span[2] :]
            if span
            else _with_field(body, "status", "doing")
        )
    elif span:
        body = _without(body, span)
    current = markdown.parse(body).tasks[0]  # (the line on its own, its places found again)
    copy = None
    if to == "done" and current.open:
        copy = _next_one(body, current, day) if current.repeat else None
        body = _ticked(body, current, True, day)
    elif to != "done" and not current.open:  # finished (or cancelled): open again
        body = _ticked(body, current, False, day)
    if body == original:
        return Ticked(None, file, task.text, to == "done")
    lines[task.line] = body + (ending or newline) + copy + ending if copy else body + ending
    written = write_note(
        file, bom + "".join(lines), reason, history, expected=before, newline=newline
    )
    next_due = markdown.parse(copy).tasks[0].due if copy else ""
    return Ticked(written, file, task.text, to == "done", next_due, copy is not None)
