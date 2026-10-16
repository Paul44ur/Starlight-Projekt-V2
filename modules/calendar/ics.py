"""A small iCalendar (RFC 5545) reader and writer: enough for events from Google, Outlook,
Apple and Nextcloud feeds - single and repeating events, all-day events and time zones.
Standard library only."""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
MAX_OCCURRENCES = 2000  # safety net for endless repeat rules


@dataclass
class Event:
    uid: str
    title: str
    start: datetime  # local, naive
    end: datetime
    all_day: bool = False
    location: str = ""
    notes: str = ""
    rule: dict[str, str] = field(default_factory=dict)  # RRULE parts, e.g. {"FREQ": "WEEKLY"}
    exdates: set[datetime] = field(default_factory=set)
    remind_minutes: int | None = None  # None: the calendar's default
    source: str = "local"

    def occurrences(self, window_start: datetime, window_end: datetime) -> list[datetime]:
        """Start times of this event that overlap the window."""
        length = self.end - self.start
        if not self.rule:
            if self.start < window_end and self.start + length > window_start:
                return [self.start]
            return []
        found: list[datetime] = []
        for start in _expand(self.start, self.rule, after=window_start - length):
            if start >= window_end:
                break
            if start + length > window_start and start not in self.exdates:
                found.append(start)
        return found


# -- reading -----------------------------------------------------------------------------


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split(line: str) -> tuple[str, dict[str, str], str]:
    """NAME;PARAM=...;PARAM="...":value. A quoted parameter may hold ":" and ";" (Outlook:
    TZID="(UTC+01:00) Amsterdam, Berlin, Bern, Rom, Stockholm, Wien")."""
    parts: list[str] = []
    quoted, begin = False, 0
    for i, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif not quoted and char in ";:":
            parts.append(line[begin:i])
            begin = i + 1
            if char == ":":
                break
    else:
        return line.upper(), {}, ""
    name, *params = parts
    options = {}
    for param in params:
        key, _, val = param.partition("=")
        options[key.upper()] = val.strip('"')
    return name.upper(), options, line[begin:]


_ESCAPED = re.compile(r"\\([\\;,nN])")


def _unescape(value: str) -> str:
    # One pass, so a Windows path such as D:\neu (saved as D:\\neu) keeps its backslash.
    return _ESCAPED.sub(lambda m: "\n" if m.group(1) in "nN" else m.group(1), value)


def parse_time(value: str, params: dict[str, str]) -> tuple[datetime, bool]:
    """(local naive datetime, all_day)."""
    value = value.strip()
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        day = datetime.strptime(value[:8], "%Y%m%d")
        return day, True
    moment = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return moment.replace(tzinfo=UTC).astimezone().replace(tzinfo=None), False
    zone = params.get("TZID")
    if zone:
        try:
            aware = moment.replace(tzinfo=ZoneInfo(zone))
            return aware.astimezone().replace(tzinfo=None), False
        except (ZoneInfoNotFoundError, ValueError, OSError):
            pass  # unknown zone: take the time as local
    return moment, False


