"""Understanding "when": one-off times ("in 2h", "tomorrow 9am") and repeating schedules
("every day at 8:00", "weekdays 7:30", "mon,wed,fri 18:00", "every 15 minutes",
"every month on the 1st at 9:00"). Pure functions on local time, no dependencies; used by
reminders, automations, calendar and anything else that plans ahead."""

from __future__ import annotations

import calendar
import contextlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from starlight.core.tools import ToolError

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
UNITS = {
    **dict.fromkeys(("s", "sec", "secs", "second", "seconds"), 1),
    **dict.fromkeys(("m", "min", "mins", "minute", "minutes"), 60),
    **dict.fromkeys(("h", "hr", "hrs", "hour", "hours"), 3600),
    **dict.fromkeys(("d", "day", "days"), 86400),
    **dict.fromkeys(("w", "week", "weeks"), 7 * 86400),
}
_FILLER = {"every", "each", "at", "on", "day", "daily", "the", "and", "of", "in"}
_GROUPS = {
    "weekday": (0, 1, 2, 3, 4),
    "weekdays": (0, 1, 2, 3, 4),
    "workday": (0, 1, 2, 3, 4),
    "workdays": (0, 1, 2, 3, 4),
    "weekend": (5, 6),
    "weekends": (5, 6),
}
MIN_INTERVAL = 60  # a repeating schedule runs at most once a minute


class ScheduleError(ToolError):
    pass


# -- one-off times ------------------------------------------------------------------------


def parse_when(text: str, now: datetime) -> datetime:
    """Turn a natural "when" into a datetime, relative to `now`."""
    raw = text.strip().lower()
    if not raw:
        raise ScheduleError("when should it happen?")

    match = re.fullmatch(r"in\s+(\d+|an?|one)\s*([a-z]+)", raw)
    if match:
        count, unit = match.group(1), match.group(2)
        amount = int(count) if count.isdigit() else 1  # "in an hour"
        seconds = UNITS.get(unit)
        if seconds is None:
            raise ScheduleError("use seconds, minutes, hours or days, e.g. 'in 2 hours'")
        return now + timedelta(seconds=amount * seconds)

    words = raw.removeprefix("next ").removeprefix("on ").split(maxsplit=1)
    weekday = _weekday(words[0]) if words and len(words[0]) >= 3 else None
    if weekday is not None:  # "friday", "next monday 10am", "on tue at 18:00"
        rest = words[1].removeprefix("at ").strip() if len(words) > 1 else "09:00"
        clock = parse_clock(rest)
        if clock is None:
            raise ScheduleError(f"could not understand the time '{rest}'")
        return Schedule("daily", clock[0], clock[1], weekdays=(weekday,)).next_after(now)

    day: datetime | None = None  # a named day: its time is kept even if already past
    for word, offset in (("tomorrow", 1), ("today", 0), ("yesterday", -1)):
        if raw.startswith(word):
            day = now + timedelta(days=offset)
            raw = raw[len(word) :].strip() or "09:00"
            break

    clock = parse_clock(raw.removeprefix("at ").strip())
    if clock is not None:
        hour, minute = clock
        target = (day or now).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day is None and target <= now:
            target += timedelta(days=1)  # a bare time already past means tomorrow
        return target

    for parser in (datetime.fromisoformat, parse_date_time):
        with contextlib.suppress(ValueError):
            return parser(text.strip())
    raise ScheduleError(
        "could not understand that time; try 'in 2h', 'tomorrow 9am', '18:30' or '2026-12-24 20:00'"
    )


# A time of day in a when-text: "18:30", "18.30" (but not the "4.12" in 24.12.2026), "6pm".
_CLOCK = re.compile(r"(?<![\d.])\d{1,2}[:.]\d{2}(?![.\d])|\d\s*(?:am|pm)\b")


def has_clock_time(text: str) -> bool:
    """Whether a when-text names a time of day; without one it means the whole day."""
    return bool(_CLOCK.search(text.lower()))


def parse_clock(text: str) -> tuple[int, int] | None:
    text = text.strip()
    ampm = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text)
    if ampm:
        hour, minute = int(ampm.group(1)), int(ampm.group(2) or 0)
        if not (1 <= hour <= 12 and minute < 60):  # "13pm", "9:75pm"
            return None
        return hour % 12 + (12 if ampm.group(3) == "pm" else 0), minute
    hhmm = re.fullmatch(r"(\d{1,2})[:.](\d{2})(?:\s*uhr)?", text)
    if hhmm:
        hour, minute = int(hhmm.group(1)), int(hhmm.group(2))
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour, minute
    return None


