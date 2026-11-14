"""Block R8, the page: under each rule "Last ran <time> · <how it went>" (or "Has not run yet"),
a History button that opens the rule's last runs, and "Use" on a ready-made rule that fills
German texts on a German page. A reason is outside content: it stays text and is not translated."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.test_chat_page import CHROMIUM
from tests.test_chat_page import static_site as static_site  # (the fixture)


@pytest.fixture
async def playwright(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as started:
        yield started


EVIL = '<img src=x onerror="window.pwned = 1"> and more'


def rule(name: str, last: dict[str, str] | None, **more: Any) -> dict[str, Any]:
    return {
        "name": name, "enabled": True, "does": "every day at 07:30: tell you “hi”",
        "next": "2026-11-13 07:30", "may_run_without_asking": [], "last_run": last, **more,
    }  # fmt: skip


def run(outcome: str, text: str = "", when: str = "2026-11-12 07:30") -> dict[str, str]:
    return {"time": when, "outcome": outcome, "text": text}


RULES = [
    rule("r-done", run("done")),
    rule("r-failed", run("failed", EVIL)),
    rule("r-waiting", run("waiting")),
    rule("r-skipped", run("skipped")),
    rule("r-stopped", run("stopped", "panic")),
    rule("r-never", None),
]
HISTORY = {
    "r-failed": [run("failed", EVIL, f"2026-11-{day:02d} 07:30") for day in range(12, 2, -1)],
    "r-done": [run("done", "", "2026-11-12 07:30"), run("failed", "boom", "2026-11-11 07:30")],
    "r-never": [],
}
TEMPLATES = {
    "evening-todo": {
        "does": "x",
        "rule": {
            "name": "evening-todo", "when": "every day at 18:00",
            "actions": [
                {"tool": "todo.due", "args": {"days": 1}},
                {"say": "Still open for today and tomorrow: {result.count} task(s)",
                 "only_if": "count > 0"},
                {"ask": "Run all my backups (backup.list, then backup.run for each)."},
            ],
        },
    },
}  # fmt: skip


@asynccontextmanager
async def automations(
    playwright: Any, site: str, *, lang: str = "en", scheme: str = "dark", width: int = 1100,
    rules: list[dict[str, Any]] | None = None,
) -> AsyncIterator[Any]:  # fmt: skip
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None:
        pytest.skip("no Chromium for Playwright")
    browser = await playwright.chromium.launch(executable_path=executable)
    context = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme,  # type: ignore[arg-type]
        locale="de-DE" if lang == "de" else "en-US",
    )  # fmt: skip
    page = await context.new_page()
    page.history_calls = []  # type: ignore[attr-defined]
    page.errors = []  # type: ignore[attr-defined]
    page.on("pageerror", lambda e: page.errors.append(str(e)))  # type: ignore[attr-defined]
    await page.add_init_script(
        f"try {{ localStorage.setItem('starlight-lang', '{lang}');"
        f" localStorage.setItem('starlight-theme', '{scheme}'); }} catch (e) {{}}"
    )
    data = {
        "rules": RULES if rules is None else rules,
        "templates": TEMPLATES,
        "events": {"network.server_down": {"when": "a server stops answering", "fields": ["a"]}},
        "tools": [],
    }

    async def answer(route: Any) -> None:
        url = route.request.url
        if url.endswith("/history"):
            name = url.rsplit("/", 2)[1]
            page.history_calls.append(name)
            await route.fulfill(json={"rule": name, "runs": HISTORY.get(name, [])})
        elif route.request.method == "GET":
            await route.fulfill(json=data)
        else:
            await route.fulfill(json={"ok": True})

    await page.route("**/api/automations**", answer)
    await page.goto(f"{site}/automations.html")
    try:
        await page.wait_for_selector(".rule .last", timeout=5000)
        yield page
    finally:
        assert page.errors == []
        await browser.close()


LINES = "() => Object.fromEntries([...document.querySelectorAll('.rule')].map(r => [r.querySelector('b').textContent, r.querySelector('.last').innerText]))"  # noqa: E501


async def test_each_rule_says_when_it_last_ran_and_how_it_went(
    playwright: Any, static_site: str
) -> None:
    async with automations(playwright, static_site) as page:
        lines = await page.evaluate(LINES)
        assert lines["r-done"] == "Last ran 2026-11-12 07:30 · done"
        assert lines["r-waiting"] == "Last ran 2026-11-12 07:30 · waiting for your OK"
        assert lines["r-skipped"] == "Last ran 2026-11-12 07:30 · skipped"
        assert (
            lines["r-stopped"] == "Last ran 2026-11-12 07:30 · stopped by the kill switch · panic"
        )
        assert lines["r-failed"] == f"Last ran 2026-11-12 07:30 · failed · {EVIL}"
        assert lines["r-never"] == "Has not run yet"
        # the time is in .num, the word has the colour of its outcome
        colours = await page.evaluate(
            """() => {
              const probe = (token) => { const p = document.createElement('i');
                p.style.color = `var(${token})`; document.body.append(p);
                const c = getComputedStyle(p).color; p.remove(); return c; };
              const word = (name) => { const row = [...document.querySelectorAll('.rule')]
                .find(r => r.querySelector('b').textContent === name);
                return getComputedStyle(row.querySelector('.last [class^=run-]')).color; };
              const num = document.querySelector('.rule .last .num').textContent;
              return {num, done: [word('r-done'), probe('--ok')],
                failed: [word('r-failed'), probe('--bad')],
                stopped: [word('r-stopped'), probe('--bad')],
                waiting: [word('r-waiting'), probe('--warn')],
                skipped: [word('r-skipped'), probe('--dim')]};
            }"""
        )
        assert colours.pop("num") == "2026-11-12 07:30"
        for name, (got, wanted) in colours.items():
            assert got == wanted, name


async def test_a_reason_stays_text_and_is_not_translated(playwright: Any, static_site: str) -> None:
    async with automations(playwright, static_site) as page:
        assert await page.evaluate("() => window.pwned") is None
        assert await page.locator(".rule .last img").count() == 0
        why = page.locator(".rule .last span[translate=no]", has_text="onerror")
        assert await why.count() == 1
    async with automations(playwright, static_site, lang="de") as page:
        german = await page.evaluate(LINES)
        assert german["r-failed"] == f"Zuletzt gelaufen 2026-11-12 07:30 · fehlgeschlagen · {EVIL}"
        assert german["r-never"] == "Noch nicht gelaufen"
        assert german["r-waiting"].endswith("· wartet auf dein OK")
        assert german["r-skipped"].endswith("· übersprungen")
        assert german["r-stopped"].endswith("· vom Not-Aus gestoppt · panic")
        assert german["r-done"].endswith("· fertig")
        # the reason "boom" is the tool's word, not ours: it stays as it is
        await page.locator(".rule", has_text="r-done").get_by_role("button", name="Verlauf").click()
        await page.wait_for_selector(".runs li")
        assert await page.locator(".runs li").nth(1).inner_text() == (
            "2026-11-11 07:30 · fehlgeschlagen · boom"
        )


async def test_history_opens_shows_the_runs_and_closes(playwright: Any, static_site: str) -> None:
    async with automations(playwright, static_site) as page:
        row = page.locator(".rule", has_text="r-failed")
        button = row.get_by_role("button", name="History")
        assert await button.get_attribute("aria-expanded") == "false"
        assert await row.locator(".runs").count() == 0 and page.history_calls == []
        await button.click()
        await page.wait_for_selector(".runs li")
        assert page.history_calls == ["r-failed"]  # fetched when it was opened
        assert await button.get_attribute("aria-expanded") == "true"
        assert await row.locator(".runs li").count() == 10
        assert await row.locator(".runs li").first.inner_text() == (
            f"2026-11-12 07:30 · failed · {EVIL}"
        )
        assert await page.evaluate("() => window.pwned") is None
        # the plain button is not the gold one, and sits with the other buttons of the rule
        assert "primary" not in (await button.get_attribute("class") or "")
        assert await row.locator(".actions button").count() == 4
        await button.click()
        assert await button.get_attribute("aria-expanded") == "false"
        assert await row.locator(".runs").count() == 0
        # a rule that never ran
        never = page.locator(".rule", has_text="r-never")
        await never.get_by_role("button", name="History").click()
        await page.wait_for_selector(".rule .runs li")
        assert await never.locator(".runs li").inner_text() == "Has not run yet"


async def test_run_now_refreshes_the_line_and_an_open_history(
    playwright: Any, static_site: str
) -> None:
    async with automations(playwright, static_site) as page:
        await page.locator(".rule", has_text="r-done").get_by_role("button", name="History").click()
        await page.wait_for_selector(".runs li")
        calls = len(page.history_calls)
        await page.locator(".rule", has_text="r-done").get_by_role("button", name="Run now").click()
        for _ in range(100):
            if len(page.history_calls) > calls:
                break
            await page.wait_for_timeout(50)
        assert len(page.history_calls) > calls  # the open history was read again
        row = page.locator(".rule", has_text="r-done")
        await row.locator(".runs li").first.wait_for()
        assert await row.get_by_role("button", name="History").get_attribute("aria-expanded") == (
            "true"
        )


async def test_use_fills_german_texts_only_on_a_german_page(
    playwright: Any, static_site: str
) -> None:
    async def used(page: Any) -> list[str]:
        await page.locator("#templates button").click()
        await page.wait_for_selector("#steps .step")
        return await page.evaluate(
            "() => [...document.querySelectorAll('#steps .step')].map(s => "
            "[...s.querySelectorAll('input, textarea')].filter(e => !e.hidden)"
            ".map(e => e.value).filter(Boolean).join(' | '))"
        )

    async with automations(playwright, static_site, lang="de") as page:
        steps = await used(page)
        assert steps[0] == 'todo.due | {"days":1}'
        assert steps[1] == "Für heute und morgen noch offen: {result.count} Aufgabe(n) | count > 0"
        assert steps[2] == (
            "Führe alle meine Sicherungen aus (backup.list, dann backup.run für jede)."
        )
    async with automations(playwright, static_site) as page:
        steps = await used(page)
        assert steps[0] == 'todo.due | {"days":1}'
        assert steps[1] == "Still open for today and tomorrow: {result.count} task(s) | count > 0"
        assert steps[2] == "Run all my backups (backup.list, then backup.run for each)."


async def test_nothing_sticks_out_at_360_px(playwright: Any, static_site: str) -> None:
    long_reason = "x" * 120 + " " + "y" * 90
    rules = [
        rule("a-rather-long-rule-name-here", run("failed", long_reason), problem="x"),
        rule("r-stopped", run("stopped", "panic"), enabled=False),
        rule("r-never", None),
    ]
    for lang in ("en", "de"):
        for scheme in ("dark", "light"):
            async with automations(
                playwright, static_site, lang=lang, scheme=scheme, width=360, rules=rules
            ) as page:
                word = "History" if lang == "en" else "Verlauf"
                await page.locator(".rule .actions button", has_text=word).first.click()
                await page.wait_for_selector(".runs li")
                wide = await page.evaluate("() => document.documentElement.scrollWidth")
                assert wide <= 360, (lang, scheme, wide)
                # every button of a rule is inside the card
                outside = await page.evaluate(
                    "() => [...document.querySelectorAll('.rule button')]"
                    ".filter(b => b.getBoundingClientRect().right > 360).length"
                )
                assert outside == 0
