"""Block B6 of the notes plan (docs/plan-notes-app.md): editing on the Notes page. It runs in
Chromium against the real web app (tests/test_notes_page.notes_page): typing saves on its
own, nothing is ever overwritten, and every earlier version can come back."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_notes_page import app as app  # (the fixtures)
from tests.test_notes_page import notebook as notebook
from tests.test_notes_page import notes_page, opened

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
SAVED = "() => document.getElementById('save-state')?.textContent === 'Saved'"


async def start_editing(page: Any, static_site: str, path: str = "Notes%2FPlan.md") -> None:
    await opened(page, f"{static_site}/notes.html?path={path}")
    await page.wait_for_selector("#edit-toggle")
    await page.locator("#edit-toggle").click()
    await page.wait_for_selector(".cm-content")


async def type_at_end(page: Any, text: str) -> None:
    await page.locator(".cm-content").click()
    await page.keyboard.press("Control+End")
    await page.keyboard.type(text)


async def test_typing_saves_on_its_own_and_the_editor_loads_only_to_edit(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        loaded: list[str] = []
        page.on("request", lambda r: loaded.append(r.url))
        await opened(page, f"{static_site}/notes.html?path=Notes%2FPlan.md")
        await page.wait_for_selector("#edit-toggle")
        assert not any("vendor/editor.js" in url for url in loaded)  # reading needs no editor
        await page.locator("#edit-toggle").click()
        await page.wait_for_selector(".cm-content")
        assert any("vendor/editor.js" in url for url in loaded)
        assert await page.locator("#edit-toggle").text_content() == "✓ Done"
        assert await page.locator(".title-input").input_value() == "Plan"
        await type_at_end(page, "\nA new line.")
        await page.wait_for_function(SAVED)
        file = notebook / "Notes" / "Plan.md"
        assert file.read_text("utf-8").endswith("The word Settings stays.\n\nA new line.")
        versions = app.get(
            "/api/note/versions",
            params={"path": "Notes/Plan.md"},
            headers={"host": "127.0.0.1:8700"},
        ).json()
        assert len(versions["versions"]) == 1  # the text before this editing session
        await page.keyboard.press("Control+e")  # back to reading
        await page.wait_for_selector("#column .note-body")
        assert "A new line." in await page.locator("#column .note-body").text_content()
        await page.reload()
        await page.wait_for_selector("#column .note-body")
        assert "A new line." in await page.locator("#column .note-body").text_content()
        assert page.errors == []  # type: ignore[attr-defined]


async def test_toolbar_slash_menu_and_task_ticking(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await start_editing(page, static_site)
        await type_at_end(page, "\nbold")
        for _ in range(4):
            await page.keyboard.press("Shift+ArrowLeft")
        await page.locator('.toolbar [data-tool="bold"]').click()
        await page.keyboard.press("End")
        await page.keyboard.type("\n/dat")
        await page.wait_for_selector(".cm-tooltip-autocomplete")
        await page.wait_for_timeout(150)  # (Enter picks an item only after 75 ms)
        await page.keyboard.press("Enter")
        await page.locator('.toolbar [data-tool="heading"]').click()
        await page.keyboard.press("End")
        await page.keyboard.type("\n/task")
        await page.wait_for_selector(".cm-tooltip-autocomplete")
        await page.wait_for_timeout(150)  # (Enter picks an item only after 75 ms)
        await page.keyboard.press("Enter")
        await page.keyboard.type("buy apples")
        await page.keyboard.press("Control+Enter")  # ticks it
        await page.keyboard.press("Enter")  # the list goes on
        assert (await page.locator(".cm-activeLine").text_content() or "").startswith("- [ ]")
        await page.wait_for_function(SAVED)
        text = (notebook / "Notes" / "Plan.md").read_text("utf-8")
        today = await page.evaluate(
            "() => { const d = new Date(); return [d.getFullYear(), String(d.getMonth() + 1)"
            ".padStart(2, '0'), String(d.getDate()).padStart(2, '0')].join('-'); }"
        )
        assert text.endswith(f"\n**bold**\n# {today}\n- [x] buy apples\n- [ ] "), text[-60:]
        assert page.errors == []  # type: ignore[attr-defined]


async def test_a_change_made_elsewhere_is_never_overwritten(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await start_editing(page, static_site)
        file = notebook / "Notes" / "Plan.md"
        file.write_text("Changed in Notepad.\n", encoding="utf-8")
        await type_at_end(page, "\nMine.")
        await page.wait_for_selector("#conflict:not([hidden])")
        assert await page.locator("#theirs").text_content() == "Changed in Notepad.\n"
        assert file.read_text("utf-8") == "Changed in Notepad.\n"  # not overwritten
        await page.locator("#keep-both").click()
        await page.wait_for_function(SAVED)
        text = file.read_text("utf-8")
        assert text.startswith("Changed in Notepad.\n\n---\n\n# The plan") and text.endswith(
            "Mine."
        )
        assert page.errors == []  # type: ignore[attr-defined]


async def test_pasting_a_picture_puts_it_into_the_note(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await start_editing(page, static_site)
        await page.locator(".cm-content").click()
        await page.keyboard.press("Control+End")
        await page.evaluate(
            """bytes => {
              const data = new DataTransfer();
              const file = new File([new Uint8Array(bytes)], 'holiday.png', { type: 'image/png' });
              data.items.add(file);
              const paste = new ClipboardEvent('paste',
                { clipboardData: data, bubbles: true, cancelable: true });
              document.querySelector('.cm-content').dispatchEvent(paste);
            }""",
            list(PNG),
        )
        await page.wait_for_function(
            SAVED + " && document.querySelector('.cm-content').textContent.includes('holiday.png')"
        )
        assert (notebook / "Notes" / "Attachments" / "holiday.png").read_bytes() == PNG
        assert "![[holiday.png]]" in (notebook / "Notes" / "Plan.md").read_text("utf-8")
        assert page.errors == []  # type: ignore[attr-defined]


async def test_an_earlier_version_comes_back(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        await start_editing(page, static_site)
        await page.locator(".cm-content").click()
        await page.keyboard.press("Control+a")
        await page.keyboard.type("Everything replaced.")
        await page.wait_for_function(SAVED)
        file = notebook / "Notes" / "Plan.md"
        assert file.read_text("utf-8") == "Everything replaced."
        await page.locator('button[title="Earlier versions"]').click()
        await page.wait_for_selector("#history:not([hidden]) #versions button")
        await page.locator("#versions button").first.click()
        await page.wait_for_selector("#version-view pre")
        removed = await page.locator("#version-view .del").all_text_contents()
        assert any("The word Settings stays." in line for line in removed)
        await page.locator("#restore-version").click()
        await page.wait_for_selector("#history[hidden]", state="attached")
        await page.wait_for_selector(".cm-content")  # still editing, with the old text back
        assert (
            file.read_text("utf-8")
            == "# The plan\nBack to [[Shopping#Food]].\n\nThe word Settings stays.\n"
        )
        assert "The word Settings stays." in await page.locator(".cm-content").text_content()
        assert page.errors == []  # type: ignore[attr-defined]


async def test_new_rename_delete_and_bring_back(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app) as page:
        page.on("dialog", lambda dialog: dialog.accept())  # "Move this note to the trash?"
        await opened(page, f"{static_site}/notes.html?path=Notes%2FPlan.md")
        await page.wait_for_selector("#edit-toggle")
        await page.locator("#new-note").click()
        await page.wait_for_selector(".title-input")
        assert await page.locator(".title-input").input_value() == "Untitled"
        assert (notebook / "Notes" / "Untitled.md").is_file()
        await page.keyboard.type("Groceries")  # (the title is selected: typing replaces it)
        await page.keyboard.press("Enter")
        await page.wait_for_function("() => location.search.includes('Groceries')")
        await page.keyboard.type("Rice and beans.")
        await page.wait_for_function(SAVED)
        groceries = notebook / "Notes" / "Groceries.md"
        assert (
            groceries.read_text("utf-8") == "Rice and beans."
            and not (notebook / "Notes" / "Untitled.md").exists()
        )
        assert await page.locator('.side .item[aria-current="page"]').text_content() == "Groceries"

        await page.locator('button[title="Move to the trash"]').click()
        await page.wait_for_selector(".trash-row")
        assert not groceries.exists()
        assert await page.locator(".trash-row .t").all_text_contents() == ["Groceries"]
        await page.locator(".trash-row button", has_text="Bring back").click()
        await page.wait_for_function(
            "() => document.querySelector('h1.title')?.textContent === 'Groceries'"
        )
        assert groceries.read_text("utf-8") == "Rice and beans."
        assert page.errors == []  # type: ignore[attr-defined]


async def test_editing_in_german(
    static_site: str, app: Any, notebook: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with notes_page(static_site, app, locale="de-DE") as page:
        await opened(page, f"{static_site}/notes.html?path=Notes%2FPlan.md")
        await page.wait_for_selector("#edit-toggle")
        assert await page.locator("#edit-toggle").text_content() == "✎ Bearbeiten"
        await page.locator("#edit-toggle").click()
        await page.wait_for_selector(".cm-content")
        assert (
            await page.locator('.toolbar [data-tool="bold"]').get_attribute("title")
            == "Fett (Strg+B)"
        )
        assert await page.locator("#save-state").text_content() == "Gespeichert"
        assert await page.locator("#edit-toggle").text_content() == "✓ Fertig"
        assert page.errors == []  # type: ignore[attr-defined]
