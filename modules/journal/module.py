"""A journal: one Markdown file per day in your Starlight folder (Journal/2026-10-30.md),
each entry with the time it was written. `week` reads the last seven days back and writes a
weekly review with the model - what happened, what got done, what is open, what to focus on
- saved in Journal/Weekly. Schedule it with an automation ("every Sunday at 18:00, write my
weekly review") and it comes to you on every channel. Every change keeps the day's file as it
was (starlight.notebook), so it can be undone with files.restore.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.model import Message
from starlight.notebook import append_note, write_note

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

REVIEW = (
    "You write the user's weekly review from their journal (and, if given, their to-dos and "
    "calendar). In the language the journal is written in. Short sections: 'The week' (two "
    "or three sentences), 'Good things', 'Done', 'Still open', 'Next week' (two or three "
    "concrete things to focus on). Warm and honest, never invent anything."
)


def parse_day(text: str, today: dt.date) -> dt.date:
    """'today', 'yesterday', 'heute', 'gestern', a weekday's name (the last one), or a date
    (2026-10-30, 30.10.2026, 30.10.)."""
    raw = text.strip().lower()
    if raw in ("", "today", "heute"):
        return today
    if raw in ("yesterday", "gestern"):
        return today - dt.timedelta(days=1)
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    german = ["montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag"]
    for days in (names, german):
        if raw in days:
            back = (today.weekday() - days.index(raw)) % 7
            return today - dt.timedelta(days=back)
    try:
        return dt.date.fromisoformat(raw)
    except ValueError:
        pass
    match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})?", raw)
    if match:
        year = int(match.group(3) or today.year)
        try:
            return dt.date(year, int(match.group(2)), int(match.group(1)))
        except ValueError:
            pass
    raise ToolError("which day? Say today, yesterday, a weekday or a date like 2026-10-30")


class Config(BaseModel):
    folder: str = "Journal"
    review_uses_todos: bool = True
    review_uses_calendar: bool = True


class JournalModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    @property
    def folder(self) -> Path:
        return self.ctx.output_dir / (_BAD.sub("", self.config.folder).strip() or "Journal")

    def _today(self) -> dt.date:
        return dt.date.today()

    def _day_file(self, day: dt.date) -> Path:
        return self.folder / f"{day.isoformat()}.md"

    def _read_day(self, day: dt.date) -> str:
        path = self._day_file(day)
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    @tool
    def write(self, text: str, day: str = "") -> dict[str, Any]:
        """Add an entry to the journal (today's, or another day's): how the day went,
        a thought, something to remember. Kept as written, with the time."""
        text = text.strip()
        if not text:
            raise ToolError("what should go in the journal?")
        when = parse_day(day, self._today())
        path = self._day_file(when)
        path.parent.mkdir(parents=True, exist_ok=True)
        start = "" if path.is_file() else f"# {when.strftime('%A, %d %B %Y')}\n"
        stamp = dt.datetime.now().strftime("%H:%M") if when == self._today() else "later"
        written = append_note(
            path, f"{start}\n**{stamp}** - {text}\n", "journal.write", self.ctx.history
        )
        return {"saved": str(path), "day": when.isoformat(), "changed": written.changed(path)}

    @tool
    def read(self, day: str = "") -> dict[str, Any]:
        """The journal for a day (today if not said)."""
        when = parse_day(day, self._today())
        return {"day": when.isoformat(), "journal": self._read_day(when) or "(nothing written)"}

    @tool
    def search(self, text: str, limit: int = 20) -> dict[str, Any]:
        """Find journal entries that mention something, newest first."""
        wanted = text.strip().casefold()
        if not wanted:
            raise ToolError("what should I look for?")
        found: list[dict[str, str]] = []
        files = sorted(self.folder.glob("*.md"), reverse=True) if self.folder.is_dir() else []
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                if wanted in line.casefold() and not line.startswith("#"):
                    found.append({"day": path.stem, "entry": line.strip()[:300]})
            if len(found) >= limit:
                break
        return {"count": len(found[:limit]), "entries": found[:limit]}

    async def _extra(self, tool: str, args: dict[str, Any]) -> str:
        """Another module's view (to-dos, calendar) for the review; "" when it is off."""
        if not self.ctx.has_tool(tool):
            return ""
        result = await self.ctx.call(tool, args)
        return result.to_text()[:4000] if result.ok else ""

    @tool
    async def week(self, until: str = "") -> dict[str, Any]:
        """The weekly review: the last seven days of the journal (to the day given, today if
        not said), with to-dos and the coming week's calendar, written up by the model and
        saved in Journal/Weekly."""
        end = parse_day(until, self._today())
        days = [end - dt.timedelta(days=n) for n in range(6, -1, -1)]
        journal = "\n\n".join(text for day in days if (text := self._read_day(day)))
        context = [f"Journal from {days[0]} to {end}:\n\n{journal or '(nothing written)'}"]
        if self.config.review_uses_todos:
            todos = await self._extra("todo.list_tasks", {"include_done": True})
            if todos:
                context.append(f"To-dos (done and open):\n{todos}")
        if self.config.review_uses_calendar:
            agenda = await self._extra("calendar.agenda", {"days": 7, "start": "tomorrow"})
            if agenda:
                context.append(f"Calendar for the coming week:\n{agenda}")
        if not journal and len(context) == 1:
            raise ToolError("the journal has nothing for this week yet: write something first")
        model = self.ctx.model_for("summary")
        if model is None:
            raise ToolError("no model is configured")
        reply = await model.chat(
            [Message("system", REVIEW), Message("user", "\n\n".join(context))], []
        )
        review = str(reply.content).strip()
        year, number, _ = end.isocalendar()
        path = self.folder / "Weekly" / f"{year}-W{number:02d}.md"
        content = f"# Week {number}, {year}\n\n{review}\n"
        written = write_note(path, content, "journal.week", self.ctx.history)
        return {"review": review, "saved": str(path), "changed": written.changed(path)}
