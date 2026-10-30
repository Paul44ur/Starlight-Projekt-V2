"""Trips: where and when, with a countdown, and a packing list made from the length of the
trip and (once it is close enough for a forecast) the weather there, from the weather module.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.jsonfile import load_json, save_json

ALWAYS = ["passport or ID card", "phone and charger", "wallet and cards", "medication",
          "toothbrush and toiletries", "glasses or contact lenses", "keys"]  # fmt: skip
CLOTHES_PER_DAY = ["underwear", "socks", "T-shirts or tops"]


def parse_date(text: str) -> dt.date:
    raw = text.strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    raise ToolError(f"'{text}' is not a date; use 2026-12-20 or 20.12.2026")


def packing(days: int, forecast: list[dict[str, Any]]) -> list[str]:
    """What to pack for ``days`` days, with the forecast's days (high, low, rain chance)."""
    items = list(ALWAYS)
    pieces = min(max(days, 1), 7)  # more than a week: wash on the way
    items += [f"{pieces} x {thing}" for thing in CLOTHES_PER_DAY]

    def number(value: Any) -> float | None:  # "4°C", "-2 °C", "80%"
        match = re.match(r"\s*(-?\d+(?:\.\d+)?)", str(value))
        return float(match.group(1)) if match else None

    highs = [h for d in forecast if (h := number(d.get("high"))) is not None]
    lows = [lo for d in forecast if (lo := number(d.get("low"))) is not None]
    rain = [r for d in forecast if (r := number(d.get("rain_chance"))) is not None]
    if lows and min(lows) < 5:
        items += ["warm jacket", "hat, scarf and gloves"]
    elif lows and min(lows) < 13:
        items += ["jumper or fleece", "light jacket"]
    if highs and max(highs) > 24:
        items += ["shorts", "sun cream", "sunglasses", "sun hat"]
    if rain and max(rain) >= 50:
        items += ["umbrella or rain jacket", "waterproof shoes"]
    if days > 3:
        items.append("laundry bag")
    return items


class TripsModule(Module):
    async def setup(self, ctx: ModuleContext) -> None:
        self.file = ctx.data_dir / "trips.json"

    def _today(self) -> dt.date:
        return dt.date.today()

    def _all(self) -> list[dict[str, Any]]:
        return load_json(self.file, [])

    @tool
    def add(self, destination: str, start: str, end: str, notes: str = "") -> dict[str, Any]:
        """Plan a trip: where to, first and last day (2026-12-20 or 20.12.2026), notes."""
        first, last = parse_date(start), parse_date(end)
        if last < first:
            raise ToolError("the trip ends before it starts")
        trips = [
            t for t in self._all() if t["destination"].casefold() != destination.strip().casefold()
        ]
        trips.append({"destination": destination.strip(), "start": first.isoformat(),
                      "end": last.isoformat(), "notes": notes.strip()})  # fmt: skip
        save_json(self.file, sorted(trips, key=lambda t: t["start"]))
        return {"saved": destination.strip(), "in_days": (first - self._today()).days}

    @tool
    def list_trips(self) -> dict[str, Any]:
        """Your trips still to come (and one under way), soonest first, with a countdown."""
        today = self._today()
        out = []
        for trip in self._all():
            first, last = dt.date.fromisoformat(trip["start"]), dt.date.fromisoformat(trip["end"])
            if last < today:
                continue
            state = "under way" if first <= today else f"in {(first - today).days} days"
            out.append({**trip, "days": (last - first).days + 1, "when": state})
        return {"trips": out}

    @tool
    def remove(self, destination: str) -> dict[str, Any]:
        """Remove a trip."""
        trips = self._all()
        kept = [t for t in trips if t["destination"].casefold() != destination.strip().casefold()]
        if len(kept) == len(trips):
            raise ToolError(f"no trip to {destination}")
        save_json(self.file, kept)
        return {"removed": destination}

    @tool
    async def packing_list(self, destination: str) -> dict[str, Any]:
        """A packing list for a trip, for its length and (within two weeks of it) the
        weather forecast there."""
        trip = next(
            (
                t
                for t in self._all()
                if t["destination"].casefold() == destination.strip().casefold()
            ),
            None,
        )
        if trip is None:
            raise ToolError(f"no trip to {destination}: add it first")
        first, last = dt.date.fromisoformat(trip["start"]), dt.date.fromisoformat(trip["end"])
        days = (last - first).days + 1
        forecast: list[dict[str, Any]] = []
        note = "no forecast yet: the trip is more than two weeks away"
        if (first - self._today()).days <= 14 and self.ctx.has_tool("weather.forecast"):
            result = await self.ctx.call(
                "weather.forecast", {"place": trip["destination"], "days": 14}
            )
            if result.ok and isinstance(result.output, dict):
                forecast = [
                    d for d in result.output.get("days", [])
                    if trip["start"] <= str(d.get("date", "")) <= trip["end"]
                ]  # fmt: skip
                note = f"with the forecast for {len(forecast)} of its days" if forecast else note
            else:
                note = "the forecast could not be fetched"
        return {"trip": trip["destination"], "days": days, "weather": note,
                "pack": packing(days, forecast)}  # fmt: skip
