"""Everyday and PC modules: expenses and receipts, birthdays, habits, recipes, trips, podcasts,
the log watcher, pollen and air quality, folder sizes and startup programs, window layouts
and winget, the clipboard history, and code review."""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths, ToolError
from tests.conftest import ScriptedProvider

TODAY = dt.date.today()


def instance(k: Kernel, name: str) -> Any:
    module = k.modules.records[name].instance
    assert module is not None, k.modules.records[name].error
    return module


async def call(k: Kernel, tool: str, **args: Any) -> Any:
    result = await k.dispatcher.call(tool, args)
    assert result.ok, result.error
    return result.output


async def fails(k: Kernel, tool: str, **args: Any) -> str:
    result = await k.dispatcher.call(tool, args)
    assert not result.ok
    return str(result.error)


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    for name in ("expenses", "birthdays", "habits", "recipes", "trips", "podcasts", "logwatch"):
        use_module(name)
    with paths.config_file.open("a") as config:
        config.write("memory:\n  enabled: false\nskills:\n  enabled: false\n")
    return paths


# -- expenses -------------------------------------------------------------------------------------


def test_amounts_as_people_write_them() -> None:
    from modules.expenses.module import parse_amount

    assert parse_amount("12,50 €") == 12.5 and parse_amount("1.234,56") == 1234.56
    assert parse_amount("$1,234.56") == 1234.56 and parse_amount(9) == 9.0
    with pytest.raises(Exception, match="not an amount"):
        parse_amount("lots")


async def test_expenses_a_month_and_its_budget(project: Paths, documents: Path) -> None:
    (project.module_configs / "expenses.yaml").write_text("monthly_budget: 100\n")
    async with Kernel(project) as k:
        await call(k, "expenses.add", amount="23,40", what="Groceries at the market")
        await call(k, "expenses.add", amount="12", what="Pizza", category="eating out")
        added = await call(k, "expenses.add", amount="5", what="Bus", category="transport",
                           day="yesterday")  # fmt: skip
        month = await call(k, "expenses.month")
        assert month["total"] == 40.4 and month["left"] == 59.6
        assert month["by_category"] == {"groceries": 23.4, "eating out": 12.0, "transport": 5.0}
        await call(k, "expenses.remove", id=added["added"]["id"])
        exported = await call(k, "expenses.export_csv")
        csv = Path(exported["saved"]).read_text(encoding="utf-8-sig")
        assert csv.splitlines()[0] == "day;amount;currency;what;category" and "23.40" in csv
        assert "not an amount" in await fails(k, "expenses.add", amount="x", what="y")


async def test_a_receipt_is_read_by_the_model(project: Paths, documents: Path) -> None:
    receipt = documents / "Starlight" / "receipt.txt"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text("BIO MARKT\n2026-10-28\nTOTAL 31,20 EUR")
    provider = ScriptedProvider(
        'Here: {"shop": "Bio Markt", "day": "2026-10-28", "total": "31,20", '
        '"category": "groceries"}'
    )
    async with Kernel(project, provider=provider) as k:
        result = await call(k, "expenses.read_receipt", path=str(receipt))
    assert result["added"]["amount"] == 31.2 and result["added"]["day"] == "2026-10-28"
    assert (
        result["added"]["category"] == "groceries" and "TOTAL 31,20" in provider.calls[0][0].content
    )


# -- birthdays ------------------------------------------------------------------------------------


