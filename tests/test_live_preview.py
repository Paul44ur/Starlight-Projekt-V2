"""Block G4 of the notes plan (docs/plan-notes-app.md): live preview in the editor. The
Markdown marks (# , **, *, ~~, `, a link's [ ](address), [[ | ]]) hide on the lines the
cursor is not on, a bullet shows as • and a task as a box to tick; the note's text itself
never changes. The toolbar's 👁 switches it off (remembered in the browser)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_editing import start_editing
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page

GARDEN = """---
tags: [demo]
---
# Garden plan

Buy **seeds** and *soil*, ~~not the old~~ `tools`, see [[Tools|the tools]] and [[Shed]].
A [web **link**](https://example.org/x) here, [a
b](https://example.org/y) and [c](
https://example.org/z) over lines.

- first point
- [ ] Water the roses due: 2026-11-05
- [x] Dig the bed

```
# not a heading **kept**
```
![[photo.png]]
"""
PREVIEW = [
    "---", "tags: [demo]", "---",  # the properties block stays as written
    "Garden plan", "",
    "Buy seeds and soil, not the old tools, see the tools and Shed.",
    "A web link here, a", "b and c", " over lines.", "",  # (links over lines too)
    "• first point",
    " Water the roses due: 2026-11-05",  # (the box is an input: no text)
    " Dig the bed", "",
    "```", "# not a heading **kept**", "```",  # code stays as written
    "![[photo.png]]",  # an embed too
    "",
]  # fmt: skip


async def lines(page: Any) -> list[str]:
    return await page.locator(".cm-line").all_text_contents()  # type: ignore[no-any-return]


async def saved(page: Any, german: bool = False) -> None:
    word = "Gespeichert" if german else "Saved"
    await page.wait_for_function(
        f"() => document.getElementById('save-state')?.textContent === '{word}'"
    )


@pytest.mark.parametrize(("scheme", "locale"), [("light", "en-US"), ("dark", "de-DE")])
async def test_the_marks_hide_away_from_the_cursor(
    static_site: str,
    app: Any,
    notebook: Path,
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
    locale: str,
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    note = notebook / "Notes" / "Garden.md"
    note.write_text(GARDEN, encoding="utf-8")
    async with notes_page(static_site, app, locale=locale, scheme=scheme) as page:
        await start_editing(page, static_site, "Notes%2FGarden.md")
        await page.keyboard.press("Control+End")  # (the cursor on the last, empty line)
        await page.wait_for_function("() => document.querySelector('.cm-bullet')")
        assert await lines(page) == PREVIEW
        assert await page.locator(".cm-task-box").evaluate_all("bs => bs.map(b => b.checked)") == [
            False,
            True,
        ]
        await page.keyboard.press("Control+Home")  # onto the lines of the text: marks show
        for _ in range(5):
            await page.keyboard.press("ArrowDown")
        shown = await lines(page)
        assert shown[5] == (
            "Buy **seeds** and *soil*, ~~not the old~~ `tools`, see [[Tools|the tools]] and "
            "[[Shed]]."
        )
        assert shown[3] == "Garden plan" and shown[6] == "A web link here, a"  # (others: not)
        await page.keyboard.press("ArrowUp")
        await page.keyboard.press("ArrowUp")
        assert (await lines(page))[3] == "# Garden plan"
        box = page.locator(".cm-task-box").first  # a box ticks its task
        assert await box.get_attribute("aria-label") == (
            "Abhaken" if locale == "de-DE" else "Tick it off"
        )
        await box.click()
        await saved(page, locale == "de-DE")
        today = dt.date.today().isoformat()
        text = note.read_text("utf-8")
        assert f"- [x] Water the roses due: 2026-11-05 done: {today}\n" in text
        assert text == GARDEN.replace(
            "- [ ] Water the roses due: 2026-11-05",
            f"- [x] Water the roses due: 2026-11-05 done: {today}",
        )  # (nothing else changed: the hidden marks are all still there)
        assert (await lines(page))[3] == "# Garden plan"  # (the cursor stayed where it was)
        assert page.errors == []  # type: ignore[attr-defined]


async def test_switching_it_off_is_remembered(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    (notebook / "Notes" / "Garden.md").write_text(GARDEN, encoding="utf-8")
    async with notes_page(static_site, app) as page:
        await start_editing(page, static_site, "Notes%2FGarden.md")
        await page.keyboard.press("Control+End")
        eye = page.locator('.toolbar [data-tool="preview"]')
        assert await eye.get_attribute("aria-pressed") == "true"
        await eye.click()
        assert await eye.get_attribute("aria-pressed") == "false"
        assert await lines(page) == GARDEN.split("\n")  # every mark shows
        assert await page.locator(".cm-task-box, .cm-bullet").count() == 0
        await page.reload()  # (the next time too)
        await start_editing(page, static_site, "Notes%2FGarden.md")
        await page.keyboard.press("Control+End")
        assert await page.locator('.toolbar [data-tool="preview"]').get_attribute(
            "aria-pressed"
        ) == ("false")
        assert (await lines(page))[3] == "# Garden plan"
        await page.locator('.toolbar [data-tool="preview"]').click()  # and on again
        assert (await lines(page))[3] == "Garden plan"
        await page.keyboard.type("More **text**.")
        await saved(page)
        file = (notebook / "Notes" / "Garden.md").read_text("utf-8")
        assert file == GARDEN + "More **text**."  # (what is typed is kept as typed)
        assert page.errors == []  # type: ignore[attr-defined]
