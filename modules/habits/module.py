"""Habits: things you want to do every day (or every week). Tick one off, and see how long
your streak is and the last two weeks at a glance.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.jsonfile import load_json, save_json


def streak(done: set[str], every: str, today: dt.date) -> int:
    """How many days (or weeks) in a row up to now. Today (this week) not done yet does
    not break it."""
    if every == "week":
        weeks = {dt.date.fromisoformat(d).isocalendar()[:2] for d in done}
        count, week = 0, today
        if today.isocalendar()[:2] not in weeks:
            week -= dt.timedelta(days=7)
        while week.isocalendar()[:2] in weeks:
            count += 1
            week -= dt.timedelta(days=7)
        return count
    count, day = 0, today
    if day.isoformat() not in done:
        day -= dt.timedelta(days=1)
    while day.isoformat() in done:
        count += 1
        day -= dt.timedelta(days=1)
    return count


class HabitsModule(Module):
    async def setup(self, ctx: ModuleContext) -> None:
        self.file = ctx.data_dir / "habits.json"

    def _today(self) -> dt.date:
        return dt.date.today()

    def _all(self) -> dict[str, dict[str, Any]]:
        return load_json(self.file, {})

    def _find(self, habits: dict[str, Any], name: str) -> str:
        wanted = name.strip().casefold()
        for key in habits:
            if key.casefold() == wanted:
                return key
        matches = [key for key in habits if wanted in key.casefold()]
        if len(matches) == 1:
            return matches[0]
        raise ToolError(
            f"no habit called {name}" + (f"; did you mean {', '.join(matches)}?" if matches else "")
        )

    @tool
    def add(self, habit: str, every: Literal["day", "week"] = "day") -> dict[str, Any]:
        """Start tracking a habit, daily (the usual) or weekly."""
        name = " ".join(habit.split())
        if not name:
            raise ToolError("which habit?")
        habits = self._all()
        habits.setdefault(name, {"every": every, "done": []})
        save_json(self.file, habits)
        return {"tracking": name, "every": every}

    @tool
    def done(self, habit: str, day: str = "") -> dict[str, Any]:
        """Tick off a habit for today (or a day like 2026-10-29 or "yesterday")."""
        habits = self._all()
        name = self._find(habits, habit)
        today = self._today()
        if day.strip().lower() in ("yesterday", "gestern"):
            when = today - dt.timedelta(days=1)
        else:
            try:
                when = dt.date.fromisoformat(day.strip()) if day.strip() else today
            except ValueError:
                raise ToolError("the day as YYYY-MM-DD, or yesterday") from None
        days = set(habits[name]["done"]) | {when.isoformat()}
        habits[name]["done"] = sorted(days)[-400:]  # (a year and a bit is plenty)
        save_json(self.file, habits)
        return {"done": name, "day": when.isoformat(),
                "streak": streak(days, habits[name]["every"], today)}  # fmt: skip

    @tool
    def status(self) -> dict[str, Any]:
        """Every habit: done today (this week)?, the streak, and the last 14 days."""
        today = self._today()
        out = []
        for name, entry in self._all().items():
            done = set(entry["done"])
            every = entry.get("every", "day")
            if every == "week":
                now = today.isocalendar()[:2] in {
                    dt.date.fromisoformat(d).isocalendar()[:2] for d in done
                }
            else:
                now = today.isoformat() in done
            days = "".join(
                "x" if (today - dt.timedelta(days=n)).isoformat() in done else "."
                for n in range(13, -1, -1)
            )
            out.append({"habit": name, "every": every, "done_now": now,
                        "streak": streak(done, every, today), "last_14_days": days})  # fmt: skip
        return {"habits": out}

    @tool
    def remove(self, habit: str) -> dict[str, Any]:
        """Stop tracking a habit (its record is deleted)."""
        habits = self._all()
        name = self._find(habits, habit)
        del habits[name]
        save_json(self.file, habits)
        return {"removed": name}
