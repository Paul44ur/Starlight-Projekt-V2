"""Reminders and recurring nudges that Starlight brings back to you at the right time.

Say "remind me in 2 hours to ...", "remind me tomorrow at 9am ...", or set a repeating one
("every day at 8:00", "weekdays 7:30", "every 2 hours", "every month on the 1st at 9:00").
When a reminder is due it is delivered to every open channel: the terminal, the web chat,
a desktop notification and WhatsApp when it is set up. Reminders are saved to disk, so
they survive a restart; only the primary Starlight process fires them, so they never come
twice.

Everything is local: no account, no network.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.schedule import Schedule, parse_schedule, parse_when

__all__ = ["RemindersModule", "parse_schedule", "parse_when"]


class Config(BaseModel):
    check_interval: float = Field(default=20, ge=0)  # seconds between checks; 0 turns the loop off
    max_reminders: int = Field(default=200, ge=1)


class RemindersModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "reminders.json"
        self.items: list[dict[str, Any]] = self._load()
        if self.config.check_interval > 0:
            # Only the primary core fires reminders, so two open Starlights never fire twice.
            ctx.every(self.config.check_interval, self.check_due, "reminders")

    def _now(self) -> datetime:
        return datetime.now()

    # -- storage -------------------------------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        if not self.file.exists():
            return []
        try:
            data = json.loads(self.file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return data if isinstance(data, list) else []

    def _refresh(self) -> None:
        """Re-read the file: another Starlight process may have added or cancelled some."""
        self.items = self._load()

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(self.items, indent=2), encoding="utf-8")

    # -- tools ---------------------------------------------------------------------------

    @tool
    def remind(self, when: str, text: str) -> dict[str, Any]:
        """Set a one-off reminder. `when` can be "in 2h", "in 30m", "in 90s", "tomorrow
        9am", "18:30", or a date-time like "2026-12-24 20:00". `text` is what to remind you
        about."""
        if not text.strip():
            raise ToolError("what should the reminder say?")
        self._refresh()
        if len(self.items) >= self.config.max_reminders:
            raise ToolError(f"too many reminders (limit {self.config.max_reminders}); cancel some")
        due = parse_when(when, self._now())
        item = {
            "id": uuid.uuid4().hex[:8],
            "text": text.strip(),
            "kind": "once",
            "due": due.timestamp(),
            "when": due.strftime("%Y-%m-%d %H:%M"),
            "created": self._now().isoformat(timespec="seconds"),
        }
        self.items.append(item)
        self._save()
        return {"set": True, "id": item["id"], "due": item["when"], "text": item["text"]}

    @tool
    def every(self, schedule: str, text: str) -> dict[str, Any]:
        """Set a repeating reminder. `schedule` can be "8:00" or "every day at 9am",
        "weekdays 7:30", "monday 9am", "mon,wed,fri 18:00", "every 2 hours" or "every month
        on the 1st at 9:00". `text` is the reminder."""
        if not text.strip():
            raise ToolError("what should the reminder say?")
        plan = parse_schedule(schedule if any(c.isalpha() for c in schedule) else f"at {schedule}")
        self._refresh()
        if len(self.items) >= self.config.max_reminders:
            raise ToolError(f"too many reminders (limit {self.config.max_reminders}); cancel some")
        now = self._now()
        due = plan.next_after(now)
        item = {
            "id": uuid.uuid4().hex[:8],
            "text": text.strip(),
            "kind": "repeat",
            "schedule": plan.to_dict(),
            "repeats": plan.describe(),
            "due": due.timestamp(),
            "when": due.strftime("%Y-%m-%d %H:%M"),
            "created": now.isoformat(timespec="seconds"),
        }
        self.items.append(item)
        self._save()
        return {"set": True, "id": item["id"], "repeats": item["repeats"], "next": item["when"]}

    @tool(name="list")
    def list_reminders(self) -> dict[str, Any]:
        """The reminders you have set, soonest first."""
        self._refresh()
        pending = sorted(self.items, key=lambda r: r.get("due", 0))
        return {
            "count": len(pending),
            "reminders": [
                {
                    "id": r["id"],
                    "text": r["text"],
                    "next": r.get("when"),
                    "repeats": r.get("repeats") if r["kind"] != "once" else None,
                }
                for r in pending
            ],
        }

    @tool
    def cancel(self, id: str) -> dict[str, Any]:
        """Cancel a reminder by its id (see reminders.list)."""
        self._refresh()
        for index, item in enumerate(self.items):
            if item["id"] == id.strip():
                self.items.pop(index)
                self._save()
                return {"cancelled": True, "id": id, "text": item["text"]}
        raise ToolError(f"no reminder with id '{id}'")

    # -- firing --------------------------------------------------------------------------

    async def _fire(self, item: dict[str, Any]) -> None:
        await self.ctx.deliver(f"Reminder: {item['text']}", title="Reminder")
        await self.ctx.publish("reminder.due", {"id": item["id"], "text": item["text"]})
        self.ctx.logger.info("reminder fired", id=item["id"])

    async def check_due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Fire every reminder that is due. One-offs are removed; repeating ones are moved to
        their next time. Returns the reminders that fired."""
        self._refresh()
        moment = now or self._now()
        fired: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for item in self.items:
            if item.get("due", 0) > moment.timestamp():
                remaining.append(item)
                continue
            fired.append(item)
            if item["kind"] == "once":
                continue  # drop it
            nxt = _schedule_of(item).next_after(moment + timedelta(seconds=1))
            item["due"] = nxt.timestamp()
            item["when"] = nxt.strftime("%Y-%m-%d %H:%M")
            remaining.append(item)
        if fired:
            self.items = remaining
            self._save()
        for item in fired:
            await self._fire(item)
        return fired


def _schedule_of(item: dict[str, Any]) -> Schedule:
    """The repeat of a reminder; reminders saved by older versions kept time + weekday."""
    if "schedule" in item:
        return Schedule.from_dict(item["schedule"])
    hour, minute = (int(p) for p in item["time"].split(":"))
    weekday = item.get("weekday")
    return Schedule("daily", hour, minute, weekdays=(weekday,) if weekday is not None else ())