async def test_birthdays_ages_and_reminders(project: Paths) -> None:
    soon = TODAY + dt.timedelta(days=3)
    async with Kernel(project) as k:
        module = instance(k, "birthdays")
        told: list[str] = []

        async def deliver(text: str, title: str = "", **_: Any) -> None:
            told.append(text)

        module.ctx.deliver = deliver
        await call(k, "birthdays.add", name="Anna", birthday=f"{TODAY.day}.{TODAY.month}.1990")
        await call(k, "birthdays.add", name="Ben", birthday=f"{soon.day}.{soon.month}.")
        coming = (await call(k, "birthdays.upcoming", days=7))["birthdays"]
        assert [b["name"] for b in coming] == ["Anna", "Ben"]
        assert coming[0]["in_days"] == 0 and coming[0]["turns"] == TODAY.year - 1990
        assert "turns" not in coming[1]
        module.config.remind_from_hour = 0
        await module.check()
        await module.check()  # each told once
        assert len(told) == 2 and "Today is Anna's birthday" in told[0]
        assert "Ben's birthday is in 3 days" in told[1]
        assert "not a day of the year" in await fails(
            k, "birthdays.add", name="X", birthday="31.2."
        )


def test_a_29_february_birthday_comes_on_the_28th_otherwise() -> None:
    from modules.birthdays.module import next_birthday, parse_birthday

    assert parse_birthday("29.02.2000") == (2, 29, 2000)
    assert next_birthday(2, 29, dt.date(2027, 1, 1)) == dt.date(2027, 2, 28)
    assert next_birthday(2, 29, dt.date(2028, 1, 1)) == dt.date(2028, 2, 29)


# -- habits --------------------------------------------------------------------------------------


def test_streaks() -> None:
    from modules.habits.module import streak

    friday = dt.date(2026, 10, 30)
    days = {(friday - dt.timedelta(days=n)).isoformat() for n in (1, 2, 3, 5)}
    assert streak(days, "day", friday) == 3  # today not done yet: still running
    assert streak(days | {friday.isoformat()}, "day", friday) == 4
    weeks = {"2026-10-27", "2026-10-20", "2026-10-06"}
    assert streak(weeks, "week", friday) == 2


async def test_habits_are_ticked_off(project: Paths) -> None:
    async with Kernel(project) as k:
        await call(k, "habits.add", habit="Walk 30 minutes")
        await call(k, "habits.done", habit="walk", day="yesterday")
        done = await call(k, "habits.done", habit="walk")
        assert done["streak"] == 2
        status = (await call(k, "habits.status"))["habits"][0]
        assert status["done_now"] and status["last_14_days"].endswith("xx")
        assert "no habit" in await fails(k, "habits.done", habit="swim")


# -- recipes --------------------------------------------------------------------------------------


def test_scaling_amounts() -> None:
    from modules.recipes.module import scale, show_amount

    half = Fraction(1, 2)
    assert scale("250 g flour", half) == "125 g flour"
    assert scale("1 1/2 cups milk", Fraction(2)) == "3 cups milk"
    assert scale("3 eggs", half) == "1.5 eggs"
    assert scale("½ l milk", Fraction(3)) == "1.5 l milk"
    assert scale("1,5 kg potatoes", half) == "0.75 kg potatoes"
    assert scale("salt", half) == "salt"
    assert show_amount(Fraction(1, 3)) == "1/3" and show_amount(Fraction(4, 3)) == "1 1/3"


async def test_recipes_and_a_shopping_list(project: Paths, documents: Path) -> None:
    async with Kernel(project) as k:
        await call(k, "recipes.save", title="Pancakes", serves=4,
                   ingredients=["250 g flour", "1/2 l milk", "2 eggs", "salt"],
                   steps=["Mix", "Fry"])  # fmt: skip
        await call(k, "recipes.save", title="Crepes", serves=2,
                   ingredients=["100 g flour", "2 eggs"], steps=["Mix"])  # fmt: skip
        assert (await call(k, "recipes.find", text="milk"))["recipes"] == ["Pancakes"]
        shown = await call(k, "recipes.show", title="pancake", serves=2)
        assert shown["ingredients"] == ["125 g flour", "0.25 l milk", "1 eggs", "salt"]
        assert shown["steps"] == ["Mix", "Fry"] and shown["scaled_from"] == 4
        listed = await call(k, "recipes.shopping_list", titles=["Pancakes", "Crepes"])
        assert "350 g flour" in listed["items"] and "4 eggs" in listed["items"]
        assert "salt" in listed["items"]
    assert (documents / "Starlight" / "Recipes" / "Pancakes.md").is_file()