def parse_date_time(text: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        with contextlib.suppress(ValueError):
            return datetime.strptime(text, fmt)
    raise ValueError(text)


# -- repeating schedules ------------------------------------------------------------------


@dataclass(frozen=True)
class Schedule:
    """When something repeats. ``kind``:
    - "daily": at hour:minute on ``weekdays`` (empty = every day)
    - "monthly": at hour:minute on ``day`` of each month (clamped to short months)
    - "interval": every ``seconds``, counted from when it was set
    """

    kind: Literal["daily", "monthly", "interval"]
    hour: int = 0
    minute: int = 0
    weekdays: tuple[int, ...] = field(default=())
    day: int = 1
    seconds: int = 0

    def next_after(self, now: datetime) -> datetime:
        """The first time strictly after ``now``."""
        if self.kind == "interval":
            return now.replace(microsecond=0) + timedelta(seconds=self.seconds)
        if self.kind == "monthly":
            year, month = now.year, now.month
            for _ in range(13):
                last = calendar.monthrange(year, month)[1]
                target = datetime(year, month, min(self.day, last), self.hour, self.minute)
                if target > now:
                    return target
                year, month = (year + 1, 1) if month == 12 else (year, month + 1)
            raise AssertionError("unreachable")
        target = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
        for offset in range(8):
            candidate = target + timedelta(days=offset)
            if candidate > now and (not self.weekdays or candidate.weekday() in self.weekdays):
                return candidate
        raise AssertionError("unreachable")

    def describe(self) -> str:
        clock = f"{self.hour:02d}:{self.minute:02d}"
        if self.kind == "interval":
            for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
                if self.seconds % size == 0:
                    count = self.seconds // size
                    return f"every {count} {unit}{'s' if count != 1 else ''}"
            return f"every {self.seconds} seconds"
        if self.kind == "monthly":
            return f"monthly on day {self.day} at {clock}"
        if not self.weekdays:
            return f"every day at {clock}"
        if self.weekdays == (0, 1, 2, 3, 4):
            return f"weekdays at {clock}"
        if self.weekdays == (5, 6):
            return f"weekends at {clock}"
        names = ", ".join(WEEKDAYS[d].capitalize() for d in self.weekdays)
        return f"{names} at {clock}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "hour": self.hour,
            "minute": self.minute,
            "weekdays": list(self.weekdays),
            "day": self.day,
            "seconds": self.seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Schedule:
        return cls(
            kind=data.get("kind", "daily"),
            hour=int(data.get("hour", 0)),
            minute=int(data.get("minute", 0)),
            weekdays=tuple(int(d) for d in data.get("weekdays") or ()),
            day=int(data.get("day", 1)),
            seconds=int(data.get("seconds", 0)),
        )


def _weekday(word: str) -> int | None:
    word = word.removesuffix("s") if word.removesuffix("s") in WEEKDAYS else word
    for index, name in enumerate(WEEKDAYS):
        if word in (name, name[:3], name[:2]) and len(word) >= 2:
            return index
    return None


def parse_schedule(text: str) -> Schedule:
    """A repeating schedule in plain words -> Schedule."""
    raw = text.strip().lower()
    if not raw:
        raise ScheduleError("give a schedule, e.g. 'every day at 8:00' or 'weekdays 7:30'")

    interval = re.fullmatch(r"(?:every|each)\s+(\d+|an?|one)?\s*([a-z]+)", raw)
    if interval and interval.group(2) in UNITS:
        count = interval.group(1) or "1"
        amount = int(count) if count.isdigit() else 1
        seconds = amount * UNITS[interval.group(2)]
        if seconds < MIN_INTERVAL:
            raise ScheduleError("the shortest repeat is once a minute")
        if seconds in (86400, 7 * 86400) and not interval.group(1):
            pass  # "every day"/"every week" without a time: handled below
        else:
            return Schedule("interval", seconds=seconds)

    # Work on whole words: removing "at" or "day" from inside a word would turn
    # "saturday" into "ur" and "tuesday" into "tues".
    words = re.sub(r"[,/&+]", " ", raw).split()
    monthly = "month" in words or "monthly" in words
    day_of_month: int | None = None
    weekdays: list[int] = []
    rest: list[str] = []
    for word in words:
        if word in ("month", "monthly", "week", "weekly"):
            continue
        if word in _GROUPS:
            weekdays.extend(_GROUPS[word])
            continue
        index = _weekday(word)
        if index is not None:
            weekdays.append(index)
            continue
        ordinal = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th|\.)", word)
        if monthly and ordinal:
            day_of_month = int(ordinal.group(1))
            continue
        if word not in _FILLER:
            rest.append(word)

    clock = parse_clock(" ".join(rest)) if rest else None
    if clock is None:
        raise ScheduleError(
            "give a time, e.g. 'every day at 8:00', 'weekdays 7:30', 'monday 9am', "
            "'every 2 hours' or 'every month on the 1st at 9:00'"
        )
    hour, minute = clock
    if monthly:
        day = day_of_month or 1
        if not 1 <= day <= 31:
            raise ScheduleError("a day of the month is 1 to 31")
        return Schedule("monthly", hour, minute, day=day)
    return Schedule("daily", hour, minute, weekdays=tuple(sorted(set(weekdays))))


def next_occurrence(now: datetime, hour: int, minute: int, weekday: int | None) -> datetime:
    """The next hour:minute (on ``weekday`` if given) after ``now``."""
    days = (weekday,) if weekday is not None else ()
    return Schedule("daily", hour, minute, weekdays=days).next_after(now)
