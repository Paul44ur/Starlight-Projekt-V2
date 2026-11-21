"""The overlay's section on the Settings page (docs/plan-overlay.md 7, package W7): the
routes (at the PC only; a paired phone gets 403), saving the settings with their comments,
the "config" message to a running overlay (no restart), on and off, the test point, and the
section itself in a browser (what it shows and sends, German, a phone).

The overlay process is the fake child of tests/test_overlay_module.py. No Qt."""

from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.conftest import REPO
from tests.test_chat_page import static_site as static_site  # (the fixture)
from tests.test_overlay_module import Child
from tests.test_overlay_module import child as child  # (the fixture)
from tests.test_remote import LOCAL, client, home, pair, phone
from tests.test_settings_paper import open_page

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake child is a #! script")


def wait(check: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.02)


@pytest.fixture
def web(tmp_path: Path, child: Child) -> Iterator[tuple[Any, Child, Path]]:
    """A Starlight with remote access and the overlay module, its overlay on and running."""
    root = home(tmp_path / "home")
    shutil.copytree(
        REPO / "modules" / "overlay",
        root / "modules" / "overlay",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    config = root / "config" / "modules" / "overlay.yaml"
    config.write_text("enabled: true\npoint_seconds: 5\n")
    with client(root) as c:
        wait(lambda: c.get("/api/overlay", headers=LOCAL).json()["running"])
        yield c, child, config


def test_the_pc_sees_the_settings_and_the_screens(web: tuple[Any, Child, Path]) -> None:
    c, _, _ = web
    seen = c.get("/api/overlay", headers=LOCAL).json()
    assert seen["enabled"] and seen["installed"] and seen["running"]
    assert len(seen["screens"]) == 2  # (the "Orb on monitor" choices: 1 and 2)
    assert {k: seen[k] for k in ("orb", "orb_monitor", "accent", "reduce_motion")} == {
        "orb": "always",
        "orb_monitor": 1,
        "accent": "#4FC8FF",
        "reduce_motion": False,
    }


def test_a_change_is_saved_with_its_comments_and_sent_without_a_restart(
    web: tuple[Any, Child, Path],
) -> None:
    c, child, config = web
    done = c.post(
        "/api/overlay", json={"orb": "awake", "accent": "#3ddc97", "orb_monitor": 2}, headers=LOCAL
    )
    assert done.status_code == 200, done.text
    assert done.json()["orb"] == "awake" and done.json()["accent"] == "#3DDC97"
    wait(lambda: "config" in child.types())
    sent = [m for m in child.received() if m["type"] == "config"]
    assert sent == [{"type": "config", "orb": "awake", "accent": "#3ddc97", "orb_monitor": 2}]
    assert child.starts == 1  # no restart
    saved = yaml.safe_load(config.read_text())
    assert saved == {
        "enabled": True,
        "orb": "awake",
        "orb_monitor": 2,
        "accent": "#3ddc97",
        "reduce_motion": False,
        "point_seconds": 5,  # (kept: not on the page)
    }
    lines = [line for line in config.read_text().splitlines() if line and line[0] != "#"]
    assert all("  # " in line for line in lines), lines  # every setting keeps its comment
    # The same value again sends nothing more.
    c.post("/api/overlay", json={"orb": "awake"}, headers=LOCAL)
    c.post("/api/overlay", json={"reduce_motion": True}, headers=LOCAL)
    wait(lambda: len([m for m in child.received() if m["type"] == "config"]) == 2)
    assert [m for m in child.received() if m["type"] == "config"][1] == {
        "type": "config",
        "reduce_motion": True,
    }


def test_a_wrong_value_is_refused_and_nothing_is_saved(web: tuple[Any, Child, Path]) -> None:
    c, child, config = web
    before = config.read_text()
    for wrong in (
        {"accent": "red"},
        {"orb": "sometimes"},
        {"orb_monitor": 0},
        {"point_seconds": 1},  # not a setting of the page
        {"command": "calc.exe"},
    ):
        answer = c.post("/api/overlay", json=wrong, headers=LOCAL)
        assert answer.status_code == 400, wrong
    assert config.read_text() == before
    assert "config" not in child.types()


def test_off_stops_the_overlay_and_on_starts_it_again(web: tuple[Any, Child, Path]) -> None:
    c, child, config = web
    off = c.post("/api/overlay", json={"enabled": False}, headers=LOCAL).json()
    assert off["enabled"] is False and off["running"] is False
    assert "quit" in child.types()
    assert yaml.safe_load(config.read_text())["enabled"] is False
    c.post("/api/overlay", json={"accent": "#FFB547"}, headers=LOCAL)  # off: saved, not sent
    assert "config" not in child.types()
    c.post("/api/overlay", json={"enabled": True}, headers=LOCAL)
    wait(lambda: c.get("/api/overlay", headers=LOCAL).json()["running"])
    assert child.starts == 2
    assert child.received()[-1]["type"] == "hello"
    assert child.received()[-1]["accent"] == "#FFB547"  # the new start has the new colour


def test_the_test_point_points_at_the_box_for_three_seconds(web: tuple[Any, Child, Path]) -> None:
    c, child, _ = web
    shown = c.post("/api/overlay/test", json={"box": [12.4, 80, 140.6, 30]}, headers=LOCAL)
    assert shown.json() == {"shown": True}
    wait(lambda: "caption" in child.types())
    point = next(m for m in child.received() if m["type"] == "point")
    assert point["box"] == [12, 80, 141, 30] and point["seconds"] == 3
    assert point["label"] == "Einstellungen" and point["confidence"] == "sure"
    caption = next(m for m in child.received() if m["type"] == "caption")
    assert caption["text"] == "Hier ist es: Einstellungen"
    for bad in ([1, 2, 3], [0, 0, 0, 10], "here", [True, 0, 5, 5]):
        assert c.post("/api/overlay/test", json={"box": bad}, headers=LOCAL).status_code == 400
    c.post("/api/overlay", json={"enabled": False}, headers=LOCAL)
    assert c.post("/api/overlay/test", json={"box": [0, 0, 5, 5]}, headers=LOCAL).json() == {
        "shown": False
    }


def test_a_paired_phone_is_refused_everything(web: tuple[Any, Child, Path]) -> None:
    c, child, config = web
    token = pair(c)
    before = config.read_text()
    assert c.get("/api/overlay", headers=phone(token)).status_code == 403
    assert c.post("/api/overlay", json={"enabled": False}, headers=phone(token)).status_code == 403
    test = c.post("/api/overlay/test", json={"box": [0, 0, 5, 5]}, headers=phone(token))
    assert test.status_code == 403
    assert config.read_text() == before
    assert not {"config", "point", "quit"} & set(child.types())


# -- the section in a browser -----------------------------------------------------------------

OVERLAY = {
    "enabled": True,
    "installed": True,
    "running": True,
    "screens": [{"name": "1"}, {"name": "2"}],
    "hidden_from_screenshots": True,
    "restarts": 0,
    "gave_up": False,
    "last_error": "",
    "orb": "always",
    "orb_monitor": 1,
    "accent": "#4FC8FF",
    "reduce_motion": False,
}


def with_overlay(local: bool = True, **changes: Any) -> str:
    """Added to the Settings page's stub (tests/test_settings_paper.py): /api/overlay, which
    a phone is refused with 403."""
    data = {**copy.deepcopy(OVERLAY), **changes}
    return (
        f"(() => {{ const OVERLAY = {json.dumps(data)}; const before = window.fetch;\n"
        "window.fetch = async (url, init) => {\n"
        "  const u = new URL(String(url), location.href).pathname;\n"
        "  if (!u.startsWith('/api/overlay')) return before(url, init);\n"
        "  window.overlayAsked = (window.overlayAsked || 0) + 1;\n"
        f'  if (!{json.dumps(local)}) return new Response(\'{{"detail": "at the PC only"}}\',\n'
        "    {status: 403, headers: {'content-type': 'application/json'}});\n"
        "  let answer = OVERLAY;\n"
        "  if (init && init.method === 'POST') {\n"
        "    const body = JSON.parse(init.body); window.posted.push([u, body]);\n"
        "    if (u === '/api/overlay') Object.assign(OVERLAY, body); else answer = {shown: true};\n"
        "  }\n"
        "  return new Response(JSON.stringify(answer), {status: 200,\n"
        "    headers: {'content-type': 'application/json'}});\n"
        "}; })();"
    )


async def overlay_page(
    playwright: Any,
    site: str,
    *,
    width: int = 1280,
    local: bool = True,
    scheme: str = "light",
    german: bool = False,
    shot: str = "",
    **changes: Any,
) -> tuple[Any, Any]:
    browser, page = await open_page(playwright, site, width=width, local=local, scheme=scheme)
    await page.add_init_script(with_overlay(local, **changes))
    if german:
        await page.add_init_script("localStorage.setItem('starlight-lang', 'de')")
    await page.reload()
    await page.wait_for_selector("details.mod")
    if local:
        await page.wait_for_selector("#overlay-card:not([hidden])")
    folder = os.environ.get("OVERLAY_SHOTS")  # a folder for screenshots to look at
    if shot and folder:
        await page.locator("#overlay-card").screenshot(path=str(Path(folder) / f"{shot}.png"))
    return browser, page


async def test_the_section_shows_the_settings_and_sends_changes(static_site: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser, page = await overlay_page(p, static_site, shot="overlay-light")
        assert await page.is_checked("#overlay-on")
        assert await page.is_hidden("#overlay-missing")
        pressed = page.locator("#overlay-orb button[aria-pressed=true]")
        assert await pressed.get_attribute("data-value") == "always"
        options = await page.locator("#overlay-monitor option").all_inner_texts()
        assert options == ["1", "2"]
        swatches = page.locator("#overlay-accent button")
        assert await swatches.count() == 5
        assert await page.locator("#overlay-accent [aria-checked=true]").get_attribute(
            "aria-label"
        ) == ("Cyan")
        assert await page.inner_text("#overlay-state") == "Running on 2 monitors."

        await page.click("#overlay-orb button[data-value=awake]")
        await page.locator("#overlay-accent button").nth(2).click()
        await page.select_option("#overlay-monitor", "2")
        await page.click("#overlay-card label.practice:has(#overlay-motion) .switch")
        await page.click("#overlay-test")
        await page.wait_for_function("window.posted.length >= 5")
        posted = await page.evaluate("window.posted")
        assert posted[:4] == [
            ["/api/overlay", {"orb": "awake"}],
            ["/api/overlay", {"accent": "#FFB547"}],
            ["/api/overlay", {"orb_monitor": 2}],
            ["/api/overlay", {"reduce_motion": True}],
        ]
        url, sent = posted[4]
        assert url == "/api/overlay/test" and len(sent["box"]) == 4
        assert all(isinstance(v, int) for v in sent["box"]) and sent["box"][2] > 0
        await page.wait_for_selector("#overlay-accent [aria-checked=true][aria-label=Amber]")

        await page.click("#overlay-card label.practice:has(#overlay-on) .switch")
        await page.wait_for_function("window.posted.length >= 6")
        assert (await page.evaluate("window.posted"))[5] == ["/api/overlay", {"enabled": False}]
        await page.wait_for_selector("#overlay-settings", state="hidden")
        assert await page.inner_text("#overlay-state") == "The overlay is off."
        await browser.close()


async def test_not_installed_says_how_to_get_it_and_cannot_be_turned_on(
    static_site: str,
) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser, page = await overlay_page(
            p, static_site, enabled=False, installed=False, running=False, screens=[]
        )
        assert await page.is_visible("#overlay-missing")
        assert "Starlight.exe" in await page.inner_text("#overlay-missing")
        assert await page.is_disabled("#overlay-on")
        assert await page.is_hidden("#overlay-settings")
        await browser.close()


async def test_in_german_dark_and_on_a_phone(static_site: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser, page = await overlay_page(p, static_site, scheme="dark", shot="overlay-dark")
        await browser.close()
        browser, page = await overlay_page(p, static_site, german=True, shot="overlay-de")
        texts = await page.locator("#overlay-card").inner_text()
        for german in ("Farbe", "Weniger Bewegung", "Testpunkt zeigen", "Läuft auf 2"):
            assert german in texts
        for english in ("Show the overlay", "Reduce motion", "Running on", "Only when awake"):
            assert english not in texts
        amber = page.locator("#overlay-accent button").nth(2)
        assert await amber.get_attribute("aria-label") == "Bernstein"
        await browser.close()
        browser, page = await overlay_page(p, static_site, width=360, shot="overlay-phone-width")
        assert await page.evaluate("document.documentElement.scrollWidth") <= 360
        await browser.close()


async def test_a_paired_phone_does_not_see_the_section(static_site: str) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser, page = await overlay_page(p, static_site, width=360, local=False)
        await page.wait_for_function("document.body.classList.contains('remote')")
        await page.wait_for_function("window.overlayAsked >= 1")
        await page.evaluate("new Promise(r => requestAnimationFrame(() => setTimeout(r)))")
        assert await page.is_hidden("#overlay-card")
        await browser.close()
