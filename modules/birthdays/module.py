"""Birthdays: name and day (with the year, if you know it, for "turns 40"). Once a day,
after the morning hour you set, Starlight tells you about today's birthdays and the ones a
few days ahead, on every channel - each one once.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.jsonfile import load_json, save_json


def parse_birthday(text: str) -> tuple[int, int, int | None]:
    """(month, day, year or None) from "24.12.1985", "24.12.", "1985-12-24", "12-24"."""
    raw = text.strip()
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.?(\d{4})?", raw)
    if match:
        day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
    elif match := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw):
        year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
    elif match := re.fullmatch(r"(\d{1,2})-(\d{1,2})", raw):
        month, day, year = int(match.group(1)), int(match.group(2)), None
    else:
        raise ToolError("the birthday as 24.12.1985, 24.12. or 1985-12-24")
    try:
        dt.date(2000, month, day)  # (a leap year: 29 February is a birthday too)
    except ValueError:
        raise ToolError(f"{raw} is not a day of the year") from None
    return month, day, int(year) if year else None


def next_birthday(month: int, day: int, today: dt.date) -> dt.date:
    """The next time it comes round (today counts); 29 February on 28 February otherwise."""
    for year in (today.year, today.year + 1):
        try:
            when = dt.date(year, month, day)
        except ValueError:  # 29 February in a year that has none
            when = dt.date(year, 2, 28)
        if when >= today:
            return when
    raise AssertionError("unreachable")


class Config(BaseModel):
    remind_days_before: int = Field(default=3, ge=0, le=30)
    remind_from_hour: int = Field(default=8, ge=0, le=23)


class BirthdaysModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.file = ctx.data_dir / "birthdays.json"
        self.told_file = ctx.data_dir / "told.json"
        ctx.every(1800, self.check, name="birthdays", first_delay=60)

    def _today(self) -> dt.date:
        return dt.date.today()

    def _all(self) -> dict[str, dict[str, Any]]:
        return load_json(self.file, {})

    @tool
    def add(self, name: str, birthday: str) -> dict[str, Any]:
        """Remember someone's birthday (24.12.1985, or 24.12. without the year)."""
        name = " ".join(name.split())
        if not name:
            raise ToolError("whose birthday?")
        month, day, year = parse_birthday(birthday)
        people = self._all()
        people[name] = {"month": month, "day": day, "year": year}
        save_json(self.file, people)
        return {"saved": name, "next": self._describe(name, people[name])}

    @tool
    def remove(self, name: str) -> dict[str, Any]:
        """Forget someone's birthday."""
        people = self._all()
        match = next((n for n in people if n.casefold() == name.strip().casefold()), None)
        if match is None:
            raise ToolError(f"no birthday saved for {name}")
        del people[match]
        save_json(self.file, people)
        return {"removed": match}

    def _describe(self, name: str, entry: dict[str, Any]) -> dict[str, Any]:
        today = self._today()
        when = next_birthday(entry["month"], entry["day"], today)
        out: dict[str, Any] = {
            "name": name,
            "date": when.isoformat(),
            "in_days": (when - today).days,
        }
        if entry.get("year"):
            out["turns"] = when.year - int(entry["year"])
        return out

    @tool
    def upcoming(self, days: int = 30) -> dict[str, Any]:
        """The birthdays in the next days (30 if not said), soonest first."""
        found = [self._describe(n, e) for n, e in self._all().items()]
        found = [b for b in found if b["in_days"] <= max(0, days)]
        return {"birthdays": sorted(found, key=lambda b: b["in_days"])}

    @tool
    def all(self) -> dict[str, Any]:
        """Every birthday you saved, in calendar order."""
        people = self._all()
        ordered = sorted(people.items(), key=lambda kv: (kv[1]["month"], kv[1]["day"]))
        return {
            "count": len(ordered),
            "birthdays": [
                {
                    "name": n,
                    "day": f"{e['day']:02d}.{e['month']:02d}."
                    + (str(e["year"]) if e.get("year") else ""),
                }
                for n, e in ordered
            ],
        }

    async def check(self) -> None:
        """Once the morning hour has come: today's birthdays and the ones a few days away,
        each told once."""
        now = dt.datetime.now()
        if now.hour < self.config.remind_from_hour:
            return
        told: dict[str, str] = load_json(self.told_file, {})
        today = self._today().isoformat()
        for name, entry in self._all().items():
            info = self._describe(name, entry)
            wanted = info["in_days"] == 0 or info["in_days"] == self.config.remind_days_before
            key = f"{name}|{info['date']}|{info['in_days']}"
            if not wanted or key in told:
                continue
            turns = f" (turns {info['turns']})" if "turns" in info else ""
            if info["in_days"] == 0:
                text = f"Today is {name}'s birthday{turns}."
            else:
                text = f"{name}'s birthday is in {info['in_days']} days, on {info['date']}{turns}."
            await self.ctx.deliver(text, "Birthday")
            told[key] = today
        cutoff = (self._today() - dt.timedelta(days=60)).isoformat()
        save_json(self.told_file, {k: v for k, v in told.items() if v >= cutoff})
