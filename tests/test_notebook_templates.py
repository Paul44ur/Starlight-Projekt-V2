"""Block C4 of the notes plan (docs/plan-notes-app.md): templates, the journal's daily page,
and monthly and yearly reviews."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from starlight.core import Kernel, Paths
from starlight.notebook import templates
from tests.conftest import REPO, ScriptedProvider
from tests.test_home_devices import call, fails
from tests.test_remote import LOCAL, client, home

NOW = dt.datetime(2026, 11, 1, 14, 30)  # a Sunday


def test_dates_in_any_format_and_both_languages() -> None:
    assert templates.format_date(NOW, "YYYY-MM-DD") == "2026-11-01"
    assert templates.format_date(NOW, "dddd, D MMMM YYYY") == "Sunday, 1 November 2026"
    assert templates.format_date(NOW, "dddd, D. MMMM YYYY", "de") == "Sonntag, 1. November 2026"
    assert templates.format_date(NOW, "ddd DD.MM.YY", "de") == "So 01.11.26"
    assert templates.format_date(dt.date(2026, 3, 9), "D MMM", "de") == "9 Mär"


async def test_variables_are_filled_and_a_missing_module_leaves_its_place_empty() -> None:
    text = (
        "# {{title}}\n{{date}} {{time}} {{ date:dddd }} {{yesterday}}\n"
        "{{events}}\n{{todo}}\n{{weather}}\n{{unknown}} {{date:D. MMMM}}"
    )
    outputs: dict[str, Any] = {
        "calendar.agenda": {"events": [
            {"title": "Dentist", "start": "2026-11-01 09:00", "end": "09:30", "location": "Town"},
            {"title": "Holiday", "date": "2026-11-01", "all_day": True},
        ]},
        "todo.due": {"overdue": [{"text": "Pay bill"}], "due": [{"text": "Buy milk"}]},
        "weather.now": {"place": "Berlin", "summary": "Sunny, 12 °C"},
    }  # fmt: skip
    asked: list[str] = []

    async def modules(tool: str, args: dict[str, Any]) -> Any:
        asked.append(tool)
        return outputs[tool]

    full = await templates.render(text, title="Plan", lang="de", now=NOW, call=modules)
    assert full.splitlines() == [
        "# Plan",
        "2026-11-01 14:30 Sonntag [[2026-10-31]]",
        "- 09:00\u201309:30 Dentist (Town)",
        "- ganztägig Holiday",
        "- Pay bill (überfällig)",
        "- Buy milk",
        "Berlin: Sunny, 12 °C",
        "{{unknown}} 1. November",
    ]
    assert sorted(asked) == ["calendar.agenda", "todo.due", "weather.now"]

    async def off(tool: str, args: dict[str, Any]) -> Any:
        return None

    async def broken(tool: str, args: dict[str, Any]) -> Any:
        raise RuntimeError("no network")

    for caller in (off, broken, None):
        empty = await templates.render("A{{events}}B{{todo}}C{{weather}}D", now=NOW, call=caller)
        assert empty == "ABCD"
    asked.clear()
    await templates.render("only {{title}}", title="x", call=modules)
    assert asked == []  # a module is asked only for what the template uses


def test_shipped_templates_come_once_in_your_language(tmp_path: Path) -> None:
    root = tmp_path / "Starlight"
    assert templates.names(root, "de") == ["Daily", "Meeting", "Project"]
    daily = root / "Templates" / "Daily.md"
    assert "## Heute" in daily.read_text("utf-8")
    daily.write_text("# My own day\n", encoding="utf-8")
    (root / "Templates" / "Shopping.md").write_text("- [ ] \n", encoding="utf-8")
    assert templates.names(root, "en") == ["Daily", "Meeting", "Project", "Shopping"]
    assert daily.read_text("utf-8") == "# My own day\n"  # never overwritten
    assert templates.find(root, "meeting.md") == root / "Templates" / "Meeting.md"
    assert templates.find(root, "nothing") is None
    english = tmp_path / "Other"
    templates.ensure(english, "en-US")
    assert "## Today" in (english / "Templates" / "Daily.md").read_text("utf-8")


@pytest.fixture
def journal(
    paths: Paths,
    use_module: Callable[[str], Path],
    documents: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    for name in ("notes", "journal", "todo"):
        use_module(name)
    monkeypatch.setattr(templates, "system_language", lambda: "en")
    return documents / "Starlight"


async def test_notes_from_a_template_and_the_daily_journal_page(
    paths: Paths, journal: Path
) -> None:
    today = dt.date.today()
    async with Kernel(paths) as k:
        assert (await call(k, "notes.templates"))["templates"] == ["Daily", "Meeting", "Project"]
        await call(k, "todo.add", text="Call the garage", due="today")
        made = await call(k, "notes.from_template", template="meeting", title="Müller call")
        meeting = (journal / "Notes" / "Müller call.md").read_text("utf-8")
        assert made["template"] == "Meeting" and meeting.startswith("---\ntags: [meeting]\n")
        assert f"date: {today.isoformat()}" in meeting and "# Müller call" in meeting
        assert "already a note" in await fails(
            k, "notes.from_template", template="Meeting", title="Müller call"
        )
        assert "Daily, Meeting, Project" in await fails(
            k, "notes.from_template", template="Party", title="x"
        )

        page = await call(k, "journal.today")
        day = journal / "Journal" / f"{today.isoformat()}.md"
        text = day.read_text("utf-8")
        assert page["new"] and text.startswith(
            f"# {templates.format_date(today, 'dddd, D MMMM YYYY')}"
        )
        assert "## Today\n\n" in text and "## Due\n- Call the garage\n" in text  # (no calendar)
        assert (await call(k, "journal.today"))["new"] is False
        await call(k, "journal.write", text="A good day.")
        assert day.read_text("utf-8").startswith(text)  # the entry goes below the page

        await call(k, "journal.write", text="Earlier.", day="yesterday")
        yesterday = journal / "Journal" / f"{(today - dt.timedelta(days=1)).isoformat()}.md"
        assert "## Due\n\n" in yesterday.read_text("utf-8")  # today's to-dos are not yesterday's


async def test_monthly_and_yearly_reviews(paths: Paths, journal: Path) -> None:
    folder = journal / "Journal"
    folder.mkdir(parents=True)
    (folder / "2026-10-03.md").write_text("Hiked in the hills.\n", encoding="utf-8")
    (folder / "2026-10-20.md").write_text("Started the new job.\n", encoding="utf-8")
    (folder / "Weekly").mkdir()
    (folder / "Weekly" / "2026-W43.md").write_text("A busy week.\n", encoding="utf-8")
    (folder / "2026-09-10.md").write_text("Moved house.\n", encoding="utf-8")
    provider = ScriptedProvider("October: hills and a new job.", "2026: moving and a new job.")
    async with Kernel(paths, provider=provider) as k:
        month = await call(k, "journal.month", month="2026-10")
        assert Path(month["saved"]) == folder / "Monthly" / "2026-10.md"
        sent = provider.calls[0][1].content
        assert (
            "Hiked" in sent and "new job" in sent and "A busy week" in sent and "Moved" not in sent
        )
        assert (folder / "Monthly" / "2026-10.md").read_text("utf-8").startswith("# October 2026\n")
        year = await call(k, "journal.year", year="2026")
        sent = provider.calls[1][1].content
        assert "Monthly review 2026-10" in sent and "Journal 2026-09" in sent and "Moved" in sent
        assert Path(year["saved"]) == folder / "Yearly" / "2026.md"
        assert "nothing for 2025-01" in await fails(k, "journal.month", month="2025-01")
        assert "which month" in await fails(k, "journal.month", month="soon")


def test_month_names_are_understood() -> None:
    from modules.journal.module import parse_month

    today = dt.date(2026, 1, 15)
    assert parse_month("", today) == dt.date(2026, 1, 1)
    assert parse_month("last", today) == dt.date(2025, 12, 1)
    assert parse_month("10.2025", today) == dt.date(2025, 10, 1)
    assert parse_month("2025-07", today) == dt.date(2025, 7, 1)


async def test_the_automation_templates_for_reviews_and_plans(
    paths: Paths, use_module: Callable[[str], Path]
) -> None:
    for name in ("automations", "journal", "notes"):
        use_module(name)
    async with Kernel(paths) as k:
        listed = await call(k, "automations.templates")
    assert "monthly-review" in listed and "weekly-plan" in listed
    assert "journal.month" in str(listed["monthly-review"])


def test_the_api_makes_notes_from_templates_and_the_days_page(
    tmp_path: Path, documents: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = home(tmp_path / "h")
    import shutil

    for name in ("notes", "journal"):
        shutil.copytree(REPO / "modules" / name, root / "modules" / name)
    monkeypatch.setattr(templates, "system_language", lambda: "de")
    with client(root) as c:
        listed = c.get("/api/notes/templates", params={"lang": "de"}, headers=LOCAL).json()
        assert listed["templates"] == ["Daily", "Meeting", "Project"]
        body = {"folder": "Notes", "title": "Projekt X", "template": "Project", "lang": "de"}
        made = c.post("/api/note", json=body, headers=LOCAL).json()
        text = (documents / "Starlight" / made["path"]).read_text("utf-8")
        assert "# Projekt X" in text and "status: offen" in text
        missing = c.post("/api/note", json={**body, "template": "Nope"}, headers=LOCAL)
        assert missing.status_code == 404
        day = c.post("/api/notes/today", json={"lang": "de"}, headers=LOCAL).json()
        assert day["new"] and day["path"] == f"Journal/{dt.date.today().isoformat()}.md"
        assert "## Heute" in (documents / "Starlight" / day["path"]).read_text("utf-8")
        again = c.post("/api/notes/today", json={}, headers=LOCAL).json()
        assert again["new"] is False
