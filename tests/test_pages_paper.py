"""Block P5 of the UI 3 plan (docs/plan-ui-3.md): Today, Memory, Activity, Automations, Kill and
Pair in the Paper look. For every page: no element rounder than 2 px (circles excepted), no
shadow at rest, headings in the serif, no colour written out in the page's own style, at most
one filled main button (the kill switch's stop control excepted), nothing sideways at 360 px,
and text contrast of 4.5:1 in both looks. The kill switch's Kill and "Stopped" looks stay apart."""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO
from tests.test_chat_page import CHROMIUM, static_site  # noqa: F401  (a fixture)
from tests.test_pages_look import DATA

STATIC = REPO / "starlight" / "web" / "static"
PAGES = ("memory", "activity", "automations", "kill", "pair", "today")
COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(")


def style_blocks(name: str) -> str:
    html = (STATIC / f"{name}.html").read_text(encoding="utf-8")
    css = "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)  # (a comment may name a colour)


async def open_at(
    playwright: Any,
    site: str,
    name: str,
    *,
    width: int = 1200,
    scheme: str = "dark",
    kill_engaged: bool = False,
) -> tuple[Any, Any]:
    executable = os.environ.get("STARLIGHT_CHROMIUM") or (
        CHROMIUM if Path(CHROMIUM).exists() else None
    )
    if executable is None and not os.environ.get("CI"):
        pytest.skip("no Chromium for Playwright")
    data = copy.deepcopy(DATA)
    data["/api/status"]["kill"]["engaged"] = kill_engaged
    data["/api/memory"]["memories"][0] |= {"date": "2026-11-10", "expires": "2026-12-01"}
    data["/api/today"] = {
        "date": "2026-11-15",
        "cards": ["approvals", "agenda", "todo", "reminders", "weather"],
    }
    data["/api/today/approvals"] = {"count": 1}
    data["/api/today/agenda"] = {
        "events": [
            {"time": "10:00", "title": "Team meeting", "location": "Room 4", "all_day": False}
        ]
    }
    data["/api/today/todo"] = {
        "overdue": [{"text": "Call the bank", "due": "2026-11-12"}],
        "due": [{"text": "Water the plants", "time": "18:00"}],
    }
    data["/api/today/reminders"] = {"reminders": [{"time": "14:30", "text": "Dentist"}]}
    data["/api/today/weather"] = {
        "place": "Kiel", "conditions": "sunny", "temperature": "14 C", "feels_like": "12 C",
        "high": "16 C", "low": "9 C", "rain_chance": "10 %",
    }  # fmt: skip
    data["/api/activity"] = {
        "entries": [
            {"time": "2026-11-12T10:00:00", "kind": "action", "text": "mail.send ok"},
            {"time": "2026-11-12T10:05:00", "kind": "denied", "text": "files.delete refused"},
            {"time": "2026-11-12T10:06:00", "kind": "kill switch", "text": "stopped"},
        ]
    }
    fake = (
        f"const DATA = {json.dumps(data)};\n"
        "window.fetch = async (url) => { const u = new URL(String(url), location.href).pathname;"
        " const hit = Object.keys(DATA).find(k => u === k);"
        " return new Response(JSON.stringify(hit ? DATA[hit] : {}), {status: hit ? 200 : 404,"
        " headers: {'content-type': 'application/json'}}); };"
    )
    browser = await playwright.chromium.launch(executable_path=executable)
    page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    await page.add_init_script(fake)
    await page.goto(f"{site}/{name}.html")
    await page.wait_for_load_state("load")
    return browser, page


MAIN = {"today": ["ask"], "automations": ["save"], "pair": ["go"], "memory": [],
        "activity": [], "kill": []}  # fmt: skip
FILLED = """() => {
  const probe = document.createElement('div'); probe.style.background = 'var(--primary)';
  document.body.appendChild(probe);
  const primary = getComputedStyle(probe).backgroundColor; probe.remove();
  return [...document.querySelectorAll('button')].filter(b => {
    const r = b.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(b).backgroundColor === primary;
  }).map(b => b.id || b.textContent.trim());
}"""


async def test_a_view_has_only_its_one_main_button_filled(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for name in PAGES:
            for scheme in ("dark", "light"):
                browser, page = await open_at(playwright, static_site, name, scheme=scheme)
                try:
                    await page.wait_for_timeout(300)  # (the lists draw their rows)
                    # exactly the page's main action (Kill's stop control is red, not primary)
                    assert await page.evaluate(FILLED) == MAIN[name], (name, scheme)
                finally:
                    await browser.close()


RADIUS = """() => {
  const bad = [];
  for (const n of document.querySelectorAll('body *')) {
    if (n instanceof SVGElement && n.tagName !== 'svg') continue;
    const r = n.getBoundingClientRect();
    if (!r.width || !r.height) continue;
    const s = getComputedStyle(n);
    for (const k of ['borderTopLeftRadius', 'borderTopRightRadius', 'borderBottomLeftRadius',
                     'borderBottomRightRadius']) {
      const v = s[k];
      if (v === '50%') continue;  // a circle
      if (parseFloat(v) > 2) bad.push(n.tagName + '.' + n.className + ' ' + k + ' ' + v);
    }
    if (s.boxShadow !== 'none') bad.push(n.tagName + '.' + n.className + ' shadow ' + s.boxShadow);
  }
  return bad;
}"""

HEADINGS = """() => [...document.querySelectorAll('h1, h2')].filter(h => h.offsetWidth > 0)
  .map(h => [h.textContent.trim(), getComputedStyle(h).fontFamily])"""


async def test_no_round_corners_no_shadow_and_serif_headings(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for name in PAGES:
            browser, page = await open_at(playwright, static_site, name)
            try:
                await page.wait_for_timeout(300)
                assert await page.evaluate(RADIUS) == [], name
                headings = await page.evaluate(HEADINGS)
                assert headings, name
                for text, font in headings:
                    assert "Charter" in font, (name, text, font)
            finally:
                await browser.close()


TEXT_CONTRAST = """() => {
  const rgb = (c) => c.match(/[\\d.]+/g).map(Number).slice(0, 3).map(v => v / 255);
  const lin = (v) => v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  const lum = (c) => { const [r, g, b] = rgb(c).map(lin);
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const back = (n) => { for (; n; n = n.parentElement) {
    const c = getComputedStyle(n).backgroundColor;
    if (!/, 0\\)$/.test(c) && c !== 'transparent') return c; } return 'rgb(255, 255, 255)'; };
  const low = [];
  for (const n of document.querySelectorAll('body *')) {
    if (![...n.childNodes].some(c => c.nodeType === 3 && c.textContent.trim())) continue;
    const r = n.getBoundingClientRect();
    if (!r.width || !r.height || n.disabled) continue;
    const a = lum(getComputedStyle(n).color), b = lum(back(n));
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    if (ratio < 4.5) low.push(n.tagName + '.' + n.className + ' ' + ratio.toFixed(2));
  }
  return low;
}"""


async def test_numbers_are_in_mono(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    async with async_api.async_playwright() as playwright:
        for name, selector in (
            ("memory", "#count"),
            ("memory", ".mem .num"),
            ("activity", ".entry .when"),
            ("activity", "#count"),
        ):
            browser, page = await open_at(playwright, static_site, name)
            try:
                await page.wait_for_selector(selector)
                await page.wait_for_function(
                    "s => document.querySelector(s).textContent.trim() !== ''", arg=selector
                )
                font = await page.locator(selector).first.evaluate(
                    "n => getComputedStyle(n).fontFamily"
                )
                assert "mono" in font.lower() or "consolas" in font.lower(), (name, selector)
            finally:
                await browser.close()


async def test_the_kill_button_and_the_stopped_state_look_apart(
    static_site: str,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async_api = pytest.importorskip("playwright.async_api")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    look = "b => { const s = getComputedStyle(b); return [s.backgroundColor, s.color]; }"
    async with async_api.async_playwright() as playwright:
        seen = {}
        for scheme in ("dark", "light"):
            browser, page = await open_at(playwright, static_site, "kill", scheme=scheme)
            try:
                seen[scheme] = await page.locator("#stop").evaluate(look)
            finally:
                await browser.close()
        # the red does not change with the look
        assert seen["dark"] == seen["light"] == ["rgb(163, 54, 30)", "rgb(255, 255, 255)"]
        assert "fixed red identity" in (STATIC / "kill.html").read_text(encoding="utf-8")
        browser, page = await open_at(playwright, static_site, "kill", kill_engaged=True)
        try:
            await page.wait_for_selector("#stop.done")
            assert await page.locator("#stop").evaluate(look) != seen["dark"]
        finally:
            await browser.close()