def parse(text: str, source: str = "local") -> list[Event]:
    """The events in an iCalendar text. A property that can't be read (an odd date, a
    time zone written in a way we don't know) is skipped, and an event without a usable
    start is left out - never the whole calendar."""
    events: list[Event] = []
    current: dict[str, Any] | None = None
    depth = 0  # VALARM and friends inside an event are skipped
    for line in _unfold(text):
        name, params, value = _split(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            current, depth = {"exdates": set()}, 0
            continue
        if current is None:
            continue
        if name == "BEGIN":
            depth += 1
        elif name == "END" and depth:
            depth -= 1
        elif name == "END" and value.upper() == "VEVENT":
            try:
                event = _build(current, source)
            except (ValueError, TypeError, OverflowError):
                event = None
            if event is not None:
                events.append(event)
            current = None
        elif not depth:
            with contextlib.suppress(ValueError, TypeError, OverflowError):
                _read_property(current, name, params, value)
    return events


def _read_property(current: dict[str, Any], name: str, params: dict[str, str], value: str) -> None:
    if name in ("DTSTART", "DTEND"):
        current[name] = parse_time(value, params)
    elif name == "DURATION":
        current["DURATION"] = parse_duration(value)
    elif name == "EXDATE":
        for part in value.split(","):
            with contextlib.suppress(ValueError):
                current["exdates"].add(parse_time(part, params)[0])
    elif name == "RRULE":
        current["RRULE"] = dict(p.split("=", 1) for p in value.upper().split(";") if "=" in p)
    elif name in ("UID", "SUMMARY", "LOCATION", "DESCRIPTION", "X-STARLIGHT-REMIND"):
        current[name] = _unescape(value)


def _build(data: dict[str, Any], source: str) -> Event | None:
    if "DTSTART" not in data:
        return None
    start, all_day = data["DTSTART"]
    if "DTEND" in data:
        end = data["DTEND"][0]
    elif "DURATION" in data:
        end = start + data["DURATION"]
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
    remind = data.get("X-STARLIGHT-REMIND")
    return Event(
        uid=data.get("UID") or f"{start.isoformat()}-{data.get('SUMMARY', '')}",
        title=data.get("SUMMARY", "(no title)"),
        start=start,
        end=max(end, start),
        all_day=all_day,
        location=data.get("LOCATION", ""),
        notes=data.get("DESCRIPTION", ""),
        rule=data.get("RRULE", {}),
        exdates=data["exdates"],
        remind_minutes=int(remind) if remind and remind.isdigit() else None,
        source=source,
    )


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(
        r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", value.strip()
    )
    if not match:
        return timedelta(hours=1)
    sign, weeks, days, hours, minutes, seconds = match.groups()
    span = timedelta(
        weeks=int(weeks or 0),
        days=int(days or 0),
        hours=int(hours or 0),
        minutes=int(minutes or 0),
        seconds=int(seconds or 0),
    )
    return -span if sign == "-" else span


# -- repeating events --------------------------------------------------------------------


def _add_months(moment: datetime, months: int) -> datetime | None:
    index = moment.month - 1 + months
    year, month = moment.year + index // 12, index % 12 + 1
    try:
        return moment.replace(year=year, month=month)
    except ValueError:
        return None  # e.g. the 31st in a 30-day month: RFC 5545 skips it


_BYDAY = re.compile(r"([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)")


def _skip(start: datetime, after: datetime | None, freq: str, interval: int) -> int:
    """How many whole repeats lie before ``after``: a rule from 2020 asked about today
    starts counting near today (endless rules are capped at MAX_OCCURRENCES from there)."""
    if after is None or after <= start:
        return 0
    if freq == "WEEKLY":
        turns = (after - start).days // 7
    elif freq == "MONTHLY":
        turns = (after.year - start.year) * 12 + after.month - start.month
    elif freq == "YEARLY":
        turns = after.year - start.year
    else:
        turns = (after - start).days
    return max(0, turns // interval - 1)


def _month_days(month: datetime, by_day: list[tuple[int, int]], at: datetime) -> list[datetime]:
    """The days of ``month`` a MONTHLY rule's BYDAY names: 2TU is the second Tuesday, -1FR
    the last Friday, TU every Tuesday. At the time of day of ``at``."""
    first = month.replace(day=1, hour=at.hour, minute=at.minute, second=at.second)
    days_in_month = (_add_months(first, 1) or first + timedelta(days=31)) - first
    by_weekday: dict[int, list[datetime]] = {}
    for offset in range(days_in_month.days):
        day = first + timedelta(days=offset)
        by_weekday.setdefault(day.weekday(), []).append(day)
    found: list[datetime] = []
    for ordinal, weekday in by_day:
        days = by_weekday.get(weekday, [])
        if not ordinal:
            found += days
        elif 0 < ordinal <= len(days) or 0 < -ordinal <= len(days):
            found.append(days[ordinal - 1 if ordinal > 0 else ordinal])
    return sorted(found)


def _expand(start: datetime, rule: dict[str, str], after: datetime | None = None) -> Any:
    """The start times of a repeating event, in order. ``after``: only times after it are
    wanted, so earlier repeats are skipped instead of walked through (unless the rule has
    a COUNT, which counts from the first)."""
    freq = rule.get("FREQ", "DAILY")
    interval = max(int(rule.get("INTERVAL", "1") or 1), 1)
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    until: datetime | None = None
    if rule.get("UNTIL"):
        with contextlib.suppress(ValueError):
            until = parse_time(rule["UNTIL"], {})[0]
            if len(rule["UNTIL"]) == 8:
                until += timedelta(days=1) - timedelta(seconds=1)
    by_ordinal = [
        (int(m.group(1) or 0), _DAYS.index(m.group(2)))
        for d in rule.get("BYDAY", "").split(",")
        if (m := _BYDAY.fullmatch(d.strip()))
    ]
    by_day = [weekday for _, weekday in by_ordinal]
    produced = 0
    step = first_step = 0 if count is not None else _skip(start, after, freq, interval)
    while produced < (count or MAX_OCCURRENCES) and step < first_step + MAX_OCCURRENCES * 7:
        if freq == "WEEKLY":
            week_start = start + timedelta(weeks=step * interval)
            monday = week_start - timedelta(days=week_start.weekday())
            days = sorted(by_day) if by_day else [start.weekday()]
            candidates = [monday + timedelta(days=d) for d in days]
        elif freq == "MONTHLY" and by_ordinal:
            month = _add_months(start.replace(day=1), step * interval)
            candidates = _month_days(month, by_ordinal, start) if month else []
        elif freq == "MONTHLY":
            moved = _add_months(start, step * interval)
            candidates = [moved] if moved else []
        elif freq == "YEARLY":
            moved = _add_months(start, 12 * step * interval)
            candidates = [moved] if moved else []
        else:  # DAILY
            candidates = [start + timedelta(days=step * interval)]
            if by_day:
                candidates = [c for c in candidates if c.weekday() in by_day]
        step += 1
        for candidate in candidates:
            if candidate < start:
                continue
            if until is not None and candidate > until:
                return
            yield candidate
            produced += 1
            if count is not None and produced >= count:
                return


# -- writing -----------------------------------------------------------------------------


_LINE_BREAKS = re.compile("\r\n|[\n\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]")


def _escape(value: str) -> str:
    # Every kind of line break becomes \n: a raw one would end the property's line.
    escaped = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return _LINE_BREAKS.sub(lambda _: "\\n", escaped)


def _fold(line: str) -> list[str]:
    out = []
    while len(line.encode("utf-8")) > 75:
        cut = 74
        while len(line[:cut].encode("utf-8")) > 74:
            cut -= 1
        out.append(line[:cut])
        line = " " + line[cut:]
    out.append(line)
    return out


def _stamp(moment: datetime, all_day: bool) -> str:
    return moment.strftime("%Y%m%d") if all_day else moment.strftime("%Y%m%dT%H%M%S")


def write(events: list[Event]) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Starlight//calendar//EN"]
    now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for event in events:
        kind = ";VALUE=DATE" if event.all_day else ""
        lines += [
            "BEGIN:VEVENT",
            f"UID:{_escape(event.uid)}",
            f"DTSTAMP:{now}",
            f"DTSTART{kind}:{_stamp(event.start, event.all_day)}",
            f"DTEND{kind}:{_stamp(event.end, event.all_day)}",
            f"SUMMARY:{_escape(event.title)}",
        ]
        if event.location:
            lines.append(f"LOCATION:{_escape(event.location)}")
        if event.notes:
            lines.append(f"DESCRIPTION:{_escape(event.notes)}")
        if event.rule:
            lines.append("RRULE:" + ";".join(f"{k}={v}" for k, v in event.rule.items()))
        for skipped in sorted(event.exdates):
            lines.append(f"EXDATE{kind}:{_stamp(skipped, event.all_day)}")
        if event.remind_minutes is not None:
            lines.append(f"X-STARLIGHT-REMIND:{event.remind_minutes}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(part for line in lines for part in _fold(line)) + "\r\n"
