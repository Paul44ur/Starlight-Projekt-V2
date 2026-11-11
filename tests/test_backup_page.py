"""The Backup and restore card on the Settings page (stubbed server)."""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.test_chat_page import answer, static_site  # noqa: F401  (a fixture)

MANIFEST = {
    "created": "2026-03-03T10:00:00",
    "starlight_version": "0.1.0",
    "counts": {"conversations": 142, "notes": 310, "memories": 58},
}


def stub(local: bool = True, pending: bool = False, last: str = "null") -> str:
    return f"""
window.calls = [];
window.pendingNow = {str(pending).lower()};
window.fetch = async (url, init) => {{
  const method = (init && init.method) || "GET";
  const json = (data, status = 200) => new Response(JSON.stringify(data),
    {{status, headers: {{"content-type": "application/json"}}}});
  window.calls.push([method, url]);
  if (url === "/api/status") return json({{local: {str(local).lower()}}});
  if (url === "/api/backup")
    return json({{pending: window.pendingNow ? {MANIFEST} : null, last: {last}}});
  if (url === "/api/backup/inspect") {{
    if (init.body.name === "bad.zip") return json({{detail: "This file is not a backup."}}, 400);
    return json({{token: "tok123", manifest: {MANIFEST}}});
  }}
  if (url === "/api/backup/check") {{
    if (window.tooBig) return json({{detail: "Your data is 9.0 GB, more than a backup can "
      + "hold (8.0 GB). The biggest parts: notes/Big (9.0 GB). Move or delete some of it, "
      + "or copy it by hand."}}, 400);
    return json({{left_out: ["1 file over 2.0 GB is not in the backup: notes/Notes/big.bin"]}});
  }}
  if (url === "/api/backup/restore") {{ window.pendingNow = true; return json({{staged: true}}); }}
  if (url === "/api/backup/pending") {{
    window.pendingNow = false;
    return json({{cancelled: true}});
  }}
  return json({{tools: [], rules: {{}}, modules: [], devices: [],
                remote: {{enabled: false, hosts: []}}}});
}};
"""


async def open_settings(playwright: Any, site: str, script: str, german: bool = False) -> Any:
    executable = os.environ.get("STARLIGHT_CHROMIUM")
    if not executable:
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    page = await browser.new_page(viewport={"width": 900, "height": 800})
    if german:
        await page.add_init_script("localStorage.setItem('starlight-lang', 'de')")
    await page.add_init_script(script)
    await page.goto(f"{site}/settings.html")
    await page.wait_for_function("window.calls.length > 1")
    return browser, page


def pick(name: str = "mine.zip") -> dict[str, Any]:
    return {"name": name, "mimeType": "application/zip", "buffer": b"PK"}


