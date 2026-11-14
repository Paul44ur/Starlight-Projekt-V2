"""Block R9: the routes of the Today page (starlight/web/today_api.py)."""

from __future__ import annotations

import json
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from starlight.web import today_api
from tests.conftest import REPO
from tests.test_remote import LOCAL, PHONE, client, home, pair, phone

TODAY = date.today()
DAY = TODAY.isoformat()
YESTERDAY = (TODAY - timedelta(days=1)).isoformat()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()

# A stand-in weather module: no network. It notes every call in a file, so a test can count the
# calls, and the TODAY_TEST_WEATHER variable makes it fail.
WEATHER_YAML = """name: weather
version: 0.1.0
description: a stand-in for the weather module
keywords: ["weather"]
entrypoint: module:WeatherModule
permissions: [safe]
dependencies: []
enabled_by_default: true
"""
WEATHER_PY = '''
import os
from typing import Any

from pydantic import BaseModel

from starlight.core import Module, ModuleContext, ToolError, tool


class Config(BaseModel):
    default_place: str = ""


class WeatherModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config
        self.calls = ctx.data_dir / "calls.txt"

    def note(self, what: str, place: str) -> None:
        with self.calls.open("a", encoding="utf-8") as out:
            out.write(f"{what} {place}\\n")
        if os.environ.get("TODAY_TEST_WEATHER") == "fail":
            raise ToolError("the weather service did not answer")

    @tool
    async def now(self, place: str = "") -> dict[str, Any]:
        """The weather now."""
        self.note("now", place)
        return {"place": place or "Hamburg", "summary": "partly cloudy, 12 C (feels like 10 C)",
                "temperature": "12°C", "feels_like": "10°C", "conditions": "partly cloudy"}

    @tool
    async def forecast(self, place: str = "", days: int = 0) -> dict[str, Any]:
        """The forecast."""
        self.note("forecast", place)
        day = {"date": "x", "conditions": "rain", "high": "14°C", "low": "8°C",
               "rain_chance": "30%"}
        return {"place": place or "Hamburg", "days": [day]}
'''


def ics(*events: str) -> str:
    body = "".join(f"BEGIN:VEVENT\r\n{e}END:VEVENT\r\n" for e in events)
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\n{body}END:VCALENDAR\r\n"


def stamp(day: str, clock: str) -> str:
    return day.replace("-", "") + "T" + clock.replace(":", "") + "00"


def write(root: Path, module: str, name: str, content: Any, text: bool = False) -> None:
    folder = root / "data" / "modules" / module
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(content if text else json.dumps(content), encoding="utf-8")


