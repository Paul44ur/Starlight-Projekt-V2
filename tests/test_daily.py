"""Daily-life modules: to-do lists, exact maths and units, translation, the calendar
(with feeds and alerts) and the briefing that pulls them together. All local; feeds use a
mock web server."""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from starlight.core import Kernel, Paths
from starlight.core.model import Message, ModelResponse
from starlight.core.vault import MemoryBackend


class User:
    def __init__(self, *answers: bool) -> None:
        self.answers = list(answers)
        self.questions: list[str] = []
        self.told: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answers.pop(0) if self.answers else True

    async def ask(self, question: str, secret: bool = False) -> str | None:
        return None

    async def notify(self, text: str) -> None:
        self.told.append(text)


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("todo", "calc", "translate", "calendar", "briefing", "reminders"):
        use_module(name)
    return paths


async def call(k: Kernel, tool: str, **args: Any) -> Any:
    result = await k.dispatcher.call(tool, args)
    assert result.ok, result.error
    return result.output


async def fails(k: Kernel, tool: str, **args: Any) -> str:
    result = await k.dispatcher.call(tool, args)
    assert not result.ok
    return str(result.error)


def instance(k: Kernel, name: str) -> Any:
    record = k.modules.records[name]
    assert record.instance is not None, record.error
    return record.instance


# -- todo --------------------------------------------------------------------------------


async def test_todo_lists_order_and_ticking_off(project: Paths) -> None:
    async with Kernel(project) as k:
        today = date.today()
        await call(k, "todo.add", text="buy milk", list="Shopping")
        await call(k, "todo.add", text="file taxes", due="yesterday", priority="high")
        await call(k, "todo.add", text="call mum", due="tomorrow")
        await call(k, "todo.add", text="dentist", due="tomorrow 15:00")
        listed = await call(k, "todo.list")
        assert [t["text"] for t in listed["tasks"]] == [
            "file taxes",
            "call mum",
            "dentist",
            "buy milk",
        ]
        assert listed["lists"] == ["inbox", "shopping"]
        assert listed["tasks"][1]["due"] == (today + timedelta(days=1)).isoformat()  # whole day
        assert listed["tasks"][2]["due"].endswith("T15:00")
        due = await call(k, "todo.due", days=1)
        assert [t["text"] for t in due["overdue"]] == ["file taxes"]
        assert {t["text"] for t in due["due"]} == {"call mum", "dentist"}

        assert (await call(k, "todo.done", id="taxes"))["text"] == "file taxes"  # by a word
        assert "file taxes" not in [t["text"] for t in (await call(k, "todo.list"))["tasks"]]
        await call(k, "todo.change", id="milk", list="groceries", due="friday")
        shopping = await call(k, "todo.list", list="groceries")
        assert shopping["tasks"][0]["text"] == "buy milk" and "due" in shopping["tasks"][0]
        assert "matches 2" in await fails(k, "todo.done", id="m")  # "milk" and "mum": ambiguous
    async with Kernel(project) as k2:  # saved to disk
        assert (await call(k2, "todo.list"))["count"] == 3


async def test_todo_unknown_task_is_a_clear_error(project: Paths) -> None:
    async with Kernel(project) as k:
        assert "no task" in await fails(k, "todo.done", id="nothing-like-this")


# -- calc --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "text"),
    [
        ("(17.5 * 3) / 4", "13.125"),
        ("2^10", "1024"),
        ("15% of 80", "12"),
        ("19% * 249.90", "47.481"),
        ("sqrt(2)", "1.4142135624"),
        ("sin(30)", "0.5"),
        ("17 mod 5", "2"),
        ("factorial(20)", "2 432 902 008 176 640 000"),
    ],
)
async def test_calculate(project: Paths, expression: str, text: str) -> None:
    async with Kernel(project) as k:
        assert (await call(k, "calc.calculate", expression=expression))["text"] == text


@pytest.mark.parametrize(
    "expression",
    ['__import__("os").system("x")', "open('f')", "9**9**9", "1/0", "x + 1", "().__class__"],
)
async def test_calculate_refuses_anything_but_maths(project: Paths, expression: str) -> None:
    async with Kernel(project) as k:
        await fails(k, "calc.calculate", expression=expression)


