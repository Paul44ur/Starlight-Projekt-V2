"""Every request knows the date and time, where saved files go and the user's profile; the
profile is kept in memory/profile.md, filled in from what the user says, and used by
modules (the weather for "here")."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from zoneinfo import ZoneInfo

from starlight.core import Kernel, Paths
from starlight.core.situation import describe, local_now
from starlight.memory.profile import Profile
from starlight.memory.store import MemoryStore
from tests.conftest import ScriptedProvider
from tests.test_weather import mock_openmeteo


def test_the_situation_text() -> None:
    now = dt.datetime(2026, 10, 8, 14, 5, tzinfo=ZoneInfo("Europe/Vienna"))
    folder = Path("/home/paul/Documents/Starlight")
    text = describe(now, folder, {"name": "Paul"})
    assert "Thursday, 8 October 2026, 14:05" in text
    assert "UTC+02:00" in text
    assert str(folder) in text  # with \ on Windows
    assert "name: Paul" in text
    assert "profile" not in describe(now, None, {})
    assert local_now("Asia/Tokyo").utcoffset() == dt.timedelta(hours=9)
    assert local_now("Not/AZone").tzinfo is not None  # falls back to the PC's zone


def test_profile_file_round_trip(tmp_path: Path) -> None:
    profile = Profile(MemoryStore(tmp_path))
    assert profile.read() == {}
    profile.set("name", "Paul")
    profile.set("City", "Vienna")  # an alias for home
    profile.set("time zone", "Europe/Vienna")
    assert profile.read() == {"name": "Paul", "home": "Vienna", "timezone": "Europe/Vienna"}
    text = (tmp_path / "profile.md").read_text()
    assert "Name: Paul" in text and "Home: Vienna" in text
    # Edited by hand, with a free line: still read.
    (tmp_path / "profile.md").write_text(text + "Likes short answers\n")
    assert profile.read()["preferences"] == "Likes short answers"
    profile.set("home", "")
    assert "home" not in profile.read()


async def test_every_request_gets_the_date_and_the_profile(paths: Paths) -> None:
    provider = ScriptedProvider("hello")
    async with Kernel(paths, provider=provider) as k:
        await k.dispatcher.call("memory.set_profile", {"field": "name", "value": "Paul"})
        await k.dispatcher.call("memory.set_profile", {"field": "language", "value": "German"})
        await k.ask("hi")
        system = [m.content for m in provider.calls[0] if m.role == "system"]  # type: ignore[attr-defined]
        situation = system[1]
        assert f"{dt.date.today().year}" in situation and "Now:" in situation
        assert "name: Paul" in situation and "language: German" in situation
        assert "Files you save" in situation
        # The profile is not repeated as a "remembered" memory.
        assert not any("profile" in s.lower() and "remember" in s.lower() for s in system)
        assert (await k.dispatcher.call("memory.profile", {})).output["name"] == "Paul"
        bad = await k.dispatcher.call("memory.set_profile", {"field": "shoe", "value": "44"})
        assert not bad.ok and "unknown profile field" in (bad.error or "")


async def test_the_profile_is_filled_in_from_what_the_user_says(paths: Paths) -> None:
    facts = json.dumps(
        [
            {"profile": "home", "value": "Vienna, Austria"},
            {"profile": "name", "value": "Paul"},
            {"topic": "Editor", "fact": "Paul uses Neovim.", "confidence": 0.9},
        ]
    )
    provider = ScriptedProvider("Nice to meet you, Paul.", facts)
    async with Kernel(paths, provider=provider) as k:
        await k.ask("I'm Paul, I live in Vienna and I use Neovim")
        await k.drain()
        assert k.memory.profile.read() == {"name": "Paul", "home": "Vienna, Austria"}
        # A similar fact is never merged into the profile file.
        entry, merged = await k.memory.remember("Paul lives in Vienna", topic="Profile")
        assert entry.id != "profile" and not merged
    assert (paths.memory / "editor.md").is_file()


async def test_weather_uses_the_home_place(paths: Paths, use_module: Callable[[str], Path]) -> None:
    use_module("weather")
    transport, seen = mock_openmeteo()
    async with Kernel(paths, http_transport=transport) as k:
        missing = await k.dispatcher.call("weather.now", {})
        assert not missing.ok and "where you live" in (missing.error or "")
        await k.dispatcher.call("memory.set_profile", {"field": "home", "value": "Vienna"})
        result = await k.dispatcher.call("weather.now", {})
        assert result.ok and result.output["place"].startswith("Vienna")
        assert any("name=Vienna" in url for url in seen)


async def test_extracted_preferences_are_added_and_nothing_is_cleared(paths: Paths) -> None:
    facts = json.dumps(
        [
            {"profile": "preferences", "value": "metric units"},
            {"profile": "home", "value": "", "fact": "x"},
            {"profile": "preferences", "value": "Vegetarian"},
        ]
    )
    provider = ScriptedProvider("ok", facts)
    async with Kernel(paths, provider=provider) as k:
        k.memory.profile.set("preferences", "vegetarian; short answers")
        k.memory.profile.set("home", "Vienna")
        await k.ask("use metric units please")
        await k.drain()
        profile = k.memory.profile.read()
    assert profile["preferences"] == "vegetarian; short answers; metric units"
    assert profile["home"] == "Vienna"
