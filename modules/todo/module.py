"""Your to-do lists: add tasks with an optional due date and priority, tick them off, move
them between lists ("work", "shopping", ...) and see what is due. The briefing shows what
is due today.

Everything is local, in data/modules/todo/todo.json.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.schedule import parse_when

Priority = Literal["low", "normal", "high"]
_RANK = {"high": 0, "normal": 1, "low": 2}


class Config(BaseModel):
    default_list: str = "inbox"
    max_items: int = Field(default=1000, ge=1)
    keep_done_days: int = Field(default=30, ge=0)


class TodoModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "todo.json"

    def _now(self) -> datetime:
        return datetime.now()

    # -- storage (re-read each time: another Starlight process may have changed it) -----

    def _load(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _save(self, items: list[dict[str, Any]]) -> None:
        cutoff = (self._now() - timedelta(days=self.config.keep_done_days)).isoformat()
        kept = [i for i in items if not i.get("done") or i.get("done_at", "") >= cutoff]
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(kept, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.file)

    def _find(self, items: list[dict[str, Any]], id: str) -> dict[str, Any]:
        key = id.strip().lower()
        for item in items:
            if item["id"] == key:
                return item
        matches = [i for i in items if key and key in i["text"].lower() and not i["done"]]
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ToolError(f"'{id}' matches {len(matches)} tasks; use the id from todo.list")
        raise ToolError(f"no task '{id}'; see todo.list")

    def _due(self, when: str) -> str | None:
        if not when.strip():
            return None
        moment = parse_when(when, self._now())
        # a day without a time ("tomorrow", "friday", "in 3 days") means that whole day
        text = when.strip().lower()
        timed = re.search(r"\d:\d{2}|\d\s*(am|pm)\b", text) or re.fullmatch(
            r"in\s+\S+\s*(s|secs?|seconds?|m|mins?|minutes?|h|hrs?|hours?)", text
        )
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
        Give `list` to see one list only; `include_done` also shows finished ones."""
        name = list.strip().lower()
        items = [
            i
            for i in self._load()
            if (include_done or not i.get("done")) and (not name or i.get("list") == name)
        ]
        items.sort(key=_order)
        lists = sorted({i.get("list", "") for i in self._load() if not i.get("done")})
        return {"count": len(items), "tasks": [self._view(i) for i in items], "lists": lists}

    @tool
    def due(self, days: int = 0) -> dict[str, Any]:
        """Tasks that are overdue or due within `days` days (0 = today)."""
        limit = (self._now().date() + timedelta(days=max(days, 0))).isoformat()
        items = [i for i in self._load() if not i.get("done") and i.get("due")]
        soon = sorted((i for i in items if i["due"][:10] <= limit), key=_order)
        today = self._now().date().isoformat()
        return {
            "count": len(soon),
            "overdue": [self._view(i) for i in soon if i["due"][:10] < today],
            "due": [self._view(i) for i in soon if i["due"][:10] >= today],
        }

    @tool
    def done(self, id: str) -> dict[str, Any]:
        """Tick off a task by its id, or by a word from its text if only one task matches."""
        items = self._load()
        item = self._find(items, id)
        item["done"] = True
        item["done_at"] = self._now().isoformat(timespec="seconds")
        self._save(items)
        return {"done": True, "text": item["text"]}

    @tool
    def reopen(self, id: str) -> dict[str, Any]:
        """Mark a finished task as not done again."""
        items = self._load()
        item = next((i for i in items if i["id"] == id.strip().lower()), None)
        if item is None:
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
        item = self._find(items, id)
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
        item = self._find(items, id)
        items.remove(item)
        self._save(items)
        return {"removed": True, "text": item["text"]}


def _order(item: dict[str, Any]) -> tuple[str, int, str]:
    return (
        item.get("due") or "9999",
        _RANK.get(item.get("priority", "normal"), 1),
        item["created"],
    )