async def test_units_and_dates(project: Paths) -> None:
    async with Kernel(project) as k:
        assert (await call(k, "calc.convert", value=72, from_unit="F", to_unit="C"))[
            "result"
        ] == pytest.approx(22.2222, abs=1e-3)
        assert (await call(k, "calc.convert", value=5, from_unit="km", to_unit="mi"))[
            "result"
        ] == pytest.approx(3.10686, abs=1e-4)
        assert (await call(k, "calc.convert", value=1, from_unit="GiB", to_unit="MB"))[
            "result"
        ] == pytest.approx(1073.741824)
        assert "cannot convert" in await fails(
            k, "calc.convert", value=1, from_unit="kg", to_unit="m"
        )
        calc = sys.modules[type(instance(k, "calc")).__module__]
        today = date(2026, 10, 7)
        assert calc.parse_date("24.12.", today) == date(2026, 12, 24)
        assert calc.parse_date("1.1.", today, future=True) == date(2027, 1, 1)
        assert calc.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
        span = await call(k, "calc.days_between", start="2026-10-05", end="2026-10-19")
        assert span["days"] == 14 and span["weekdays"] == 10
        added = await call(k, "calc.date_add", day="2026-01-31", amount="1 month")
        assert added["date"] == "2026-02-28"


# -- translate ---------------------------------------------------------------------------


class Translator:
    """Pretends to translate: upper-cases the text and remembers what it was asked."""

    name = "fake"

    def __init__(self) -> None:
        self.systems: list[str] = []

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> ModelResponse:
        self.systems.append(messages[0].content)
        if "Which language" in messages[0].content:
            return ModelResponse(content="German.")
        return ModelResponse(content=messages[-1].content.upper())


async def test_translate_uses_the_model_and_splits_long_text(project: Paths) -> None:
    (project.module_configs / "translate.yaml").write_text("max_chars: 500\n")
    model = Translator()
    async with Kernel(project, provider=model) as k:
        out = await call(k, "translate.translate", text="hallo welt", to="English")
        assert out["translation"] == "HALLO WELT" and "into English" in model.systems[-1]
        long = "\n\n".join(f"paragraph {i} " + "x" * 200 for i in range(5))
        model.systems.clear()
        out = await call(k, "translate.translate", text=long, to="French")
        assert len(model.systems) >= 3  # sent in parts, nothing cut off
        assert out["translation"].count("PARAGRAPH") == 5
        assert (await call(k, "translate.detect", text="Guten Tag"))["language"] == "German"


# -- calendar ----------------------------------------------------------------------------

FEED = """BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:standup@work\r
DTSTART;TZID=Europe/Berlin:20261005T093000\r
DTEND;TZID=Europe/Berlin:20261005T094500\r
RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR\r
SUMMARY:Stand-up\r
BEGIN:VALARM\r
TRIGGER:-PT5M\r
SUMMARY:ignore me\r
END:VALARM\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:holiday@work\r
DTSTART;VALUE=DATE:20261007\r
DTEND;VALUE=DATE:20261008\r
SUMMARY:Company day\\, no meetings\r
END:VEVENT\r
END:VCALENDAR\r
"""