# -- trips --------------------------------------------------------------------------------------


def test_packing_follows_the_weather() -> None:
    from modules.trips.module import packing

    cold = packing(3, [{"high": "4°C", "low": "-2°C", "rain_chance": "80%"}])
    assert "warm jacket" in cold and "umbrella or rain jacket" in cold and "3 x socks" in cold
    hot = packing(10, [{"high": "31°C", "low": "20°C", "rain_chance": "0%"}])
    assert "sun cream" in hot and "7 x socks" in hot and "laundry bag" in hot


async def test_trips_and_their_countdown(project: Paths) -> None:
    start = (TODAY + dt.timedelta(days=40)).isoformat()
    end = (TODAY + dt.timedelta(days=46)).isoformat()
    async with Kernel(project) as k:
        await call(k, "trips.add", destination="Lisbon", start=start, end=end)
        listed = (await call(k, "trips.list_trips"))["trips"]
        assert listed[0]["when"] == "in 40 days" and listed[0]["days"] == 7
        packing = await call(k, "trips.packing_list", destination="lisbon")
        assert "more than two weeks away" in packing["weather"] and packing["pack"]
        assert "ends before it starts" in await fails(k, "trips.add", destination="X",
                                                      start=end, end=start)  # fmt: skip


# -- podcasts --------------------------------------------------------------------------------------

FEED = """<?xml version="1.0"?><rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel><title>Night Sky</title>
<item><title>Jupiter</title><pubDate>Fri, 30 Oct 2026</pubDate>
<itunes:duration>31:00</itunes:duration>
<enclosure url="https://example.org/ep2.mp3" type="audio/mpeg"/></item>
<item><title>Saturn</title><enclosure url="https://example.org/ep1.mp3"/></item>
<item><title>No audio</title></item>
</channel></rss>"""


async def test_podcasts(project: Paths) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=FEED, headers={"content-type": "application/rss+xml"})

    async with Kernel(project, http_transport=httpx.MockTransport(handler)) as k:
        assert (await call(k, "podcasts.subscribe", feed_url="https://example.org/feed"))[
            "subscribed"
        ] == "Night Sky"
        episodes = (await call(k, "podcasts.episodes"))["podcasts"][0]["episodes"]
        assert [e["title"] for e in episodes] == ["Jupiter", "Saturn"]
        assert episodes[0]["length"] == "31:00"
        played = await call(k, "podcasts.play", podcast="night")
        assert played["audio"] == "https://example.org/ep2.mp3"
        assert "has 2 episodes" in await fails(k, "podcasts.play", podcast="night", episode=5)


# -- the log watcher -----------------------------------------------------------------------------


async def test_new_error_lines_are_reported_once(project: Paths, tmp_path: Path) -> None:
    log = tmp_path / "server.log"
    log.write_text("old ERROR before watching\n")
    async with Kernel(project) as k:
        watcher = instance(k, "logwatch")
        told: list[str] = []

        async def deliver(text: str, title: str = "", **_: Any) -> None:
            told.append(text)

        watcher.ctx.deliver = deliver
        watcher.watch_log(str(log), name="server")
        with log.open("a") as out:
            out.write("all fine\nERROR: disk full\nWarning: slow\nfatal crash\npartial ERR")
        assert await watcher.check() == {"server": 2}
        assert "disk full" in told[0] and "fatal crash" in told[0] and "old ERROR" not in told[0]
        assert await watcher.check() == {}  # nothing new
        with log.open("a") as out:
            out.write("OR line\n")  # the half line is finished now
        assert await watcher.check() == {"server": 1}
        log.write_text("Exception after rotation\n")  # smaller: rotated
        assert await watcher.check() == {"server": 1}
        with pytest.raises(ToolError, match="not valid"):
            watcher.watch_log(str(log), "(")


