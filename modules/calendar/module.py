"""Your calendar: add events, see your agenda, find free time, and get an alert before
something starts. Events live in a standard iCalendar file (data/modules/calendar/
calendar.ics). You can also subscribe to read-only calendar feeds - for example the
"secret address in iCal format" of a Google calendar, or an Outlook/Nextcloud ICS link;
the address is a password, so it goes into the keyring.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.config import write_yaml_config
from starlight.core.fsutil import retry_on_lock, write_atomic
from starlight.core.http import HttpError
from starlight.core.safepaths import PathGuard
from starlight.core.schedule import ScheduleError, names_time_of_day, parse_clock, parse_when

from . import ics

Repeat = Literal["", "daily", "weekdays", "weekly", "monthly", "yearly"]
_RULES = {
    "daily": {"FREQ": "DAILY"},
    "weekdays": {"FREQ": "WEEKLY", "BYDAY": "MO,TU,WE,TH,FR"},
    "weekly": {"FREQ": "WEEKLY"},
    "monthly": {"FREQ": "MONTHLY"},
    "yearly": {"FREQ": "YEARLY"},
}
FEED_KEY = "calendar.feed.{}"
MAX_REMINDER = 366 * 24 * 60  # minutes; how far ahead check_alerts looks for any reminder


def short_id(uid: str) -> str:
    """The id shown for an event. Our own ids are uuids and start differently; a foreign UID
    (imported or from a feed) may share a long prefix with others - every Outlook UID starts
    with the same 32 characters - so it is shown as a short hash of the whole UID."""
    if uid.endswith("@starlight"):
        return uid[:8]
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:8]


class Config(BaseModel):
    remind_minutes: int = Field(default=15, ge=0)
    check_interval: float = Field(default=60, ge=0)
    feed_refresh_minutes: int = Field(default=30, ge=1)
    work_start: str = "08:00"
    work_end: str = "18:00"
    feeds: list[str] = Field(default_factory=list)


class CalendarModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "calendar.ics"
        self.alerted_file = ctx.data_dir / "alerted.json"
        self._feeds: dict[str, tuple[datetime, list[ics.Event]]] = {}
        if self.config.check_interval > 0:
            ctx.every(self.config.check_interval, self.check_alerts, "alerts")

    def _now(self) -> datetime:
        return datetime.now().replace(microsecond=0)

    # -- storage -------------------------------------------------------------------------

    def _local(self) -> list[ics.Event]:
        """Your own events. Only a missing file means "no calendar yet": any other failure
        to read it must stop the tool, or the next save would write a calendar with just
        the new event over the real one."""
        try:
            text = retry_on_lock(lambda: self.file.read_text("utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError) as exc:
            raise ToolError(
                f"could not read the calendar file {self.file.name}, nothing was changed: {exc}"
            ) from exc
        return ics.parse(text)

    def _save(self, events: list[ics.Event]) -> None:
        write_atomic(self.file, ics.write(events), newline="")

    async def _feed(self, name: str) -> list[ics.Event]:
        cached = self._feeds.get(name)
        now = self._now()
        if cached and now - cached[0] < timedelta(minutes=self.config.feed_refresh_minutes):
            return cached[1]
        url = self.ctx.vault.get(FEED_KEY.format(name))
        if not url:
            return []
        try:
            response = await self.ctx.http.get(
                url.replace("webcal://", "https://", 1), respect_robots=False, use_cache=False
            )
        except HttpError as exc:
            self.ctx.logger.warning("calendar feed failed", feed=name, error=str(exc))
            return cached[1] if cached else []
        if not response.ok:
            self.ctx.logger.warning("calendar feed failed", feed=name, status=response.status)
            return cached[1] if cached else []
        events = ics.parse(response.text, source=name)
        self._feeds[name] = (now, events)
        return events

    async def _all(self) -> list[ics.Event]:
        events = self._local()
        for name in self.config.feeds:
            events += await self._feed(name)
        return events

    @staticmethod
    def _within(
        events: list[ics.Event], start: datetime, end: datetime
    ) -> list[tuple[datetime, ics.Event]]:
        found = [(when, e) for e in events for when in e.occurrences(start, end)]
        return sorted(found, key=lambda pair: (not pair[1].all_day, pair[0]))

    async def _between(self, start: datetime, end: datetime) -> list[tuple[datetime, ics.Event]]:
        return self._within(await self._all(), start, end)

    # -- parsing helpers ----------------------------------------------------------------

    def _moment(self, text: str) -> tuple[datetime, bool]:
        """When an event starts; a day without a time makes an all-day event."""
        raw = text.strip().lower()
        if not raw:
            raise ToolError("when is it? e.g. 'tomorrow 15:00' or 'friday'")
        timed = names_time_of_day(raw)  # "24.12.2026" alone is a day, not midnight
        try:
            moment = parse_when(text, self._now())
        except ScheduleError as exc:
            raise ToolError(str(exc)) from exc
        if not timed:
            return datetime.combine(moment.date(), time()), True
        return moment, False

    @staticmethod
    def _span(text: str) -> timedelta:
        match = re.fullmatch(
            r"\s*(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hrs?|hours?|d|days?)\s*", text.lower()
        )
        if not match:
            raise ToolError("give a duration like '30m', '1h' or '2 hours'")
        amount, unit = float(match.group(1)), match.group(2)[0]
        return (
            timedelta(minutes=amount)
            if unit == "m"
            else timedelta(hours=amount)
            if unit == "h"
            else timedelta(days=amount)
        )

    @staticmethod
    def _view(when: datetime, event: ics.Event) -> dict[str, Any]:
        view: dict[str, Any] = {"id": short_id(event.uid), "title": event.title}
        if event.all_day:
            view["date"] = when.date().isoformat()
            view["all_day"] = True
        else:
            end = when + (event.end - event.start)
            view["start"] = when.strftime("%Y-%m-%d %H:%M")
            view["end"] = (
                end.strftime("%H:%M")
                if end.date() == when.date()
                else end.strftime("%Y-%m-%d %H:%M")
            )
        if event.location:
            view["location"] = event.location
        if event.rule:
            view["repeats"] = event.rule.get("FREQ", "").lower()
        if event.source != "local":
            view["calendar"] = event.source
        return view

    def _find(self, events: list[ics.Event], id: str) -> ics.Event:
        key = id.strip().lower()
        whole = [e for e in events if e.uid.lower() == key]
        if whole:
            return whole[0]
        exact = [e for e in events if short_id(e.uid).lower() == key]
        if len({e.uid for e in exact}) > 1:  # two UIDs with one short id: never guess
            raise ToolError(f"'{id}' matches {len(exact)} events; use a word from the title")
        if exact:
            return exact[0]
        named = [e for e in events if key and key in e.title.lower()]
        if len(named) == 1:
            return named[0]
        if named:
            raise ToolError(f"'{id}' matches {len(named)} events; use the id from calendar.agenda")
        raise ToolError(f"no event '{id}' in your calendar (feeds are read-only)")

    # -- tools ---------------------------------------------------------------------------

    @tool
    def add_event(
        self,
        title: str,
        start: str,
        duration: str = "1h",
        end: str = "",
        location: str = "",
        notes: str = "",
        repeat: Repeat = "",
        remind_minutes: int = -1,
    ) -> dict[str, Any]:
        """Add an event. `start` like "tomorrow 15:00", "friday 9am", "2026-12-24 18:00";
        a day without a time ("2026-12-24", "friday") is an all-day event. Give `duration`
        ("30m", "2h") or `end`. `repeat`: daily, weekdays, weekly, monthly or yearly.
        `remind_minutes`: alert this long before (-1: the default, 0: no alert)."""
        if not title.strip():
            raise ToolError("what is the event called?")
        begin, all_day = self._moment(start)
        if end.strip():
            finish, _ = self._moment(end)
            if finish <= begin:
                raise ToolError("the end must be after the start")
        else:
            finish = begin + (timedelta(days=1) if all_day else self._span(duration))
        event = ics.Event(
            uid=f"{uuid.uuid4().hex}@starlight",
            title=title.strip(),
            start=begin,
            end=finish,
            all_day=all_day,
            location=location.strip(),
            notes=notes.strip(),
            rule=dict(_RULES.get(repeat, {})),
            remind_minutes=None if remind_minutes < 0 else remind_minutes,
        )
        events = self._local()
        events.append(event)
        self._save(events)
        return {"added": True, **self._view(event.start, event)}

    @tool
    async def agenda(self, days: int = 1, start: str = "today") -> dict[str, Any]:
        """Your events for `days` days from `start` ("today", "tomorrow", "monday",
        "2026-12-24"), including subscribed calendars, in time order."""
        first = self._day(start)
        last = first + timedelta(days=max(1, min(days, 366)))
        found = await self._between(first, last)
        return {
            "from": first.date().isoformat(),
            "days": max(1, min(days, 366)),
            "count": len(found),
            "events": [self._view(when, e) for when, e in found],
        }

    def _day(self, text: str) -> datetime:
        raw = text.strip().lower()
        if raw in ("", "today"):
            return datetime.combine(self._now().date(), time())
        try:
            return datetime.combine(parse_when(raw, self._now()).date(), time())
        except ScheduleError as exc:
            raise ToolError(str(exc)) from exc

    @tool
    async def free_slots(self, day: str = "today", min_minutes: int = 30) -> dict[str, Any]:
        """Free time on a day between your working hours (work_start..work_end), at least
        `min_minutes` long."""
        base = self._day(day)
        begin_clock = parse_clock(self.config.work_start) or (8, 0)
        end_clock = parse_clock(self.config.work_end) or (18, 0)
        window_start = base.replace(hour=begin_clock[0], minute=begin_clock[1])
        window_end = base.replace(hour=end_clock[0], minute=end_clock[1])
        if day.strip().lower() in ("", "today"):
            window_start = max(window_start, self._now())
        busy = sorted(
            (when, when + (e.end - e.start))
            for when, e in await self._between(window_start, window_end)
            if not e.all_day
        )
        slots: list[dict[str, str]] = []
        cursor = window_start
        for begin, finish in [*busy, (window_end, window_end)]:
            if begin - cursor >= timedelta(minutes=min_minutes):
                slots.append(
                    {
                        "from": cursor.strftime("%H:%M"),
                        "to": min(begin, window_end).strftime("%H:%M"),
                    }
                )
            cursor = max(cursor, finish)
        return {"date": base.date().isoformat(), "free": slots}

    @tool
    def remove(self, id: str, only_on: str = "") -> dict[str, Any]:
        """Delete an event from your calendar (by id from calendar.agenda, or a word from
        its title). For a repeating event, `only_on` ("2026-10-12") skips just that day."""
        events = self._local()
        event = self._find(events, id)
        if only_on.strip() and event.rule:
            day = self._day(only_on).date()
            hits = event.occurrences(
                datetime.combine(day, time()), datetime.combine(day, time()) + timedelta(days=1)
            )
            if not hits:
                raise ToolError(f"'{event.title}' does not happen on {day}")
            event.exdates.add(hits[0])
            self._save(events)
            return {"skipped": True, "title": event.title, "date": day.isoformat()}
        events.remove(event)
        self._save(events)
        return {"removed": True, "title": event.title}

    @tool
    def import_ics(self, path: str) -> dict[str, Any]:
        """Add the events from an .ics file (exported from another calendar) to yours."""
        guard = PathGuard(self.ctx.paths.root, self.ctx.output_dir)
        source = guard.resolve(path).path
        if not source.is_file():
            raise ToolError(f"{source} does not exist")
        if source.stat().st_size > 20 * 1024**2:
            raise ToolError("that file is too big for a calendar (over 20 MB)")
        incoming = ics.parse(source.read_text("utf-8", errors="replace"))
        events = self._local()
        known = {e.uid for e in events}
        added = [e for e in incoming if e.uid not in known]
        self._save(events + added)
        return {"imported": len(added), "already_there": len(incoming) - len(added)}

    @tool
    async def subscribe(self, name: str, url: str) -> dict[str, Any]:
        """Show a calendar from elsewhere (read-only): `url` is its iCal/ICS address, e.g.
        Google Calendar -> Settings -> "Secret address in iCal format". The address is
        stored in the keyring. Asks first."""
        key = re.sub(r"[^a-z0-9_-]", "", name.strip().lower().replace(" ", "-"))
        if not key:
            raise ToolError("give the calendar a short name, e.g. 'work'")
        link = url.strip()
        if not re.match(r"(https|webcal)://", link):
            raise ToolError("the address must start with https:// or webcal://")
        if not await self.ctx.ui.confirm(f"Subscribe to the calendar '{key}' (read-only)?"):
            raise ToolError("not confirmed, nothing changed")
        self.ctx.vault.set(FEED_KEY.format(key), link)
        self._feeds.pop(key, None)
        events = await self._feed(key)
        if key not in self.config.feeds:
            self.config.feeds.append(key)
            self._write_config()
        return {"subscribed": key, "events": len(events)}

    @tool
    def calendars(self) -> dict[str, Any]:
        """Your own calendar and the feeds you subscribed to."""
        return {"local": str(self.file), "feeds": list(self.config.feeds)}

    @tool
    async def unsubscribe(self, name: str) -> dict[str, Any]:
        """Stop showing a subscribed calendar and forget its address."""
        key = name.strip().lower()
        if key not in self.config.feeds:
            raise ToolError(f"no subscribed calendar '{name}'")
        self.config.feeds.remove(key)
        self._feeds.pop(key, None)
        self._write_config()
        self.ctx.vault.delete(FEED_KEY.format(key))
        return {"unsubscribed": key}

    def _write_config(self) -> None:
        write_yaml_config(
            self.ctx.paths.module_configs / "calendar.yaml",
            self.config.model_dump(),
            "# The calendar module (modules/calendar). Feed addresses are in the keyring.",
        )

    # -- alerts --------------------------------------------------------------------------

    async def check_alerts(self, now: datetime | None = None) -> list[str]:
        """Deliver an alert for each event starting within its reminder time."""
        moment = now or self._now()
        try:
            events = await self._all()
        except ToolError as exc:  # an unreadable calendar file: try again next round
            self.ctx.logger.warning("calendar alerts skipped", error=str(exc))
            return []
        # look as far ahead as the longest reminder (an event may ask for a day before)
        ahead = max(120, self.config.remind_minutes, *(e.remind_minutes or 0 for e in events))
        horizon = moment + timedelta(minutes=min(ahead, MAX_REMINDER))
        alerted = self._alerted(moment)
        sent: list[str] = []
        for when, event in self._within(events, moment, horizon):
            minutes = (
                self.config.remind_minutes if event.remind_minutes is None else event.remind_minutes
            )
            if event.all_day or minutes <= 0 or when - timedelta(minutes=minutes) > moment:
                continue
            key = f"{event.uid}|{when.isoformat()}"
            if key in alerted or when < moment:
                continue
            alerted[key] = when.isoformat()
            left = max(0, int((when - moment).total_seconds() // 60))
            place = f" ({event.location})" if event.location else ""
            text = f"{event.title}{place} starts at {when:%H:%M} (in {left} min)"
            await self.ctx.deliver(text, title="Calendar", urgent=left <= 5)
            await self.ctx.publish(
                "calendar.soon", {"title": event.title, "start": when.isoformat()}
            )
            sent.append(event.title)
        if sent:
            self.alerted_file.write_text(json.dumps(alerted), encoding="utf-8")
        return sent

    def _alerted(self, now: datetime) -> dict[str, str]:
        try:
            data = json.loads(self.alerted_file.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        cutoff = (now - timedelta(days=1)).isoformat()
        return {k: v for k, v in data.items() if isinstance(v, str) and v >= cutoff}
