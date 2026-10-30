"""Train connections and departure boards in Germany, with delays, platforms and
cancellations, as the DB app shows them.

Uses db.transport.rest (a free, open front to Deutsche Bahn's timetable; no account or API
key). Requests go through Starlight's shared web client, so the rate limits and the
size/time caps apply. Station names are looked up once and kept for a while.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.http import HttpError
from starlight.core.schedule import parse_when

# "morgen um 8 Uhr" -> "tomorrow at 8:00": parse_when speaks English
_GERMAN = (
    (r"\bübermorgen\b", "in 2 days"),
    (r"\bmorgen\b", "tomorrow"),
    (r"\bheute\b", "today"),
    (r"\bum\b", "at"),
    (r"\bin (\d+) (?:minuten|min)\b", r"in \1 minutes"),
    (r"\bin (\d+) stunden?\b", r"in \1 hours"),
    (r"\b(\d{1,2})[.:](\d{2}) uhr\b", r"\1:\2"),
    (r"\b(\d{1,2}) uhr (\d{2})\b", r"\1:\2"),
    (r"\b(\d{1,2}) uhr\b", r"\1:00"),
)


def parse_departure(text: str, now: datetime) -> datetime:
    """When to travel: "" is now; "8:15", "tomorrow 7am", "morgen 8 Uhr", an ISO time."""
    raw = text.strip().lower()
    if raw in ("", "now", "jetzt", "sofort"):
        return now
    for pattern, replacement in _GERMAN:
        raw = re.sub(pattern, replacement, raw)
    return parse_when(re.sub(r"\s+", " ", raw).strip(), now)


def _moment(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def clock(value: Any, today: datetime | None = None) -> str:
    """ "08:12", or "Sat 31.10. 08:12" when it is not on ``today``'s date."""
    moment = _moment(value)
    if moment is None:
        return "?"
    if today is not None and moment.date() != today.date():
        return moment.strftime("%a %d.%m. %H:%M")
    return moment.strftime("%H:%M")


def delay(seconds: Any) -> str:
    """ "+5 min" late, "-1 min" early, "" on time or when the service does not know."""
    try:
        minutes = round(float(seconds) / 60)
    except (TypeError, ValueError):
        return ""
    return f"{minutes:+d} min" if minutes else ""


def duration(start: Any, end: Any) -> str:
    begin, finish = _moment(start), _moment(end)
    if begin is None or finish is None:
        return "?"
    minutes = max(0, round((finish - begin).total_seconds() / 60))
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _line(leg: dict[str, Any]) -> str:
    line = leg.get("line") or {}
    return str(line.get("name") or line.get("productName") or "train").strip()


def _at(time: Any, late: Any, platform: Any, today: datetime) -> str:
    text = clock(time, today)
    if delay(late):
        text += f" ({delay(late)})"
    if platform:
        text += f", platform {platform}"
    return text


def describe_leg(leg: dict[str, Any], today: datetime) -> str:
    origin = (leg.get("origin") or {}).get("name") or "?"
    destination = (leg.get("destination") or {}).get("name") or "?"
    if leg.get("walking"):
        distance = leg.get("distance")
        far = f" {distance} m" if distance else ""
        return f"walk{far} to {destination}"
    departs = _at(
        leg.get("departure") or leg.get("plannedDeparture"),
        leg.get("departureDelay"),
        leg.get("departurePlatform"),
        today,
    )
    arrives = _at(
        leg.get("arrival") or leg.get("plannedArrival"),
        leg.get("arrivalDelay"),
        leg.get("arrivalPlatform"),
        today,
    )
    text = f"{_line(leg)}: {origin} {departs} -> {destination} {arrives}"
    return text + (" - CANCELLED" if leg.get("cancelled") else "")


