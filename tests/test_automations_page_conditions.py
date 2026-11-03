"""The Automations page's If: none, one or several conditions on the event's fields, joined by
"all of these" (and) or "any of these" (or); what the form cannot show (brackets, not, and
with or) is shown written out and saved as it is, so a rule's condition is never lost."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import CHROMIUM
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_language_access import NAMELESS

COMPLEX = "server is nas or (status is down and subject ~ backup)"
DATA = {
    "rules": [],
    "templates": {
        "watch-servers": {"does": "when a server goes down",
                          "rule": {"name": "watch-servers", "on": "network.server_down",
                                   "condition": COMPLEX, "actions": [{"say": "{event.server}"}]}},
        "two-servers": {"does": "nas or router",
                        "rule": {"name": "two-servers", "on": "network.server_down",
                                 "condition": "server == nas and status != ok",
                                 "actions": [{"say": "x"}]}},
    },
    "events": {
        "network.server_down": {"when": "a server stops answering",
                                "fields": ["server", "status", "subject"]},
        "sensors.drive_added": {"when": "a drive is plugged in", "fields": ["label"]},
    },
    "tools": [],
}  # fmt: skip


async def automations_page(playwright: Any, site: str, locale: str = "en-US") -> Any:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None:
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    page = await (await browser.new_context(locale=locale)).new_page()
    page.sent = []  # type: ignore[attr-defined]
    page.errors = []  # type: ignore[attr-defined]
    page.on("pageerror", lambda e: page.errors.append(str(e)))  # type: ignore[attr-defined]
    page.on("dialog", lambda d: d.accept())

    async def answer(route: Any) -> None:
        request = route.request
        if request.method == "GET":
            await route.fulfill(json=DATA)
            return
        body = json.loads(request.post_data or "{}")
        page.sent.append((request.url.rsplit("/api/", 1)[1], body))  # type: ignore[attr-defined]
        if request.url.endswith("/check"):
            await route.fulfill(json={"does": "it", "may_run_without_asking": []})
        else:
            await route.fulfill(json={"saved": body.get("name"), "next": None})

    await page.route("**/api/automations**", answer)
    await page.goto(f"{site}/automations.html")
    await page.wait_for_function("() => document.querySelectorAll('#on option').length === 2")
    return browser, page


async def sent_after(page: Any, button: str, count: int = 1) -> Any:
    """Click ``button``, wait for ``count`` requests to the API, and give the last one."""
    before = len(page.sent)
    await page.locator(button).click()
    for _ in range(200):
        if len(page.sent) >= before + count:
            break
        await asyncio.sleep(0.025)
    assert len(page.sent) >= before + count, "nothing was sent"
    return page.sent[-1]


async def condition_sent(page: Any) -> str | None:
    where, body = await sent_after(page, "#check")
    assert where == "automations/check"
    return body.get("condition")  # type: ignore[no-any-return]


async def test_several_conditions_from_the_form(
    static_site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        browser, page = await automations_page(playwright, static_site)
        try:
            await page.locator("#name").fill("servers")
            await page.locator('input[name="trigger"][value="on"]').check()
            await page.locator("#on").select_option("network.server_down")
            assert await page.locator("#if-none").is_visible()  # (no condition: always)
            assert await condition_sent(page) is None
            await page.locator("#add-if").click()
            await page.locator("#add-if").click()
            rows = page.locator(".if-row")
            await rows.nth(0).locator(".if-value").fill("nas")
            await rows.nth(1).locator(".if-value").fill("router")
            assert await page.locator("#if-join-row").is_visible()  # (two: how they join)
            await page.locator("#if-join").select_option("or")
            assert await condition_sent(page) == "server == nas or server == router"
            await rows.nth(1).locator(".if-field").select_option("subject")
            await rows.nth(1).locator(".if-op").select_option("~")
            await rows.nth(1).locator(".if-value").fill("rock and roll")
            await page.locator("#if-join").select_option("and")
            sent = await condition_sent(page)
            assert sent == 'server == nas and subject ~ "rock and roll"'  # (a value in quotes)
            await rows.nth(0).locator("button").click()  # remove the first
            assert await condition_sent(page) == 'subject ~ "rock and roll"'
            assert not await page.locator("#if-join-row").is_visible()
            await page.locator("#if-mode").click()  # write it out ...
            assert await page.locator("#if-written").input_value() == 'subject ~ "rock and roll"'
            await page.locator("#if-written").fill("server is nas or status is down")
            await page.locator("#if-mode").click()  # ... and back: the form shows it
            assert await page.locator(".if-row").count() == 2
            assert await page.locator("#if-join").input_value() == "or"
            assert await condition_sent(page) == "server == nas or status == down"
            assert await page.evaluate(NAMELESS) == []  # (every field has a name)
            assert page.errors == []
        finally:
            await browser.close()


@pytest.mark.parametrize("locale", ["en-US", "de-DE"])
async def test_a_rule_s_condition_is_never_lost(
    static_site: str, monkeypatch: pytest.MonkeyPatch, locale: str
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    german = locale.startswith("de")
    async with async_api.async_playwright() as playwright:
        browser, page = await automations_page(playwright, static_site, locale)
        try:
            use = page.locator(".template", has_text="watch-servers").locator("button")
            await use.click()  # a condition the form cannot show: written out, as it is
            assert await page.locator("#if-written").is_visible()
            assert await page.locator("#if-written").input_value() == COMPLEX
            assert await page.locator("#if-mode").text_content() == (
                "Formular nutzen" if german else "Use the form"
            )
            await page.locator("#if-mode").click()  # (it cannot go into the form)
            await page.wait_for_selector(".toast")
            assert await page.locator("#if-written").is_visible()
            assert await sent_after(page, "#save", 2) == ("automations", {
                "name": "watch-servers", "on": "network.server_down", "condition": COMPLEX,
                "actions": [{"say": "{event.server}"}],
            })  # fmt: skip
            other = page.locator(".template", has_text="two-servers").locator("button")
            await other.click()  # one the form can show: rows
            await page.wait_for_selector(".if-row")
            assert await page.locator(".if-row").count() == 2
            assert await page.locator(".if-row .if-op").nth(1).input_value() == "!="
            assert await page.locator("#if-join option:checked").text_content() == (
                "Alle davon" if german else "All of these"
            )
            assert await page.locator("#add-if").text_content() == (
                "+ Bedingung hinzufügen" if german else "+ Add a condition"
            )
            assert await condition_sent(page) == "server == nas and status != ok"
            assert page.errors == []
        finally:
            await browser.close()
