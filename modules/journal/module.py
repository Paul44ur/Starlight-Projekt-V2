"""A journal: one Markdown file per day in your Starlight folder (Journal/2026-10-30.md),
each entry with the time it was written. A new day starts from the Daily template
(Templates/Daily.md in the Starlight folder: today's calendar and what is due).

`week` reads the last seven days back and writes a weekly review with the model - what
happened, what got done, what is open, what to focus on - saved in Journal/Weekly; `month`
and `year` do the same for a month (Journal/Monthly) and a year (Journal/Yearly). Schedule
them with an automation ("every Sunday at 18:00, write my weekly review") and they come to
you on every channel. Every change keeps the day's file as it was (starlight.notebook), so
it can be undone with files.restore.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.model import Message
from starlight.notebook import append_note, templates, write_note
from starlight.notebook.markdown import iso_dates

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

REVIEW = (
    "You write the user's weekly review from their journal (and, if given, their to-dos and "
    "calendar). In the language the journal is written in. Short sections: 'The week' (two "
    "or three sentences), 'Good things', 'Done', 'Still open', 'Next week' (two or three "
    "concrete things to focus on). Warm and honest, never invent anything."
)


REVIEW_MONTH = (
    "You write the user's monthly review from their journal (and their weekly reviews, if "
    "given). In the language the journal is written in. Short sections: 'The month' (three "
    "or four sentences), 'Good things', 'Done', 'Still open', 'Next month' (two or three "
    "things). Warm and honest, never invent anything."
)
REVIEW_YEAR = (
    "You write the user's yearly review from their monthly reviews and journal. In the "
    "language they are written in. Short sections: 'The year' (a short paragraph), "
    "'Highlights', 'What changed', 'Still open', 'Next year' (three things). Warm and honest, "
    "never invent anything."
)
MAX_REVIEW_INPUT = 60_000  # characters of journal a monthly or yearly review reads


def parse_month(text: str, today: dt.date) -> dt.date:
    """The first day of a month: "" or "this" (this month), "last" (last month), or
    "2026-10" / "10.2026"."""
    raw = text.strip().lower()
    first = today.replace(day=1)
    if raw in ("", "this", "dieser", "diesen"):
        return first
    if raw in ("last", "previous", "letzter", "letzten", "vorigen"):
        return (first - dt.timedelta(days=1)).replace(day=1)
    match = re.fullmatch(r"(\d{4})-(\d{1,2})", raw) or re.fullmatch(r"(\d{1,2})\.(\d{4})", raw)
    if match:
        year, month = (
            (match.group(1), match.group(2)) if "-" in raw else (match.group(2), match.group(1))
        )
        if 1 <= int(month) <= 12:
            return dt.date(int(year), int(month), 1)
    raise ToolError("which month? Say this month, last month, or 2026-10")


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

    async def _module_output(self, tool: str, args: dict[str, Any]) -> Any:
        """Another module's answer for a template ({{events}} ...); None when it is off."""
        if not self.ctx.has_tool(tool):
            return None
        result = await self.ctx.call(tool, args)
        return result.output if result.ok else None

    async def _new_day(self, when: dt.date) -> str:
        """How a new day's page starts: the Daily template filled in (today's calendar and
        what is due only for today itself), else a heading with the date."""
        template = templates.find(self.ctx.output_dir, "Daily")
        if template is None:
            return f"# {when.strftime('%A, %d %B %Y')}\n"
        moment = dt.datetime.combine(when, dt.datetime.now().time())
        text = await templates.render(
            template.read_text("utf-8", errors="replace"),
            title=when.isoformat(),
            lang=templates.system_language(),
            now=moment,
            call=self._module_output if when == self._today() else None,
        )
        return text if text.endswith("\n") else text + "\n"

    @tool
    async def write(self, text: str, day: str = "") -> dict[str, Any]:
        """Add an entry to the journal (today's, or another day's): how the day went,
        a thought, something to remember. Kept as written, with the time."""
        text = iso_dates(text.strip(), self._today())  # "due: tomorrow" -> an ISO date
        if not text:
            raise ToolError("what should go in the journal?")
        when = parse_day(day, self._today())
        path = self._day_file(when)
        path.parent.mkdir(parents=True, exist_ok=True)
        start = "" if path.is_file() else await self._new_day(when)
        stamp = dt.datetime.now().strftime("%H:%M") if when == self._today() else "later"
        written = append_note(
            path, f"{start}\n**{stamp}** - {text}\n", "journal.write", self.ctx.history
        )
        return {"saved": str(path), "day": when.isoformat(), "changed": written.changed(path)}

    @tool
    async def today(self, day: str = "") -> dict[str, Any]:
        """Today's journal page (or another day's), made from the Daily template if it is
        not there yet: today's calendar, what is due, room for notes."""
        when = parse_day(day, self._today())
        path = self._day_file(when)
        if path.is_file():
            return {"day": when.isoformat(), "file": str(path), "new": False}
        path.parent.mkdir(parents=True, exist_ok=True)
        written = write_note(path, await self._new_day(when), "journal.today", self.ctx.history)
        return {"day": when.isoformat(), "file": str(path), "new": True,
                "changed": written.changed(path)}  # fmt: skip

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

    async def _review(self, prompt: str, context: str, path: Path, heading: str) -> dict[str, Any]:
        model = self.ctx.model_for("summary")
        if model is None:
            raise ToolError("no model is configured")
        reply = await model.chat([Message("system", prompt), Message("user", context)], [])
        review = str(reply.content).strip()
        written = write_note(path, f"# {heading}\n\n{review}\n", "journal.review", self.ctx.history)
        return {"review": review, "saved": str(path), "changed": written.changed(path)}

    def _days_of(self, prefix: str) -> list[Path]:
        """The day files whose name starts with ``prefix`` ("2026-10" or "2026"), in order."""
        if not self.folder.is_dir():
            return []
        return sorted(p for p in self.folder.glob(f"{prefix}-*.md") if p.is_file())

    @tool
    async def month(self, month: str = "") -> dict[str, Any]:
        """The monthly review of a month ("this", "last" or "2026-10"; this month if not
        said): its journal days and weekly reviews written up by the model, saved in
        Journal/Monthly."""
        first = parse_month(month, self._today())
        label = f"{first.year:04d}-{first.month:02d}"
        parts = [
            f"## {p.stem}\n{p.read_text('utf-8', errors='replace')}" for p in self._days_of(label)
        ]
        days = (first + dt.timedelta(days=n) for n in range(31))
        weeks = sorted({d.isocalendar()[:2] for d in days if d.month == first.month})
        for year_number, week in weeks:  # the weekly reviews of the weeks in this month
            path = self.folder / "Weekly" / f"{year_number}-W{week:02d}.md"
            if path.is_file():
                parts.append(
                    f"## Weekly review {path.stem}\n{path.read_text('utf-8', errors='replace')}"
                )
        if not parts:
            raise ToolError(f"the journal has nothing for {label} yet")
        context = f"Journal for {label}:\n\n" + "\n\n".join(parts)
        heading = templates.format_date(first, "MMMM YYYY", templates.system_language())
        return await self._review(
            REVIEW_MONTH,
            context[-MAX_REVIEW_INPUT:],
            self.folder / "Monthly" / f"{label}.md",
            heading,
        )

    @tool
    async def year(self, year: str = "") -> dict[str, Any]:
        """The yearly review ("2026", "last"; this year if not said): from the monthly
        reviews, and the journal for months without one, written up by the model and saved
        in Journal/Yearly."""
        raw = year.strip().lower()
        today = self._today()
        if raw in ("", "this", "dieses"):
            number = today.year
        elif raw in ("last", "previous", "letztes", "voriges"):
            number = today.year - 1
        elif re.fullmatch(r"\d{4}", raw):
            number = int(raw)
        else:
            raise ToolError("which year? Say this year, last year, or 2026")
        parts = []
        for month_number in range(1, 13):
            label = f"{number:04d}-{month_number:02d}"
            review = self.folder / "Monthly" / f"{label}.md"
            if review.is_file():
                parts.append(
                    f"## Monthly review {label}\n{review.read_text('utf-8', errors='replace')}"
                )
                continue
            days = "\n".join(p.read_text("utf-8", errors="replace") for p in self._days_of(label))
            if days.strip():
                parts.append(f"## Journal {label}\n{days[:4000]}")
        if not parts:
            raise ToolError(f"the journal has nothing for {number} yet")
        context = f"The year {number}:\n\n" + "\n\n".join(parts)
        return await self._review(
            REVIEW_YEAR,
            context[:MAX_REVIEW_INPUT],
            self.folder / "Yearly" / f"{number}.md",
            str(number),
        )