def make(
    tmp_path: Path, modules: tuple[str, ...], *, place: str = "Bremen", remote: bool = True
) -> Path:
    """A home with these modules (real ones, and the stand-in weather) and a day to show."""
    root = home(tmp_path / "h", remote=remote)
    for name in modules:
        if name == "weather":
            (root / "modules" / "weather").mkdir()
            (root / "modules" / "weather" / "module.yaml").write_text(WEATHER_YAML)
            (root / "modules" / "weather" / "module.py").write_text(WEATHER_PY)
        else:
            shutil.copytree(
                REPO / "modules" / name,
                root / "modules" / name,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
    if place:
        (root / "config" / "modules" / "briefing.yaml").write_text(f"place: {place}\n")
    write(
        root,
        "calendar",
        "calendar.ics",
        ics(
            f"UID:1\r\nSUMMARY:Dentist\r\nDTSTART:{stamp(DAY, '10:00')}\r\n"
            f"DTEND:{stamp(DAY, '11:00')}\r\nLOCATION:Dr. Weiss\r\n",
            f"UID:2\r\nSUMMARY:Standup\r\nDTSTART:{stamp(DAY, '09:15')}\r\n"
            f"DTEND:{stamp(DAY, '09:30')}\r\n",
            f"UID:3\r\nSUMMARY:Mum's birthday\r\nDTSTART;VALUE=DATE:{DAY.replace('-', '')}\r\n",
            f"UID:4\r\nSUMMARY:Tomorrow's thing\r\nDTSTART:{stamp(TOMORROW, '10:00')}\r\n"
            f"DTEND:{stamp(TOMORROW, '11:00')}\r\n",
        ),
        text=True,
    )
    task = {"list": "inbox", "priority": "normal", "created": "2026-11-01T09:00:00"}
    write(
        root,
        "todo",
        "todo.json",
        [
            {"id": "a1", "text": "file taxes", **task, "due": YESTERDAY},
            {"id": "a2", "text": "pay rent", **task, "due": DAY},
            {"id": "a3", "text": "next week", **task, "due": TOMORROW},
            {"id": "a4", "text": "no day", **task},
        ],
    )
    write(
        root,
        "reminders",
        "reminders.json",
        [
            {"id": "r1", "text": "sleep", "kind": "once", "due": 2e9, "when": f"{DAY} 23:58"},
            {"id": "r2", "text": "water", "kind": "once", "due": 1e9, "when": f"{DAY} 07:30"},
            {"id": "r3", "text": "later", "kind": "once", "due": 3e9, "when": f"{TOMORROW} 09:00"},
        ],
    )
    waiting = {
        "id": "q1",
        "title": "Reply to Anna",
        "tool": "hello.greet",
        "args": {"name": "Anna"},
        "detail": "",
        "created": datetime.now().isoformat(timespec="seconds"),
    }
    write(root, "approvals", "queue.json", [waiting])
    return root


ALL = ("approvals", "calendar", "todo", "reminders", "weather", "briefing")


def calls(root: Path) -> list[str]:
    log = root / "data" / "modules" / "weather" / "calls.txt"
    return log.read_text("utf-8").splitlines() if log.exists() else []


def ran(c: Any, tool: str) -> int:
    """How often the dispatcher ran a tool: its entries in the activity log."""
    found = c.get("/api/activity", headers=LOCAL, params={"search": tool}).json()["entries"]
    return sum(1 for e in found if tool in e["text"])


def later(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    """The page's own clock (not the event loop's) is `seconds` further on."""
    real = time.monotonic()
    monkeypatch.setattr(today_api, "time", SimpleNamespace(monotonic=lambda: real + seconds))


def get(c: Any, path: str, who: dict[str, str] = LOCAL) -> Any:
    return c.get(path, headers=who)


# -- which cards exist ------------------------------------------------------------------------


def test_the_cards_follow_the_modules_that_are_on(tmp_path: Path) -> None:
    with client(make(tmp_path, ALL)) as c:
        found = get(c, "/api/today").json()
        assert found["date"] == DAY
        assert found["cards"] == ["approvals", "agenda", "todo", "reminders", "weather"]
        c.post("/api/modules/calendar", headers=LOCAL, json={"enabled": False})
        c.post("/api/modules/weather", headers=LOCAL, json={"enabled": False})
        assert get(c, "/api/today").json()["cards"] == ["approvals", "todo", "reminders"]
        assert get(c, "/api/today/agenda").status_code == 404  # (the module is off)
        assert get(c, "/api/today/weather").status_code == 404


def test_no_module_no_cards(tmp_path: Path) -> None:
    with client(make(tmp_path, (), place="")) as c:
        assert get(c, "/api/today").json()["cards"] == []


def test_approvals_only_while_something_waits(tmp_path: Path) -> None:
    root = make(tmp_path, ("approvals",))
    with client(root) as c:
        assert get(c, "/api/today").json()["cards"] == ["approvals"]
        assert get(c, "/api/today/approvals").json() == {"count": 1}
        write(root, "approvals", "queue.json", [])
        assert get(c, "/api/today").json()["cards"] == []
        assert get(c, "/api/today/approvals").json() == {"count": 0}


def test_the_weather_card_needs_a_place(tmp_path: Path) -> None:
    with client(make(tmp_path, ("weather",), place="")) as c:
        assert get(c, "/api/today").json()["cards"] == []  # (it would only ask "which place?")
    with client(make(tmp_path / "second", ("weather", "briefing"), place="Bremen")) as c:
        assert get(c, "/api/today").json()["cards"] == ["weather"]


# -- each card's data -------------------------------------------------------------------------


def test_the_agenda_has_todays_events_in_order_all_day_first(tmp_path: Path) -> None:
    with client(make(tmp_path, ALL)) as c:
        events = get(c, "/api/today/agenda").json()["events"]
        assert events == [
            {"time": "", "all_day": True, "title": "Mum's birthday", "location": ""},
            {"time": "09:15", "all_day": False, "title": "Standup", "location": ""},
            {"time": "10:00", "all_day": False, "title": "Dentist", "location": "Dr. Weiss"},
        ]


def test_todos_overdue_first_then_due_today(tmp_path: Path) -> None:
    with client(make(tmp_path, ALL)) as c:
        found = get(c, "/api/today/todo").json()
        assert found["overdue"] == [{"text": "file taxes", "due": YESTERDAY, "time": ""}]
        assert found["due"] == [{"text": "pay rent", "due": DAY, "time": ""}]  # (not tomorrow's)


def test_only_todays_reminders_by_time(tmp_path: Path) -> None:
    with client(make(tmp_path, ALL)) as c:
        found = get(c, "/api/today/reminders").json()["reminders"]
        assert found == [{"time": "07:30", "text": "water"}, {"time": "23:58", "text": "sleep"}]


def test_the_weather_is_the_briefings_place_and_values(tmp_path: Path) -> None:
    root = make(tmp_path, ALL, place="Bremen")
    with client(root) as c:
        assert get(c, "/api/today/weather").json() == {
            "place": "Bremen",
            "conditions": "partly cloudy",
            "temperature": "12°C",
            "feels_like": "10°C",
            "low": "8°C",
            "high": "14°C",
            "rain_chance": "30%",
        }
        assert calls(root) == ["now Bremen", "forecast Bremen"]  # (the briefing's place)


# -- the weather and the agenda go through the dispatcher, and are kept ---------------------------


def test_the_weather_goes_through_the_dispatcher_and_is_kept_for_half_an_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make(tmp_path, ALL)
    with client(root) as c:
        get(c, "/api/today/weather")
        assert ran(c, "weather.now") == 1 and ran(c, "weather.forecast") == 1  # (activity log)
        get(c, "/api/today/weather")
        assert len(calls(root)) == 2  # kept: no second call
        get(c, "/api/today/weather?fresh=1")
        assert len(calls(root)) == 4 and ran(c, "weather.now") == 2
        later(monkeypatch, 29 * 60)
        get(c, "/api/today/weather")
        assert len(calls(root)) == 4  # (29 minutes: still kept)
        later(monkeypatch, 31 * 60)
        get(c, "/api/today/weather")
        assert len(calls(root)) == 6


def test_the_agenda_is_kept_for_five_minutes_and_fresh_asks_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with client(make(tmp_path, ALL)) as c:
        get(c, "/api/today/agenda")
        get(c, "/api/today/agenda")
        assert ran(c, "calendar.agenda") == 1  # (a subscribed calendar could use the network)
        get(c, "/api/today/agenda?fresh=1")
        assert ran(c, "calendar.agenda") == 2
        later(monkeypatch, 301)
        get(c, "/api/today/agenda")
        assert ran(c, "calendar.agenda") == 3


def test_the_local_cards_do_not_use_the_dispatcher(tmp_path: Path) -> None:
    with client(make(tmp_path, ALL)) as c:
        for card in ("approvals", "todo", "reminders"):
            assert get(c, f"/api/today/{card}").status_code == 200
        for tool in ("todo.due", "reminders.list", "approvals.pending"):
            assert ran(c, tool) == 0


def test_with_the_kill_switch_on_no_tool_runs_and_the_card_says_so(tmp_path: Path) -> None:
    root = make(tmp_path, ALL)
    with client(root) as c:
        get(c, "/api/today/weather")  # (kept now)
        assert c.post("/api/kill", headers=LOCAL).status_code == 200
        before = (len(calls(root)), ran(c, "calendar.agenda"))
        for card in ("weather", "agenda"):
            for suffix in ("", "?fresh=1"):
                answer = get(c, f"/api/today/{card}{suffix}")
                assert answer.status_code == 503
                assert answer.json()["detail"] == "Starlight is stopped"
        assert (len(calls(root)), ran(c, "calendar.agenda")) == before  # nothing ran
        assert get(c, "/api/today/todo").status_code == 200  # (local files are still readable)
        c.post("/api/resume", headers=LOCAL)
        assert get(c, "/api/today/agenda").status_code == 200


def test_a_failing_tool_gives_that_card_a_reason_and_the_others_still_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TODAY_TEST_WEATHER", "fail")
    with client(make(tmp_path, ALL)) as c:
        bad = get(c, "/api/today/weather")
        assert bad.status_code == 503 and "did not answer" in bad.json()["detail"]
        assert get(c, "/api/today/agenda").status_code == 200
        assert get(c, "/api/today/todo").status_code == 200
        monkeypatch.delenv("TODAY_TEST_WEATHER")
        assert get(c, "/api/today/weather").status_code == 200  # (a failure is not kept)


# -- access: read only ------------------------------------------------------------------------


def test_an_unknown_card_is_404(tmp_path: Path) -> None:
    with client(make(tmp_path, ALL)) as c:
        assert get(c, "/api/today/nothing").status_code == 404
        assert get(c, "/api/today/status").status_code == 404


def test_a_paired_phone_may_read_an_unpaired_request_may_not(tmp_path: Path) -> None:
    with client(make(tmp_path, ALL)) as c:
        for path in ("/api/today", "/api/today/todo", "/api/today/weather", "/today"):
            assert get(c, path, PHONE).status_code == 403, path
        who = phone(pair(c))
        assert get(c, "/api/today", who).json()["date"] == DAY
        assert get(c, "/api/today/agenda", who).status_code == 200
        assert "Today" in get(c, "/today", who).text


def test_no_route_changes_anything(tmp_path: Path) -> None:
    root = make(tmp_path, ALL)
    with client(root) as c:
        for path in ("/api/today", "/api/today/todo", "/api/today/weather", "/api/today/x"):
            for method in ("POST", "PUT", "DELETE", "PATCH"):
                answer = c.request(method, path, headers=LOCAL, json={})
                assert answer.status_code == 405, (method, path)
        assert get(c, "/api/today/todo").json()["due"][0]["text"] == "pay rent"
    assert calls(root) == []


def test_the_page_is_served(tmp_path: Path) -> None:
    with client(make(tmp_path, ("todo",))) as c:
        page = get(c, "/today")
        assert page.status_code == 200 and "<h1" in page.text and "/api/today" in page.text
