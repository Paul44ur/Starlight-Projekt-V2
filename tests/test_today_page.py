"""Block R9, the page: /today shows the day as cards. The server's answers are faked (as in
tests/test_automation_runs_page.py), so every card, empty text and failure can be shown."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_chat_app_look import APP
from tests.test_chat_page import CHROMIUM, static_site  # noqa: F401  (a fixture)
from tests.test_german_everywhere import english_left


@pytest.fixture
async def playwright(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as started:
        yield started


EVIL = '<img src=x onerror="window.pwned = 1"> Standup'
CARDS = {
    "approvals": {"count": 3},
    "agenda": {
        "events": [
            {"time": "", "all_day": True, "title": "Mum's birthday", "location": ""},
            {"time": "09:15", "all_day": False, "title": EVIL, "location": ""},
            {"time": "10:00", "all_day": False, "title": "Dentist", "location": "Dr. Weiss"},
        ]
    },
    "todo": {
        "overdue": [{"text": "file taxes", "due": "2026-11-12", "time": ""}],
        "due": [{"text": "pay rent", "due": "2026-11-14", "time": ""}],
    },
    "reminders": {"reminders": [{"time": "07:30", "text": "water plants"}]},
    "weather": {
        "place": "Bremen", "conditions": "partly cloudy", "temperature": "12°C",
        "feels_like": "10°C", "low": "8°C", "high": "14°C", "rain_chance": "30%",
    },
}  # fmt: skip
EMPTY = {
    "agenda": {"events": []},
    "todo": {"overdue": [], "due": []},
    "reminders": {"reminders": []},
}
DATE = "2026-11-14"
ORDER = ["approvals", "agenda", "todo", "reminders", "weather"]

# What the page asks for is answered from window.TODAY (set before the page loads): a card's data,
# or {__fail: "reason"} for a 503. Every address asked is kept in window.todayCalls.
FAKE = """
const chatFetch = window.fetch;
window.todayCalls = []; window.sent = [];
window.WebSocket.prototype.send = function (m) { window.sent.push(String(m)); };
window.fetch = async (url, ...rest) => {
  const u = new URL(String(url), location.href);
  if (!u.pathname.startsWith('/api/today')) return chatFetch(url, ...rest);
  window.todayCalls.push(u.pathname + u.search);
  const type = {'content-type': 'application/json'};
  const card = u.pathname.slice('/api/today'.length).replace('/', '');
  if (window.TODAY.stall && window.TODAY.stall === card) await new Promise(() => {});
  const hit = card ? window.TODAY.cards[card] : {date: window.TODAY.date, cards: window.TODAY.list};
  if (hit === undefined) return new Response('{}', {status: 404, headers: type});
  if (hit.__fail) {
    return new Response(JSON.stringify({detail: hit.__fail}), {status: 503, headers: type});
  }
  return new Response(JSON.stringify(hit), {headers: type});
};
"""


@asynccontextmanager
async def today(
    playwright: Any, site: str, *, cards: dict[str, Any] | None = None, lang: str = "en",
    scheme: str = "dark", width: int = 1100, remote: bool = False, stall: str = "",
    wait: bool = True,
) -> AsyncIterator[Any]:  # fmt: skip
    import os

    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None:
        pytest.skip("no Chromium for Playwright")
    cards = CARDS if cards is None else cards
    state = {"date": DATE, "list": [c for c in ORDER if c in cards], "cards": cards, "stall": stall}
    browser = await playwright.chromium.launch(executable_path=executable)
    context = await browser.new_context(
        viewport={"width": width, "height": 900}, color_scheme=scheme,  # type: ignore[arg-type]
        locale="de-DE" if lang == "de" else "en-US",
    )  # fmt: skip
    page = await context.new_page()
    page.errors = []  # type: ignore[attr-defined]
    page.on("pageerror", lambda e: page.errors.append(str(e)))  # type: ignore[attr-defined]
    status = json.dumps({"local": not remote})
    await page.add_init_script(
        f"try {{ localStorage.setItem('starlight-lang', '{lang}'); }} catch (e) {{}}\n"
        f"window.TODAY = {json.dumps(state)};\n" + APP + FAKE
        + f"const appFetch = window.fetch; window.fetch = (url, ...rest) =>"
        f" String(url).includes('/api/status') ? Promise.resolve(new Response({json.dumps(status)},"
        " {headers: {'content-type': 'application/json'}})) : appFetch(url, ...rest);"
    )  # fmt: skip

    # "/?ask=…" is the chat: the static site has no "/", so it is answered with chat.html
    async def chat(route: Any) -> None:
        html = (
            Path(__file__).resolve().parent.parent / "starlight/web/static/chat.html"
        ).read_text(encoding="utf-8")
        await route.fulfill(status=200, content_type="text/html", body=html)

    await page.route(lambda u: urlparse(u).path == "/" and "ask=" in urlparse(u).query, chat)
    await page.goto(f"{site}/today.html")
    if wait:
        await page.wait_for_function(
            "() => !document.querySelector('#booting')"
            " && !document.querySelector('.card .skeleton')"
        )
    try:
        yield page
    finally:
        await browser.close()


async def texts(page: Any, selector: str) -> list[str]:
    return [t.strip() for t in await page.locator(selector).all_inner_texts()]


async def test_every_card_shows_its_data_in_order(playwright: Any, static_site: str) -> None:  # noqa: F811
    async with today(playwright, static_site) as page:
        assert await texts(page, ".card h2") == [
            "Waiting for your OK", "Agenda", "To-dos", "Reminders", "Weather",
        ]  # fmt: skip
        assert await page.locator("h1").inner_text() == "Today"
        assert await page.locator("#date").inner_text() == "Saturday, 14 November"
        assert "3 things wait for your OK" in await page.locator(".approvals").inner_text()
        link = page.locator(".approvals a")
        assert await link.get_attribute("href") == "/activity#approvals"
        rows = await texts(page, ".agenda .item")
        assert [r.replace("\n", " | ") for r in rows] == [
            "all day | Mum's birthday", f"09:15 | {EVIL}", "10:00 | Dentist | Dr. Weiss",
        ]  # fmt: skip
        assert await texts(page, ".agenda .item .num") == ["all day", "09:15", "10:00"]
        late = page.locator(".todo .late")
        assert await late.inner_text() == "overdue since 12 Nov"
        colour = await late.evaluate("n => getComputedStyle(n).color")
        bad = await page.evaluate(
            "() => { const s = document.createElement('i'); s.style.color = 'var(--bad)';"
            " document.body.append(s); return getComputedStyle(s).color; }"
        )
        assert colour == bad  # (--bad text colour)
        words = await texts(page, ".todo .item .words")
        assert words[0].startswith("file taxes") and words[1] == "pay rent"  # overdue first
        assert await page.locator(".todo a").get_attribute("href") == "/notes?view=tasks"
        assert await texts(page, ".reminders .item") == ["07:30\nwater plants"]
        weather = await page.locator(".weather").inner_text()
        assert "Bremen" in weather and "12°C" in weather and "feels like 10°C" in weather
        assert "today 8°C to 14°C, 30% chance of rain" in weather
        assert not page.errors  # type: ignore[attr-defined]


async def test_a_title_with_html_stays_text_and_is_not_translated(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    async with today(playwright, static_site) as page:
        assert await page.locator(".agenda img").count() == 0
        assert await page.evaluate("() => window.pwned") is None
        for selector in (".agenda .words span", ".todo .words span", ".reminders .words span",
                         ".weather .where"):  # fmt: skip
            assert await page.locator(selector).first.get_attribute("translate") == "no"


async def test_empty_cards_say_so(playwright: Any, static_site: str) -> None:  # noqa: F811
    async with today(playwright, static_site, cards=EMPTY) as page:
        assert await texts(page, "#cards .card p.dim") == [
            "Nothing planned today.", "Nothing due today.", "No reminders today.",
        ]  # fmt: skip
        assert await page.locator(".approvals").count() == 0
        assert await page.locator("#none").is_hidden()


async def test_a_module_that_is_off_has_no_card(playwright: Any, static_site: str) -> None:  # noqa: F811
    cards = {k: v for k, v in CARDS.items() if k in ("todo", "reminders")}
    async with today(playwright, static_site, cards=cards) as page:
        assert await texts(page, ".card h2") == ["To-dos", "Reminders"]
        calls = await page.evaluate("() => window.todayCalls")
        assert calls == ["/api/today", "/api/today/todo", "/api/today/reminders"]  # (nothing else)


async def test_with_no_cards_one_calm_line_and_a_link_to_settings(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    async with today(playwright, static_site, cards={}) as page:
        none = page.locator("#none")
        assert await none.is_visible()
        assert (
            "Switch on the calendar, to-do, reminders or weather module" in await none.inner_text()
        )
        assert await none.locator("a").get_attribute("href") == "/settings"
        assert await page.locator("#cards .card").count() == 0


async def test_a_failed_card_says_so_and_the_others_show(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    cards = {**CARDS, "weather": {"__fail": "<b>the service</b> did not answer"}}
    async with today(playwright, static_site, cards=cards) as page:
        weather = page.locator(".weather")
        assert await weather.locator("p").first.inner_text() == "Could not load"
        assert await weather.locator(".why").inner_text() == "<b>the service</b> did not answer"
        assert await weather.locator("b").count() == 0  # (text only)
        assert await page.locator(".agenda .item").count() == 3  # the others still show
        assert await page.locator(".todo .item").count() == 2


async def test_the_stopped_card_says_that_starlight_is_stopped(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    cards = {**CARDS, "weather": {"__fail": "Starlight is stopped"}}
    async with today(playwright, static_site, cards=cards) as page:
        assert await texts(page, ".weather p") == ["Starlight is stopped"]


async def test_a_slow_card_does_not_hold_back_the_others(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    async with today(playwright, static_site, stall="weather", wait=False) as page:
        await page.wait_for_selector(".agenda .item")
        assert await page.locator(".weather .skeleton").count() == 1  # (still loading)
        assert await page.locator(".todo .item").count() == 2


async def test_exactly_one_gold_button_and_it_asks_the_chat_without_sending(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    async with today(playwright, static_site) as page:
        gold = await page.evaluate(
            "() => { const s = document.createElement('i'); s.style.background = 'var(--primary)';"
            " document.body.append(s); const g = getComputedStyle(s).backgroundColor;"
            " return [...document.querySelectorAll('button')].filter("
            "b => getComputedStyle(b).backgroundColor === g).map(b => b.id); }"
        )
        assert gold == ["ask"]
        assert await page.locator("button.primary").count() == 1
        assert await page.locator("#ask").inner_text() == "Ask about my day"
        await page.locator("#ask").click()
        await page.wait_for_selector("#input")
        await page.wait_for_function("() => document.getElementById('input').value !== ''")
        assert await page.locator("#input").input_value() == "How does my day look?"
        assert await page.evaluate("() => window.sent") == []  # (nothing sent)


async def test_refresh_asks_every_card_again_with_fresh(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    async with today(playwright, static_site) as page:
        button = page.locator("#refresh")
        assert await button.get_attribute("aria-label") == "Refresh"
        assert await button.get_attribute("title") == "Refresh"
        assert await button.locator("svg.ico").count() == 1
        assert await page.evaluate("() => window.todayCalls.filter(c => c.includes('fresh'))") == []
        await page.evaluate("() => { window.todayCalls = []; }")
        await button.click()
        await page.wait_for_function("() => window.todayCalls.length >= 6")
        calls = await page.evaluate("() => window.todayCalls")
        assert calls[0] == "/api/today"
        assert sorted(calls[1:]) == sorted(f"/api/today/{c}?fresh=1" for c in ORDER)


async def test_the_german_page_has_no_english_left_and_asks_in_german(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    async with today(playwright, static_site, lang="de") as page:
        assert await page.locator("#date").inner_text() == "Samstag, 14. November"
        shown = await page.locator("body").inner_text()
        assert "3 Dinge warten auf dein OK" in shown and "überfällig seit 12. Nov." in shown
        assert "heute 8°C bis 14°C, 30% Regenwahrscheinlichkeit" in shown
        assert "teils bewölkt" in shown.lower() and "ganztägig" in shown
        assert await texts(page, ".card h2") == [
            "Wartet auf dein OK", "Termine", "Aufgaben", "Erinnerungen", "Wetter",
        ]  # fmt: skip
        assert await page.locator("#ask").inner_text() == "Frag zu meinem Tag"
        # what is the user's stays as it is
        assert "Mum's birthday" in shown and "Dentist" in shown and "Bremen" in shown
        left = await english_left(page)
        assert left == [], left
        await page.locator("#ask").click()
        await page.wait_for_function("() => document.getElementById('input')?.value")
        assert await page.locator("#input").input_value() == "Wie sieht mein Tag aus?"


async def test_german_failed_and_empty_cards_have_no_english_left(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    cards = {**EMPTY, "weather": {"__fail": "Starlight is stopped"}}
    async with today(playwright, static_site, cards=cards, lang="de") as page:
        left = await english_left(page)
        assert left == [], left
    async with today(playwright, static_site, cards={}, lang="de") as page:
        left = await english_left(page)
        assert left == [], left


async def test_night_and_day_use_the_page_and_card_tokens(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    probe = (
        "() => { const v = n => { const s = document.createElement('i');"
        " s.style.color = `var(${n})`;"
        " document.body.append(s); const c = getComputedStyle(s).color; s.remove(); return c; };"
        " return [getComputedStyle(document.body).backgroundColor, v('--bg'),"
        " getComputedStyle(document.querySelector('.card')).backgroundColor, v('--panel')]; }"
    )
    for scheme in ("dark", "light"):
        async with today(playwright, static_site, scheme=scheme) as page:
            body, bg, card, panel = await page.evaluate(probe)
            assert body == bg and card == panel, scheme
            assert body != card  # (the card is on a different surface)


def luminance(rgb: tuple[int, ...]) -> float:
    def lin(c: int) -> float:
        c2 = c / 255
        return c2 / 12.92 if c2 <= 0.03928 else ((c2 + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


async def test_text_contrast_is_at_least_4_5_to_1_in_both_looks(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    probe = """() => {
      const out = [];
      const surface = (n) => { for (let e = n; e; e = e.parentElement) {
        const c = getComputedStyle(e).backgroundColor;
        if (!/rgba\\(.*, 0\\)|transparent/.test(c)) return c; }
        return 'rgb(255,255,255)'; };
      for (const n of document.querySelectorAll('.card *, .date, .intro button')) {
        if (![...n.childNodes].some(t => t.nodeType === 3 && t.textContent.trim())) continue;
        out.push([n.className || n.tagName, getComputedStyle(n).color, surface(n)]);
      }
      return out; }"""
    for scheme in ("dark", "light"):
        async with today(playwright, static_site, scheme=scheme) as page:
            found = await page.evaluate(probe)
            assert len(found) > 10
            for name, fore, back in found:
                f = tuple(int(x) for x in re.findall(r"\d+", fore)[:3])
                b = tuple(int(x) for x in re.findall(r"\d+", back)[:3])
                assert ratio(f, b) >= 4.5, (scheme, name, fore, back, ratio(f, b))


async def test_nothing_sticks_out_at_360_px_in_german_too(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    for lang in ("en", "de"):
        async with today(playwright, static_site, width=360, lang=lang) as page:
            assert await page.evaluate("() => document.documentElement.scrollWidth") <= 360, lang
            assert await page.locator("#cards .card").count() == 5


async def test_a_phone_does_not_show_the_link_to_the_tasks(
    playwright: Any,
    static_site: str,  # noqa: F811
) -> None:
    async with today(playwright, static_site, remote=True) as page:
        await page.wait_for_function("() => document.body.classList.contains('remote')")
        assert await page.locator(".todo a").is_hidden()
    async with today(playwright, static_site) as page:
        assert await page.locator(".todo a").is_visible()


async def test_the_nav_links_the_other_pages(playwright: Any, static_site: str) -> None:  # noqa: F811
    async with today(playwright, static_site) as page:
        links = await page.locator("header.top nav a").evaluate_all("a => a.map(x => x.pathname)")
        assert links == ["/", "/notes", "/memory", "/activity", "/automations", "/settings"]
        assert parse_qs("") == {}