# -- pollen and air quality -----------------------------------------------------------------------


async def test_pollen_and_air_quality(paths: Paths, use_module: Callable[[str], Path]) -> None:
    use_module("weather")
    day = TODAY.isoformat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host.startswith("geocoding"):
            kiel = {"name": "Kiel", "country": "Germany", "latitude": 54.3, "longitude": 10.1}
            return httpx.Response(200, json={"results": [kiel]})
        return httpx.Response(200, json={"hourly": {
            "time": [f"{day}T10:00", f"{day}T14:00"],
            "birch_pollen": [12.0, 80.0], "grass_pollen": [0.2, 0.5], "alder_pollen": [None, None],
            "mugwort_pollen": [0, 0], "olive_pollen": [0, 0], "ragweed_pollen": [0, 0],
            "european_aqi": [18, 33], "uv_index": [2.5, 4.25],
        }})  # fmt: skip

    async with Kernel(paths, http_transport=httpx.MockTransport(handler)) as k:
        air = await call(k, "weather.air", place="Kiel")
    today = air["days"][0]
    assert today["pollen"] == {"birch": "high (80)"} and today["air"] == "fair (33)"
    assert today["uv_max"] == 4.2 or today["uv_max"] == 4.3


# -- the PC ---------------------------------------------------------------------------------------


def test_folder_sizes(tmp_path: Path) -> None:
    from modules.system.module import folder_sizes

    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "a.mp4").write_bytes(b"x" * 5000)
    (tmp_path / "docs" / "deep").mkdir(parents=True)
    (tmp_path / "docs" / "deep" / "b.txt").write_bytes(b"x" * 300)
    (tmp_path / "loose.bin").write_bytes(b"x" * 100)
    folders, files, complete = folder_sizes(tmp_path)
    assert folders == {"docs": 300, "videos": 5000} and complete
    assert [Path(f).name for f, _ in files] == ["a.mp4", "b.txt", "loose.bin"]