async def test_the_card_is_the_last_one_and_is_hidden_from_a_phone(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_settings(playwright, static_site, stub())
        try:
            card = page.locator("#backup-card")
            assert await card.is_visible()
            assert await card.get_attribute("class") == "card local-only"
            assert await card.locator("h2").inner_text() == "Backup and restore"
            last = await page.evaluate("document.querySelector('.page').lastElementChild.id")
            assert last == "backup-card"
            assert await page.locator("#backup-notice").is_hidden()
        finally:
            await browser.close()
        browser, page = await open_settings(playwright, static_site, stub(local=False))
        try:
            await page.wait_for_function("document.body.classList.contains('remote')")
            assert await page.locator("#backup-card").is_hidden()
        finally:
            await browser.close()


async def test_picking_a_file_asks_then_stages_and_can_be_cancelled(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_settings(playwright, static_site, stub())
        try:
            posts = "window.calls.filter(c => c[1] === '/api/backup/restore').length"
            # "No": the summary shows, nothing is sent
            await page.locator("#backup-file").set_input_files(pick())
            asked = await answer(page, yes=False)
            assert asked == (
                "Replace your current conversations, notes, memory and settings with this backup?"
            )
            summary = page.locator("#backup-summary")
            text = await summary.inner_text()
            assert "3 March 2026" in text and "Starlight 0.1.0" in text
            assert "Conversations: 142 · notes: 310 · memories: 58" in text
            assert await page.evaluate(posts) == 0
            assert await page.locator("#backup-notice").is_hidden()
            # "Yes": the token is posted and the notice says what to do
            await page.locator("#backup-file").set_input_files(pick())
            await answer(page, yes=True)
            notice = page.locator("#backup-notice")
            await notice.wait_for(state="visible")
            assert "Close Starlight and start it again" in await notice.inner_text()
            assert await page.evaluate(posts) == 1
            calls = await page.evaluate("window.calls.filter(c => c[1].includes('restore'))")
            assert calls == [["POST", "/api/backup/restore"]]
            # Cancel
            await page.locator("#backup-cancel").click()
            await notice.wait_for(state="hidden")
            assert ["DELETE", "/api/backup/pending"] in await page.evaluate("window.calls")
        finally:
            await browser.close()


async def test_a_refused_file_shows_the_reason(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await open_settings(playwright, static_site, stub())
        try:
            await page.locator("#backup-file").set_input_files(pick("bad.zip"))
            await page.locator(".toast").wait_for()
            assert await page.locator(".toast").inner_text() == "This file is not a backup."
            assert await page.locator("dialog.ask").count() == 0
        finally:
            await browser.close()


async def test_a_staged_restore_and_the_last_outcome_show_on_load(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    done = (
        '{ok: true, at: "2026-11-10T10:00:00", backup_created: "2026-03-03T10:00:00",'
        ' safety: "before-restore-20261110-100000.zip"}'
    )
    failed = '{ok: false, at: "2026-11-10T11:00:00", reason: "disk full"}'
    async with async_api.async_playwright() as playwright:
        browser, page = await open_settings(playwright, static_site, stub(pending=True))
        try:
            notice = page.locator("#backup-notice")
            await notice.wait_for(state="visible")
            assert await page.locator("#backup-cancel").is_visible()
        finally:
            await browser.close()
        browser, page = await open_settings(playwright, static_site, stub(last=done))
        try:
            text = page.locator("#backup-notice-text")
            await text.wait_for(state="visible")
            assert await text.inner_text() == (
                "Restored from the backup of 3 March 2026. What you had before is in "
                "backups/before-restore-20261110-100000.zip."
            )
            assert await page.locator("#backup-cancel").is_hidden()
            await page.reload()  # shown once only
            await page.wait_for_function("window.calls.length > 1")
            await page.wait_for_timeout(200)
            assert await page.locator("#backup-notice").is_hidden()
        finally:
            await browser.close()
        browser, page = await open_settings(playwright, static_site, stub(last=failed))
        try:
            text = page.locator("#backup-notice-text")
            await text.wait_for(state="visible")
            assert await text.inner_text() == "The restore was not applied: disk full"
        finally:
            await browser.close()


async def test_the_card_is_in_german(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    done = (
        '{ok: true, at: "2026-11-10T10:00:00", backup_created: "2026-03-03T10:00:00",'
        ' safety: "before-restore-20261110-100000.zip"}'
    )
    async with async_api.async_playwright() as playwright:
        browser, page = await open_settings(playwright, static_site, stub(last=done), german=True)
        try:
            card = page.locator("#backup-card")
            assert await card.locator("h2").inner_text() == "Sicherung und Wiederherstellung"
            buttons = await card.locator("button:visible").all_inner_texts()
            assert buttons == ["Sicherung speichern", "Aus einer Datei wiederherstellen…"]
            await page.locator("#backup-notice-text").wait_for(state="visible")
            shown = await page.locator("#backup-notice-text").inner_text()
            assert shown.startswith("Wiederhergestellt aus der Sicherung vom 3. März 2026.")
            await page.locator("#backup-file").set_input_files(pick())
            asked = await answer(page, yes=False)
            assert asked.startswith("Deine jetzigen Gespräche, Notizen, Erinnerungen")
            summary = await page.locator("#backup-summary").inner_text()
            assert summary == (
                "Sicherung vom 3. März 2026, Starlight 0.1.0. "
                "Gespräche: 142 · Notizen: 310 · Erinnerungen: 58"
            )
        finally:
            await browser.close()


async def test_a_restore_that_kept_an_old_folder_says_so(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    kept = (
        '{ok: true, at: "2026-11-10T10:00:00", backup_created: "2026-03-03T10:00:00",'
        ' safety: "before-restore-1.zip",'
        ' reason: "Some hidden files of your old notes are still in /n/.Notes.old-x."}'
    )
    async with async_api.async_playwright() as playwright:
        for german, expected in (
            (False, "are still in /n/.Notes.old-x."),
            (True, "liegen noch in"),
        ):
            browser, page = await open_settings(
                playwright, static_site, stub(last=kept), german=german
            )
            try:
                text = page.locator("#backup-notice-text")
                await text.wait_for(state="visible")
                assert expected in await text.inner_text()
            finally:
                await browser.close()


async def test_saving_first_checks_and_shows_what_is_left_out_or_why_not(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for german in (False, True):
            browser, page = await open_settings(playwright, static_site, stub(), german=german)
            try:
                await page.locator("#backup-save").click()
                left = page.locator("#backup-left")
                await left.wait_for(state="visible")
                shown = await left.inner_text()
                if german:
                    assert shown.startswith("1 Datei über 2.0 GB ist nicht in der Sicherung:")
                else:
                    assert shown == "1 file over 2.0 GB is not in the backup: notes/Notes/big.bin."
                await page.evaluate("window.tooBig = true")
                await page.locator("#backup-save").click()
                toast = page.locator(".toast")
                await toast.wait_for()
                text = await toast.inner_text()
                assert text.startswith(
                    "Deine Daten sind 9.0 GB" if german else "Your data is 9.0 GB"
                )
            finally:
                await browser.close()
