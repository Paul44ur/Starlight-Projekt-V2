"""Your to-do lists: add tasks with an optional due date and priority, tick them off, move
them between lists ("work", "shopping", ...) and see what is due. The briefing shows what
is due today.

Everything is local, in data/modules/todo/todo.json. Tasks written in your notes
(``- [ ] Call Tom due: 2026-11-05``) count too: they are listed and ticked like these, marked
with their note, and stay in the note (starlight.notebook.tasks).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.jsonfile import load_json, save_json
from starlight.core.schedule import names_time_of_day, parse_when
from starlight.notebook import NoteConflict
from starlight.notebook import tasks as note_tasks
from starlight.notebook.tasks import NoteTask

Priority = Literal["low", "normal", "high"]
_RANK = {"high": 0, "normal": 1, "low": 2}
NO_DUE = "9999"  # the sort key of a task without a due date: after every ISO date


def _day(due: str) -> str:
    """The date of a due value, which is "2026-12-24" or "2026-12-24T18:00" (see _due)."""
    return due[:10]


class Config(BaseModel):
    default_list: str = "inbox"
    max_items: int = Field(default=1000, ge=1)
    keep_done_days: int = Field(default=30, ge=0)
    note_tasks: bool = True  # also the tasks written in your notes


class TodoModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "todo.json"

    def _now(self) -> datetime:
        return datetime.now()

    # -- storage (re-read each time: another Starlight process may have changed it) -----

    def _load(self) -> list[dict[str, Any]]:
        return load_json(self.file, [])

    def _save(self, items: list[dict[str, Any]]) -> None:
        """Save the tasks, and clear away those finished more than keep_done_days ago (this
        is the only place that happens). ISO timestamps compare correctly as text; a
        finished task without done_at counts as old."""
        cutoff = (self._now() - timedelta(days=self.config.keep_done_days)).isoformat()
        kept = [i for i in items if not i.get("done") or i.get("done_at", "") >= cutoff]
        save_json(self.file, kept)

    def _notes(self) -> list[NoteTask]:
        """The tasks in your notes (none when that is switched off)."""
        if not self.config.note_tasks:
            return []
        return note_tasks.note_tasks(self.ctx.notebook)

    def _find(
        self, items: list[dict[str, Any]], id: str, notes: list[NoteTask] | None = None
    ) -> dict[str, Any] | NoteTask:
        """A task by its id, or by a word from its text if only one open task has it: one of
        the to-do list's, or (given ``notes``) one in a note."""
        key = id.strip().lower()
        for item in items:
            if item["id"] == key:
                return item
        for task in notes or []:
            if task.id == key:
                return task
        matches: list[dict[str, Any] | NoteTask] = [
            i for i in items if key and key in i["text"].lower() and not i["done"]
        ]
        matches += [t for t in notes or [] if key and key in t.text.lower() and t.open]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ToolError(f"'{id}' matches {len(matches)} tasks; use the id from todo.list")
        raise ToolError(f"no task '{id}'; see todo.list")

    def _tick(self, task: NoteTask, done: bool, reason: str) -> dict[str, Any]:
        """Tick a task in its note (or open it again): only its line changes, a version of
        the note is kept first, and nothing is written if the task changed meanwhile."""
        file = Path(self.ctx.notebook.root) / task.path
        try:
            ticked = note_tasks.tick_by_id(
                file, task.path, task.id, self.ctx.history, done=done, reason=reason
            )
        except NoteConflict as exc:
            raise ToolError(str(exc)) from exc
        result: dict[str, Any] = {"done": done, "text": task.text, "note": task.title}
        if ticked.written is None:
            result["already"] = True  # (it was so: nothing was written)
        else:
            result["changed"] = ticked.written.changed(file)
        if ticked.repeated:
            result["next"] = ticked.next_due or "added"
        return result

    @staticmethod
    def _not_here(task: NoteTask) -> ToolError:
        return ToolError(
            f"'{task.text}' is a task in the note '{task.title}'; change it in the note "
            "(notes.read, or the Notes page)"
        )

    def _due(self, when: str) -> str | None:
        if not when.strip():
            return None
        moment = parse_when(when, self._now())
        # a day without a time ("tomorrow", "friday", "in 3 days") means that whole day
        timed = names_time_of_day(when)
        return moment.isoformat(timespec="minutes") if timed else moment.date().isoformat()

    def _list_name(self, name: str) -> str:
        return name.strip().lower() or self.config.default_list

    @staticmethod
    def _view(item: dict[str, Any]) -> dict[str, Any]:
        view: dict[str, Any] = {k: item[k] for k in ("id", "text", "list", "priority") if k in item}
        if item.get("due"):
            view["due"] = item["due"]
        if item.get("done"):
            view["done"] = True
        return view

    # -- tools ---------------------------------------------------------------------------

    @tool
    def add(
        self, text: str, due: str = "", priority: Priority = "normal", list: str = ""
    ) -> dict[str, Any]:
        """Add a task. `due` is optional ("tomorrow", "friday", "in 3 days", "2026-12-24",
        "tomorrow 18:00"); `priority` is low, normal or high; `list` is the list name
        (default "inbox"), e.g. "work" or "shopping"."""
        if not text.strip():
            raise ToolError("what is the task?")
        items = self._load()
        if len([i for i in items if not i.get("done")]) >= self.config.max_items:
            raise ToolError(f"too many open tasks (limit {self.config.max_items})")
        item = {
            "id": uuid.uuid4().hex[:6],
            "text": text.strip(),
            "list": self._list_name(list),
            "priority": priority,
            "due": self._due(due),
            "done": False,
            "created": self._now().isoformat(timespec="seconds"),
        }
        items.append(item)
        self._save(items)
        return {"added": True, **self._view(item)}

    @tool(name="list")
    def list_tasks(self, list: str = "", include_done: bool = False) -> dict[str, Any]:
        """Your open tasks, most urgent first (overdue, then by due date and priority).
        Tasks written in your notes come too, with the `note` they are in. Give `list` to
        see one list only (then without the notes); `include_done` also shows finished ones."""
        name = list.strip().lower()
        stored = self._load()
        items = [
            (_order(i), self._view(i))
            for i in stored
            if (include_done or not i.get("done")) and (not name or i.get("list") == name)
        ]
        if not name:  # tasks in notes are on no list: they come with all of them
            recent = (self._now().date() - timedelta(days=self.config.keep_done_days)).isoformat()
            items += [
                (_note_order(t), t.view())
                for t in self._notes()
                if t.open or (include_done and t.finished and t.done >= recent)
            ]
        items.sort(key=lambda pair: pair[0])
        lists = sorted({i.get("list", "") for i in stored if not i.get("done")})
        return {"count": len(items), "tasks": [view for _, view in items], "lists": lists}

    @tool
    def due(self, days: int = 0) -> dict[str, Any]:
        """Tasks that are overdue or due within `days` days (0 = today), the ones in your
        notes too."""
        limit = (self._now().date() + timedelta(days=max(days, 0))).isoformat()
        items = [
            (_order(i), self._view(i)) for i in self._load() if not i.get("done") and i.get("due")
        ]
        items += [(_note_order(t), t.view()) for t in self._notes() if t.open and t.due]
        soon = [
            view
            for _, view in sorted(items, key=lambda pair: pair[0])
            if _day(view["due"]) <= limit
        ]
        today = self._now().date().isoformat()
        return {
            "count": len(soon),
            "overdue": [i for i in soon if _day(i["due"]) < today],
            "due": [i for i in soon if _day(i["due"]) >= today],
        }

    @tool
    def done(self, id: str) -> dict[str, Any]:
        """Tick off a task by its id, or by a word from its text if only one task matches.
        A task in a note is ticked in the note."""
        items = self._load()
        item = self._find(items, id, self._notes())
        if isinstance(item, NoteTask):
            return self._tick(item, True, "todo.done")
        item["done"] = True
        item["done_at"] = self._now().isoformat(timespec="seconds")
        self._save(items)
        return {"done": True, "text": item["text"]}

    @tool
    def reopen(self, id: str) -> dict[str, Any]:
        """Mark a finished task as not done again (by its id; a task in a note too)."""
        items = self._load()
        item = next((i for i in items if i["id"] == id.strip().lower()), None)
        if item is None:
            task = next((t for t in self._notes() if t.id == id.strip().lower()), None)
            if task is not None:
                result = self._tick(task, False, "todo.reopen")
                return {"reopened": True, **{k: v for k, v in result.items() if k != "done"}}
            raise ToolError(f"no task with id '{id}'")
        item["done"] = False
        item.pop("done_at", None)
        self._save(items)
        return {"reopened": True, "text": item["text"]}

    @tool
    def change(
        self, id: str, text: str = "", due: str = "", priority: str = "", list: str = ""
    ) -> dict[str, Any]:
        """Change a task: new `text`, `due` date ("none" removes it), `priority` or `list`
        (moves it). Leave a field empty to keep it."""
        items = self._load()
        item = self._find(items, id, self._notes())
        if isinstance(item, NoteTask):
            raise self._not_here(item)
        if text.strip():
            item["text"] = text.strip()
        if due.strip():
            item["due"] = None if due.strip().lower() in ("none", "no", "-") else self._due(due)
        if priority.strip():
            if priority not in _RANK:
                raise ToolError("priority is low, normal or high")
            item["priority"] = priority
        if list.strip():
            item["list"] = self._list_name(list)
        self._save(items)
        return {"changed": True, **self._view(item)}

    @tool
    def remove(self, id: str) -> dict[str, Any]:
        """Delete a task (by id, or a word from its text if only one task matches)."""
        items = self._load()
        item = self._find(items, id, self._notes())
        if isinstance(item, NoteTask):
            raise self._not_here(item)
        items.remove(item)
        self._save(items)
        return {"removed": True, "text": item["text"]}


def _order(item: dict[str, Any]) -> tuple[str, int, str, int]:
    """Most urgent first: by due date, then priority, then the oldest."""
    return (
        item.get("due") or NO_DUE,
        _RANK.get(item.get("priority", "normal"), 1),
        item["created"],
        0,
    )


def _note_order(task: NoteTask) -> tuple[str, int, str, int]:
    """Like _order; a task in a note comes after the list's own, by note and line."""
    return (task.due or NO_DUE, _RANK.get(task.priority, 1), "~" + task.path, task.line)
