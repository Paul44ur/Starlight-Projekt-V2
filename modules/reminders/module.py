"""Reminders and recurring nudges that Starlight brings back to you at the right time.

Say "remind me in 2 hours to ...", "remind me tomorrow at 9am ...", or set a repeating one
("every day at 8:00 ..."). When a reminder is due it shows a desktop notification (through
the notify module's channel) and is announced on the event bus, so the WhatsApp bridge can
forward it to your phone too. Reminders are saved to disk, so they survive a restart.

Everything is local: no account, no network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_UNITS = {"s": 1, "sec": 1, "m": 60, "min": 60, "h": 3600, "hr": 3600, "hour": 3600, "d": 86400}


class Config(BaseModel):
    check_interval: float = Field(default=20, ge=0)  # seconds between checks; 0 turns the loop off
    max_reminders: int = Field(default=200, ge=1)


class RemindersModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "reminders.json"
        self.items: list[dict[str, Any]] = self._load()
        self._task: asyncio.Task[None] | None = None
        if self.config.check_interval > 0:
            self._task = asyncio.create_task(self._loop(), name="reminders-loop")

    async def shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

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
        """Set a repeating reminder. `schedule` is a time like "8:00" or "9am" (every day),
        optionally with a weekday like "monday 9am" (every week). `text` is the reminder."""
        if not text.strip():
            raise ToolError("what should the reminder say?")
        hour, minute, weekday = parse_schedule(schedule)
        now = self._now()
        due = next_occurrence(now, hour, minute, weekday)
        item = {
            "id": uuid.uuid4().hex[:8],
            "text": text.strip(),
            "kind": "weekly" if weekday is not None else "daily",
            "due": due.timestamp(),
            "time": f"{hour:02d}:{minute:02d}",
            "weekday": weekday,
            "when": due.strftime("%Y-%m-%d %H:%M"),
            "created": now.isoformat(timespec="seconds"),
        }
        self.items.append(item)
        self._save()
        return {"set": True, "id": item["id"], "repeats": item["kind"], "next": item["when"]}

    @tool(name="list")
    def list_reminders(self) -> dict[str, Any]:
        """The reminders you have set, soonest first."""
        pending = sorted(self.items, key=lambda r: r.get("due", 0))
        return {
            "count": len(pending),
            "reminders": [
                {
                    "id": r["id"],
                    "text": r["text"],
                    "next": r.get("when"),
                    "repeats": r["kind"] if r["kind"] != "once" else None,
                }
                for r in pending
            ],
        }

    @tool
    def cancel(self, id: str) -> dict[str, Any]:
        """Cancel a reminder by its id (see reminders.list)."""
        for index, item in enumerate(self.items):
            if item["id"] == id.strip():
                self.items.pop(index)
                self._save()
                return {"cancelled": True, "id": id, "text": item["text"]}
        raise ToolError(f"no reminder with id '{id}'")

    # -- firing --------------------------------------------------------------------------

    async def _fire(self, item: dict[str, Any]) -> None:
        message = f"Reminder: {item['text']}"
        with contextlib.suppress(Exception):
            await self.ctx.ui.notify(message)
        await self.ctx.publish("reminder.due", {"id": item["id"], "text": item["text"]})
        self.ctx.logger.info("reminder fired", id=item["id"])

    async def check_due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """Fire every reminder that is due. One-offs are removed; repeating ones are moved to
        their next time. Returns the reminders that fired."""
        moment = (now or self._now()).timestamp()
        fired: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        changed = False
        for item in self.items:
            if item.get("due", 0) > moment:
                remaining.append(item)
                continue
            fired.append(item)
            changed = True
            if item["kind"] == "once":
                continue  # drop it
            hour, minute = (int(p) for p in item["time"].split(":"))
            after = datetime.fromtimestamp(moment) + timedelta(seconds=1)
            nxt = next_occurrence(after, hour, minute, item.get("weekday"))
            item["due"] = nxt.timestamp()
            item["when"] = nxt.strftime("%Y-%m-%d %H:%M")
            remaining.append(item)
        if changed:
            self.items = remaining
            self._save()
        for item in fired:
            await self._fire(item)
        return fired

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.check_interval)
            with contextlib.suppress(Exception):
                await self.check_due()


# -- parsing (pure functions, easy to test) ----------------------------------------------


def parse_when(text: str, now: datetime) -> datetime:
    """Turn a natural "when" into a datetime, relative to `now`."""
    raw = text.strip().lower()
    if not raw:
        raise ToolError("when should it remind you?")

    match = re.fullmatch(r"in\s+(\d+)\s*([a-z]+)", raw)
    if match:
        amount, unit = int(match.group(1)), match.group(2).rstrip("s")
        seconds = _UNITS.get(unit)
        if seconds is None:
            raise ToolError("use seconds, minutes, hours or days, e.g. 'in 2 hours'")
        return now + timedelta(seconds=amount * seconds)

    day = now
    if raw.startswith("tomorrow"):
        day = now + timedelta(days=1)
        raw = raw[len("tomorrow") :].strip() or "09:00"
    elif raw.startswith("today"):
        raw = raw[len("today") :].strip() or "09:00"

    clock = _parse_clock(raw)
    if clock is not None:
        hour, minute = clock
        target = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now and day == now:
            target += timedelta(days=1)  # a time already past means tomorrow
        return target

    for parser in (datetime.fromisoformat, _parse_date_time):
        with contextlib.suppress(ValueError):
            return parser(text.strip())
    raise ToolError(
        "could not understand that time; try 'in 2h', 'tomorrow 9am', '18:30' or '2026-12-24 20:00'"
    )


def parse_schedule(text: str) -> tuple[int, int, int | None]:
    """A repeating schedule -> (hour, minute, weekday or None)."""
    raw = text.strip().lower().replace("every", " ").replace("at", " ").replace("day", " ")
    weekday: int | None = None
    for index, name in enumerate(_WEEKDAYS):
        if name in raw or name[:3] in raw.split():
            weekday = index
            raw = raw.replace(name, " ").replace(name[:3], " ")
            break
    clock = _parse_clock(raw.strip()) or (_parse_clock(text.strip().lower()))
    if clock is None:
        raise ToolError("give a time, e.g. 'every day at 8:00' or 'monday 9am'")
    return clock[0], clock[1], weekday


def _parse_clock(text: str) -> tuple[int, int] | None:
    text = text.strip()
    ampm = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text)
    if ampm:
        hour = int(ampm.group(1)) % 12
        if ampm.group(3) == "pm":
            hour += 12
        return hour, int(ampm.group(2) or 0)
    hhmm = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if hhmm:
        hour, minute = int(hhmm.group(1)), int(hhmm.group(2))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour, minute
    return None


def _parse_date_time(text: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        with contextlib.suppress(ValueError):
            return datetime.strptime(text, fmt)
    raise ValueError(text)


def next_occurrence(now: datetime, hour: int, minute: int, weekday: int | None) -> datetime:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if weekday is None:
        if target <= now:
            target += timedelta(days=1)
        return target
    days_ahead = (weekday - now.weekday()) % 7
    target += timedelta(days=days_ahead)
    if target <= now:
        target += timedelta(days=7)
    return target