def feed_server(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/secret/basic.ics":
        return httpx.Response(200, text=FEED)
    return httpx.Response(404)


def calendar_kernel(project: Paths, user: User | None = None) -> Kernel:
    return Kernel(
        project,
        ui=user or User(),
        vault_backend=MemoryBackend(),
        http_transport=httpx.MockTransport(feed_server),
    )


async def test_calendar_events_agenda_free_time_and_removal(project: Paths) -> None:
    async with calendar_kernel(project) as k:
        cal = instance(k, "calendar")
        cal._now = lambda: datetime(2026, 10, 7, 8, 0)  # a Wednesday morning
        await call(
            k,
            "calendar.add_event",
            title="Dentist",
            start="today 10:00",
            duration="1h",
            location="Dr. Weiss",
        )
        await call(
            k, "calendar.add_event", title="Lunch, team", start="today 12:30", end="today 13:30"
        )
        await call(k, "calendar.add_event", title="Mum's birthday", start="2026-10-07")
        await call(k, "calendar.add_event", title="Gym", start="monday 18:00", repeat="weekly")
        today = await call(k, "calendar.agenda")
        assert [e["title"] for e in today["events"]] == ["Mum's birthday", "Dentist", "Lunch, team"]
        assert (
            today["events"][1]["start"] == "2026-10-07 10:00"
            and today["events"][1]["end"] == "11:00"
        )
        free = await call(k, "calendar.free_slots")
        assert free["free"] == [
            {"from": "08:00", "to": "10:00"},
            {"from": "11:00", "to": "12:30"},
            {"from": "13:30", "to": "18:00"},
        ]
        weeks = await call(k, "calendar.agenda", days=14)
        assert sum(e["title"] == "Gym" for e in weeks["events"]) == 2
        await call(k, "calendar.remove", id="gym", only_on="2026-10-12")  # skip one Monday
        weeks = await call(k, "calendar.agenda", days=14)
        assert [e["start"][:10] for e in weeks["events"] if e["title"] == "Gym"] == ["2026-10-19"]
        await call(k, "calendar.remove", id="dentist")
        assert "Dentist" not in [e["title"] for e in (await call(k, "calendar.agenda"))["events"]]
    text = (project.data / "modules" / "calendar" / "calendar.ics").read_text()
    assert "BEGIN:VCALENDAR" in text and "SUMMARY:Lunch\\, team" in text


async def test_calendar_feed_is_read_only_and_its_address_stays_secret(project: Paths) -> None:
    url = "https://calendar.example/secret/basic.ics"
    async with calendar_kernel(project, User(True)) as k:
        cal = instance(k, "calendar")
        cal._now = lambda: datetime(2026, 10, 7, 8, 0)
        out = await call(k, "calendar.subscribe", name="Work", url=url)
        assert out == {"subscribed": "work", "events": 2}
        assert k.vault.get("calendar.feed.work") == url
        week = await call(k, "calendar.agenda", days=7, start="2026-10-05")
        titles = [(e["title"], e.get("start", e.get("date"))) for e in week["events"]]
        assert ("Company day, no meetings", "2026-10-07") in titles
        berlin = ZoneInfo("Europe/Berlin")  # the feed's time zone, shown in local time
        expected = [
            datetime(2026, 10, day, 9, 30, tzinfo=berlin).astimezone().strftime("%Y-%m-%d %H:%M")
            for day in (5, 7, 9)
        ]
        assert [t[1] for t in titles if t[0] == "Stand-up"] == expected
        assert "read-only" in await fails(k, "calendar.remove", id="stand-up")
    config = (project.module_configs / "calendar.yaml").read_text()
    assert "work" in config and "secret" not in config  # the address is only in the keyring


async def test_calendar_alerts_once_before_an_event(project: Paths) -> None:
    delivered: list[str] = []
    async with calendar_kernel(project) as k:
        k.bus.subscribe("deliver", lambda e: delivered.append(e.data["text"]))
        cal = instance(k, "calendar")
        cal._now = lambda: datetime(2026, 10, 7, 9, 0)
        await call(k, "calendar.add_event", title="Call with Anna", start="today 9:20")
        assert await cal.check_alerts(datetime(2026, 10, 7, 9, 0)) == []  # 20 min away
        assert await cal.check_alerts(datetime(2026, 10, 7, 9, 6)) == ["Call with Anna"]
        assert await cal.check_alerts(datetime(2026, 10, 7, 9, 8)) == []  # only once
    assert delivered == ["Calendar: Call with Anna starts at 09:20 (in 14 min)"]


# -- briefing ----------------------------------------------------------------------------


async def test_briefing_brings_the_day_together_and_skips_what_is_off(project: Paths) -> None:
    async with calendar_kernel(project) as k:
        now = datetime.now()
        await call(
            k, "calendar.add_event", title="Standup", start=f"{now:%Y-%m-%d} 23:58", duration="1m"
        )
        await call(k, "todo.add", text="pay rent", due="today")
        await call(k, "reminders.remind", when=f"{now:%Y-%m-%d} 23:59", text="sleep")
        out = await call(k, "briefing.briefing")
        text = out["text"]
        assert "Standup" in text and "pay rent" in text and "sleep" in text
        assert out["sections"] == ["calendar", "todo", "reminders"]  # weather etc. not loaded
        delivered: list[str] = []
        k.bus.subscribe("deliver", lambda e: delivered.append(e.data["text"]))
        await call(k, "briefing.send", sections="todo")
        assert delivered and "pay rent" in delivered[0] and "Standup" not in delivered[0]
        assert "unknown section" in await fails(k, "briefing.briefing", sections="horoscope")
