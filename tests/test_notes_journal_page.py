"""Block C4 of the notes plan (docs/plan-notes-app.md) on the Notes page: a new note from a
template, today's journal page, and the calendar strip."""

from __future__ import annotations

import datetime as dt
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from starlight.notebook import templates
from tests.conftest import REPO
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_page import notebook as notebook  # (the fixture)
from tests.test_notes_page import notes_page, opened
from tests.test_remote import client, home


@pytest.fixture
def journal_app(tmp_path: Path, notebook: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setattr(templates, "system_language", lambda: "en")
    root = home(tmp_path / "h")
    for name in ("notes", "journal"):
        shutil.copytree(REPO / "modules" / name, root / "modules" / name)
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    (notebook / "Journal" / f"{yesterday}.md").write_text("A walk.\n", encoding="utf-8")
    with client(root) as c:
        yield c


async def test_a_new_note_from_a_template(
    static_site: str, journal_app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, journal_app) as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FPlan.md")
        await page.locator("#new-note").click()
        await page.wait_for_selector(".new-menu [data-template]")
        names = await page.locator(".new-menu button").all_text_contents()
        assert names == ["Empty note", "Daily", "Meeting", "Project"]
        await page.locator('.new-menu [data-template="Meeting"]').click()
        await page.wait_for_selector(".title-input")
        today = dt.date.today().isoformat()
        assert await page.locator(".title-input").input_value() == f"Meeting {today}"
        text = (notebook / "Notes" / f"Meeting {today}.md").read_text("utf-8")
        assert f"# Meeting {today}" in text and "## Decisions" in text
        lines = await page.locator(".cm-line").all_text_contents()  # the editor has it too
        assert "Decisions" in lines  # (live preview hides "## " away from the cursor line)
        assert page.errors == []  # type: ignore[attr-defined]


async def test_today_and_the_calendar_strip(
    static_site: str, journal_app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    today = dt.date.today()
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    async with notes_page(static_site, journal_app) as page:
        await opened(page, f"{static_site}/notes.html")
        await page.locator('.quick button[data-view="today"]').click()
        await page.wait_for_selector(".strip .day")
        days = await page.locator(".strip .day").evaluate_all(
            "ds => ds.map(d => [d.dataset.day, d.classList.contains('has'),"
            " d.classList.contains('today')])"
        )
        assert len(days) == 14 and days[-1] == [today.isoformat(), False, True]
        assert days[-2] == [yesterday, True, False]
        await page.locator(".soon button", has_text="Write today's page").click()
        await page.wait_for_selector(".cm-content")
        page_file = notebook / "Journal" / f"{today.isoformat()}.md"
        assert page_file.read_text("utf-8").startswith(
            "# " + templates.format_date(today, "dddd, D MMMM YYYY")
        )
        await page.keyboard.press("Control+e")  # back to reading: the strip shows today too
        await page.wait_for_selector(".strip .day.today.has.open")
        await page.locator(f'.strip .day[data-day="{yesterday}"]').click()
        await page.wait_for_function(
            f"() => document.querySelector('h1.title')?.textContent === '{yesterday}'"
        )
        assert "A walk." in await page.locator("#column .note-body").text_content()
        assert page.errors == []  # type: ignore[attr-defined]