async def test_startup_programs_on_linux(
    paths: Paths, use_module: Callable[[str], Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_module("system")
    autostart = tmp_path / "home" / ".config" / "autostart"
    autostart.mkdir(parents=True)
    (autostart / "syncthing.desktop").write_text("[Desktop Entry]\n")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    async with Kernel(paths) as k:
        system = instance(k, "system")
        system._platform = lambda: "linux"
        listed = (await call(k, "system.startup_programs"))["starting"]
        assert listed == [{"name": "syncthing", "command": str(autostart / "syncthing.desktop"),
                           "where": "Startup folder"}]  # fmt: skip
        system.startup_off("syncthing")  # (the tool asks first; called directly here)
        assert not (autostart / "syncthing.desktop").exists()
        assert (await call(k, "system.startup_programs"))["turned_off_here"] == ["syncthing"]
        await call(k, "system.startup_on", name="syncthing")
        assert (autostart / "syncthing.desktop").exists()


WINGET = """\r   \r  - \r
Name                Id                  Version   Available Source
--------------------------------------------------------------------
Mozilla Firefox     Mozilla.Firefox     130.0     131.0.2   winget
VLC media player    VideoLAN.VLC        3.0.20    3.0.21    winget
2 upgrades available.
"""


def test_winget_tables() -> None:
    from modules.apps.module import winget_table

    rows = winget_table(WINGET)
    assert rows == [
        {"name": "Mozilla Firefox", "id": "Mozilla.Firefox", "version": "130.0",
         "available": "131.0.2", "source": "winget"},
        {"name": "VLC media player", "id": "VideoLAN.VLC", "version": "3.0.20",
         "available": "3.0.21", "source": "winget"},
    ]  # fmt: skip
    assert winget_table("No installed package found.") == []


async def test_window_layouts(paths: Paths, use_module: Callable[[str], Path]) -> None:
    from tests.test_local_programs import FakeDesktop

    use_module("apps")

    class Desktop(FakeDesktop):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            self.places: dict[int, tuple[int, int, int, int]] = {
                1: (0, 0, 800, 600),
                2: (800, 0, 800, 600),
            }

        def place(self, window: Any) -> tuple[int, int, int, int]:
            if window.id not in self.places:
                from starlight_modules.apps.backends import (
                    AppsUnavailable,  # type: ignore[import-not-found]
                )

                raise AppsUnavailable("gone")
            return self.places[window.id]

        def move(self, window: Any, place: tuple[int, int, int, int]) -> None:
            self.places[window.id] = place

    async with Kernel(paths) as k:
        desktop = Desktop()
        instance(k, "apps")._backend = desktop
        assert (await call(k, "apps.save_layout", name="Work"))["windows"] == 2
        desktop.places = {1: (5, 5, 100, 100), 2: (5, 5, 100, 100), 3: (1, 1, 1, 1)}
        restored = await call(k, "apps.restore_layout", name="work")
        assert desktop.places[1] == (0, 0, 800, 600) and desktop.places[2] == (800, 0, 800, 600)
        assert sorted(restored["moved"]) == ["Budget.xlsx - Excel", "Inbox - Outlook"]
        assert (await call(k, "apps.layouts"))["layouts"] == {"Work": 2}


# -- clipboard history -------------------------------------------------------------------------


def test_what_looks_like_a_password_stays_out() -> None:
    from modules.clipboard.module import looks_secret

    assert looks_secret("hunter2!Xy") and looks_secret("sk-1234567890abcdef")
    assert not looks_secret("a sentence with spaces") and not looks_secret("short")
    assert not looks_secret("https://example.org/page?id=12345")


async def test_the_clipboard_history(paths: Paths, use_module: Callable[[str], Path]) -> None:
    from tests.test_clipboard import FakeBackend

    use_module("clipboard")
    (paths.module_configs / "clipboard.yaml").write_text("history: true\nhistory_size: 2\n")
    async with Kernel(paths) as k:
        clip = instance(k, "clipboard")
        clip.backend = FakeBackend()
        for text in ("first", "P4ssw0rd!x", "second", "second", "third"):
            clip.backend.text = text
            await clip._sample()
        history = (await call(k, "clipboard.history"))["copied"]
        assert [h["text"] for h in history] == ["third", "second"]  # (the password: never)
        await call(k, "clipboard.copy_again", n=2)
        assert clip.backend.text == "second"
        assert (await call(k, "clipboard.forget_history"))["forgotten"] == 2


# -- code review ------------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
async def test_a_code_review_of_the_changes(
    paths: Paths, use_module: Callable[[str], Path], tmp_path: Path
) -> None:
    use_module("dev")
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "test@example.org")
    git("config", "user.name", "Test")
    (repo / "calc.py").write_text("def div(a, b):\n    return a / b\n")
    git("add", "calc.py")
    git("commit", "-q", "-m", "first")
    provider = ScriptedProvider("1. calc.py line 2: b == 0 is not handled.")
    async with Kernel(paths, provider=provider) as k:
        clean = await call(k, "dev.review", repo=str(repo))
        assert clean["review"] == "There are no changes to review."
        (repo / "calc.py").write_text("def div(a, b):\n    return a // b\n")
        review = await call(k, "dev.review", repo=str(repo))
    assert "b == 0" in review["review"]
    assert "-    return a / b" in provider.calls[0][1].content


async def test_names_windows_keeps_for_devices_still_save(project: Paths, documents: Path) -> None:
    async with Kernel(project) as k:
        saved = await call(k, "recipes.save", title="Con", ingredients=["1 egg"], steps=["Fry"])
        assert Path(saved["saved"]).name == "_Con.md"
        assert (await call(k, "recipes.show", title="Con"))["title"] == "Con"
