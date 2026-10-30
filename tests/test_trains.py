"""The trains module: Deutsche Bahn connections and departure boards (db.transport.rest),
with delays, platforms, changes and cancellations, through the shared web client."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from starlight.core import Kernel, Paths

TODAY = datetime.now().astimezone().replace(hour=8, minute=0, second=0, microsecond=0)


def at(hour: int, minute: int, days: int = 0) -> str:
    return (TODAY + timedelta(days=days)).replace(hour=hour, minute=minute).isoformat()


STATIONS = {
    "hamburg": {"type": "station", "id": "8002549", "name": "Hamburg Hbf"},
    "berlin": {"type": "station", "id": "8011160", "name": "Berlin Hbf"},
}
JOURNEY = {
    "legs": [
        {
            "origin": {"name": "Hamburg Hbf"},
            "destination": {"name": "Hannover Hbf"},
            "departure": at(8, 17),
            "plannedDeparture": at(8, 12),
            "departureDelay": 300,
            "departurePlatform": "13",
            "arrival": at(9, 30),
            "arrivalDelay": 0,
            "arrivalPlatform": "4",
            "line": {"name": "ICE 579", "productName": "ICE"},
            "remarks": [
                {"type": "warning", "summary": "Delay due to repair work"},
                {"type": "hint", "text": "Bicycles conveyed"},
            ],
        },
        {
            "origin": {"name": "Hannover Hbf"},
            "destination": {"name": "Hannover Hbf"},
            "walking": True,
            "distance": 120,
        },
        {
            "origin": {"name": "Hannover Hbf"},
            "destination": {"name": "Berlin Hbf"},
            "departure": at(9, 41),
            "departurePlatform": "8",
            "arrival": at(11, 20),
            "arrivalDelay": 120,
            "arrivalPlatform": "14",
            "line": {"name": "IC 2241"},
        },
    ],
    "price": {"amount": 29.9, "currency": "EUR"},
}


def mock_db(status: int = 200, journeys: list[Any] | None = None) -> tuple[Any, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        if status != 200:
            return httpx.Response(status)
        if request.url.path == "/locations":
            hits = [s for key, s in STATIONS.items() if key in query["query"].lower()]
            return httpx.Response(200, json=hits)
        if request.url.path == "/journeys":
            found = [JOURNEY] if journeys is None else journeys
            return httpx.Response(200, json={"journeys": found})
        if request.url.path.endswith("/departures"):
            board = [
                {
                    "when": at(8, 4), "plannedWhen": at(8, 1), "delay": 180,
                    "platform": "5", "plannedPlatform": "3", "direction": "Kiel Hbf",
                    "line": {"name": "RE 7"},
                },
                {
                    "when": None, "plannedWhen": at(8, 10), "delay": None, "cancelled": True,
                    "plannedPlatform": "1", "direction": "Ahrensburg", "line": {"name": "S 4"},
                },
            ]  # fmt: skip
            return httpx.Response(200, json={"departures": board})
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("trains")
    with paths.config_file.open("a") as config:  # no pause between requests, no retries
        config.write("http:\n  per_domain_interval: 0\n  max_retries: 0\n")
    return paths


async def call(k: Kernel, tool: str, **args: Any) -> Any:
    return await k.dispatcher.call(f"trains.{tool}", args)


async def test_a_connection_with_its_delays_change_and_platforms(project: Paths) -> None:
    transport, seen = mock_db()
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "connections", start="Hamburg", destination="Berlin")
    assert result.ok, result.error
    out = result.output
    assert out["from"] == "Hamburg Hbf" and out["to"] == "Berlin Hbf"
    trip = out["connections"][0]
    assert trip["departs"] == "08:17 (+5 min)" and trip["arrives"] == "11:20 (+2 min)"
    assert trip["duration"] == "3 h 03 min" and trip["changes"] == 1
    assert trip["trains"] == "ICE 579 / IC 2241" and trip["price"] == "from 29.90 EUR"
    assert trip["legs"][0] == (
        "ICE 579: Hamburg Hbf 08:17 (+5 min), platform 13 -> Hannover Hbf 09:30, platform 4"
    )
    assert trip["legs"][1] == "walk 120 m to Hannover Hbf"
    assert trip["warnings"] == ["Delay due to repair work"]  # (a hint is not a warning)
    journeys = next(u for u in seen if "/journeys" in u)
    assert "from=8002549" in journeys and "to=8011160" in journeys and "departure=" in journeys


async def test_arrive_by_and_a_later_time(project: Paths) -> None:
    transport, seen = mock_db()
    async with Kernel(project, http_transport=transport) as k:
        result = await call(
            k, "connections", start="Hamburg", destination="Berlin",
            when="morgen um 9 Uhr", arrive_by=True,
        )  # fmt: skip
    assert result.ok, result.error
    query = parse_qs(next(u for u in seen if "/journeys" in u).split("?", 1)[1])
    arrival = datetime.fromisoformat(query["arrival"][0])
    assert (arrival.hour, arrival.minute) == (9, 0)
    assert arrival.date() == (datetime.now() + timedelta(days=1)).date()
    assert "arrive by" in result.output


async def test_the_home_station_is_the_start(project: Paths) -> None:
    (project.config / "modules").mkdir(exist_ok=True)
    (project.config / "modules" / "trains.yaml").write_text("home_station: Hamburg Hbf\n")
    transport, _ = mock_db()
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "connections", destination="Berlin")
        board = await call(k, "departures")
    assert result.output["from"] == "Hamburg Hbf"
    assert board.output["station"] == "Hamburg Hbf"


async def test_without_a_start_or_home_station_it_asks(project: Paths) -> None:
    transport, _ = mock_db()
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "connections", destination="Berlin")
    assert not result.ok and "which station" in str(result.error)


async def test_the_departure_board(project: Paths) -> None:
    transport, seen = mock_db()
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "departures", station="Hamburg")
    first, second = result.output["departures"]
    assert first == {
        "time": "08:04", "line": "RE 7", "towards": "Kiel Hbf", "delay": "+3 min",
        "platform": "5 (instead of 3)",
    }  # fmt: skip
    assert second["cancelled"] and second["time"] == "08:10" and second["platform"] == "1"
    assert any("/stops/8002549/departures" in u for u in seen)


async def test_unknown_station_busy_service_and_no_connections(project: Paths) -> None:
    transport, _ = mock_db()
    async with Kernel(project, http_transport=transport) as k:
        unknown = await call(k, "connections", start="Hamburg", destination="Atlantis")
    assert "could not find a station called 'Atlantis'" in str(unknown.error)

    transport, _ = mock_db(status=503)
    async with Kernel(project, http_transport=transport) as k:
        busy = await call(k, "departures", station="Hamburg")
    assert "busy or down (HTTP 503)" in str(busy.error)

    transport, _ = mock_db(journeys=[])
    async with Kernel(project, http_transport=transport) as k:
        none = await call(k, "connections", start="Hamburg", destination="Berlin")
    assert "no connections found from Hamburg Hbf to Berlin Hbf" in str(none.error)


async def test_find_station(project: Paths) -> None:
    transport, _ = mock_db()
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "find_station", name="Berlin")
    assert result.output == [{"name": "Berlin Hbf", "id": "8011160"}]


def test_times_in_german_and_english() -> None:
    from modules.trains.module import clock, delay, duration, parse_departure

    now = datetime(2026, 10, 30, 7, 0).astimezone()
    assert parse_departure("", now) == now
    assert parse_departure("morgen 8 Uhr", now).replace(tzinfo=None) == datetime(2026, 10, 31, 8)
    assert parse_departure("um 18.30 Uhr", now).replace(tzinfo=None) == datetime(
        2026, 10, 30, 18, 30
    )
    assert parse_departure("in 20 Minuten", now) == now + timedelta(minutes=20)
    assert parse_departure("tomorrow 7am", now).replace(tzinfo=None) == datetime(2026, 10, 31, 7)
    assert clock("2026-10-31T09:05:00+01:00", now) == "Sat 31.10. 09:05" and clock(None) == "?"
    assert (
        delay(300) == "+5 min" and delay(0) == "" and delay(None) == "" and delay(-60) == "-1 min"
    )
    assert duration("2026-10-30T08:00:00+01:00", "2026-10-30T08:45:00+01:00") == "45 min"