def _warnings(legs: list[dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for leg in legs:
        for remark in leg.get("remarks") or []:
            if not isinstance(remark, dict) or remark.get("type") not in ("warning", "status"):
                continue
            text = str(remark.get("summary") or remark.get("text") or "").strip()
            if text and text not in found:
                found.append(text[:200])
    return found[:3]


def describe_journey(journey: dict[str, Any], today: datetime) -> dict[str, Any]:
    legs = [leg for leg in journey.get("legs") or [] if isinstance(leg, dict)]
    if not legs:
        return {}
    first, last = legs[0], legs[-1]
    rides = [leg for leg in legs if not leg.get("walking")]
    out: dict[str, Any] = {
        "departs": _at(
            first.get("departure") or first.get("plannedDeparture"),
            first.get("departureDelay"),
            None,
            today,
        ),
        "arrives": _at(
            last.get("arrival") or last.get("plannedArrival"),
            last.get("arrivalDelay"),
            None,
            today,
        ),
        "duration": duration(
            first.get("departure") or first.get("plannedDeparture"),
            last.get("arrival") or last.get("plannedArrival"),
        ),
        "changes": max(0, len(rides) - 1),
        "trains": " / ".join(_line(leg) for leg in rides),
        "legs": [describe_leg(leg, today) for leg in legs],
    }
    if any(leg.get("cancelled") for leg in legs):
        out["cancelled"] = True
    price = journey.get("price") or {}
    if isinstance(price, dict) and price.get("amount") is not None:
        out["price"] = f"from {price['amount']:.2f} {price.get('currency') or 'EUR'}"
    warnings = _warnings(legs)
    if warnings:
        out["warnings"] = warnings
    return out


def describe_departure(entry: dict[str, Any], today: datetime) -> dict[str, Any]:
    out: dict[str, Any] = {
        "time": clock(entry.get("when") or entry.get("plannedWhen"), today),
        "line": _line(entry),
        "towards": entry.get("direction") or (entry.get("destination") or {}).get("name") or "",
    }
    if delay(entry.get("delay")):
        out["delay"] = delay(entry.get("delay"))
    platform = entry.get("platform") or entry.get("plannedPlatform")
    if platform:
        out["platform"] = str(platform)
        planned = entry.get("plannedPlatform")
        if planned and entry.get("platform") and planned != entry.get("platform"):
            out["platform"] += f" (instead of {planned})"
    if entry.get("cancelled"):
        out["cancelled"] = True
    return out


class Config(BaseModel):
    api: str = "https://v6.db.transport.rest"
    home_station: str = ""  # used when no start is named
    results: int = Field(default=4, ge=1, le=10)
    language: str = "en"


class TrainsModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    def _now(self) -> datetime:
        return datetime.now().astimezone()

    async def _get(self, path: str, use_cache: bool = False) -> Any:
        url = self.config.api.rstrip("/") + path
        try:
            response = await self.ctx.http.get(url, respect_robots=False, use_cache=use_cache)
        except HttpError as exc:
            raise ToolError(f"could not reach the Deutsche Bahn timetable: {exc}") from exc
        if response.status in (429, 500, 502, 503, 504):
            raise ToolError(
                f"the Deutsche Bahn timetable is busy or down (HTTP {response.status}); "
                "try again in a minute"
            )
        if not response.ok:
            raise ToolError(f"the Deutsche Bahn timetable answered HTTP {response.status}")
        try:
            return response.json()
        except ValueError as exc:
            raise ToolError("the Deutsche Bahn timetable sent an answer I could not read") from exc

    async def _stations(self, query: str, results: int = 1) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            raise ToolError("which station? (set home_station in config/modules/trains.yaml)")
        params = {
            "query": query, "results": results, "stops": "true",
            "addresses": "false", "poi": "false", "language": self.config.language,
        }  # fmt: skip
        found = await self._get(f"/locations?{urlencode(params)}", use_cache=True)
        stations = [
            s for s in found or []
            if isinstance(s, dict) and s.get("id") and s.get("type") in ("station", "stop")
        ]  # fmt: skip
        if not stations:
            raise ToolError(f"could not find a station called '{query}'")
        return stations

    async def _station(self, query: str) -> dict[str, Any]:
        return (await self._stations(query or self.config.home_station))[0]

    @tool
    async def connections(
        self,
        destination: str,
        start: str = "",
        when: str = "",
        arrive_by: bool = False,
        results: int = 0,
    ) -> dict[str, Any]:
        """Train connections from one station to another (start: your home station if
        left out), with times, delays, platforms, changes and cancellations. when: "" for
        now, "18:30", "tomorrow 7am" ...; arrive_by: true to arrive by that time."""
        origin = await self._station(start)
        target = await self._station(destination)
        now = self._now()
        moment = parse_departure(when, now)
        if moment.tzinfo is None:
            moment = moment.astimezone()
        params = {
            "from": origin["id"],
            "to": target["id"],
            "arrival" if arrive_by else "departure": moment.isoformat(timespec="minutes"),
            "results": results or self.config.results,
            "stopovers": "false",
            "remarks": "true",
            "language": self.config.language,
        }
        found = await self._get(f"/journeys?{urlencode(params)}")
        journeys = found.get("journeys") if isinstance(found, dict) else None
        described = [describe_journey(j, now) for j in journeys or [] if isinstance(j, dict)]
        described = [j for j in described if j]
        if not described:
            raise ToolError(f"no connections found from {origin['name']} to {target['name']}")
        return {
            "from": origin.get("name"),
            "to": target.get("name"),
            ("arrive by" if arrive_by else "leaving"): clock(moment.isoformat(), now),
            "connections": described,
        }

    @tool
    async def departures(
        self, station: str = "", when: str = "", results: int = 8
    ) -> dict[str, Any]:
        """The departure board of a station (your home station if left out): the next
        trains, buses and trams with their delays, platforms and cancellations."""
        stop = await self._station(station)
        now = self._now()
        moment = parse_departure(when, now)
        if moment.tzinfo is None:
            moment = moment.astimezone()
        params = {
            "when": moment.isoformat(timespec="minutes"),
            "duration": 120,
            "results": max(1, min(results, 30)),
            "remarks": "false",
            "language": self.config.language,
        }
        stop_id = quote(str(stop["id"]), safe="")
        found = await self._get(f"/stops/{stop_id}/departures?{urlencode(params)}")
        entries = found.get("departures") if isinstance(found, dict) else found  # v6, v5
        board = [describe_departure(e, now) for e in entries or [] if isinstance(e, dict)]
        return {"station": stop.get("name"), "departures": board[: max(1, results)]}

    @tool
    async def find_station(self, name: str) -> list[dict[str, str]]:
        """Stations matching a name (when it is unclear which one is meant)."""
        stations = await self._stations(name, results=6)
        return [{"name": str(s.get("name")), "id": str(s.get("id"))} for s in stations]
