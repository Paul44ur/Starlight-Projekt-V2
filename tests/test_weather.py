"""The weather module: it looks a place up and reports the weather, through the shared
(rate-limited, capped) web client. No API key."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from starlight.core import Kernel, Paths


def mock_openmeteo(
    unknown: bool = False,
    geocode: httpx.Response | None = None,
    daily: dict[str, Any] | None = None,
) -> tuple[httpx.MockTransport, list[str]]:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        host, path = request.url.host, request.url.path
        if path == "/robots.txt":
            return httpx.Response(404)
        if host == "geocoding-api.open-meteo.com":
            if geocode is not None:
                return geocode
            if unknown:
                return httpx.Response(200, json={"results": []})
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "name": "Vienna",
                            "country": "Austria",
                            "admin1": "Vienna",
                            "latitude": 48.2,
                            "longitude": 16.37,
                        }
                    ]
                },
            )
        if "current=" in request.url.query.decode():
            return httpx.Response(
                200,
                json={
                    "current": {
                        "temperature_2m": 12.3,
                        "apparent_temperature": 10.0,
                        "weather_code": 3,
                        "relative_humidity_2m": 70,
                        "wind_speed_10m": 15,
                        "is_day": 1,
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "daily": daily
                or {
                    "time": ["2026-10-06", "2026-10-07", "2026-10-08"],
                    "weather_code": [0, 61, 95],
                    "temperature_2m_max": [14, 13, 11],
                    "temperature_2m_min": [6, 7, 5],
                    "precipitation_probability_max": [10, 80, 90],
                }
            },
        )

    return httpx.MockTransport(handler), seen


@pytest.fixture
def project(paths: Paths, use_module: Callable[[str], Path]) -> Paths:
    use_module("weather")
    return paths


async def call(k: Kernel, tool: str, **args: Any) -> Any:
    return await k.dispatcher.call(f"weather.{tool}", args)


async def test_current_weather(project: Paths) -> None:
    transport, _ = mock_openmeteo()
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "now", place="Vienna")
    out = result.output
    assert out["place"] == "Vienna, Vienna, Austria"
    assert out["conditions"] == "overcast" and "12.3" in out["temperature"]
    assert "overcast" in out["summary"] and out["humidity"] == "70%"


async def test_forecast(project: Paths) -> None:
    transport, _ = mock_openmeteo()
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "forecast", place="Vienna", days=3)
    days = result.output["days"]
    assert len(days) == 3
    assert days[0]["conditions"] == "clear sky" and days[1]["conditions"] == "light rain"
    assert days[2]["conditions"] == "thunderstorm" and days[1]["rain_chance"] == "80%"


async def test_unknown_place(project: Paths) -> None:
    transport, _ = mock_openmeteo(unknown=True)
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "now", place="Nowhereville")
    assert not result.ok and "could not find" in str(result.error)


async def test_needs_a_place(project: Paths) -> None:
    transport, _ = mock_openmeteo()
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "now")  # no place, no default
    assert not result.ok and "which place" in str(result.error)


async def test_imperial_units_reach_the_api(project: Paths) -> None:
    (project.module_configs / "weather.yaml").write_text("units: imperial\n")
    transport, seen = mock_openmeteo()
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "now", place="Vienna")
    assert "°F" in result.output["temperature"]
    assert any("temperature_unit=fahrenheit" in url for url in seen)


async def test_geocoder_trouble_is_not_reported_as_an_unknown_place(project: Paths) -> None:
    for answer in (
        httpx.Response(500, json={"error": True, "reason": "overloaded"}),
        httpx.Response(200, text="<html>Bad gateway</html>"),
    ):
        transport, _ = mock_openmeteo(geocode=answer)
        async with Kernel(project, http_transport=transport) as k:
            result = await call(k, "now", place="Vienna")
        assert not result.ok and "could not find" not in str(result.error)
        assert "weather service" in str(result.error)


async def test_the_place_is_url_encoded(project: Paths) -> None:
    transport, seen = mock_openmeteo()
    async with Kernel(project, http_transport=transport) as k:
        await call(k, "now", place="A&B #1+2")
    geocode = next(url for url in seen if "geocoding" in url)
    assert "name=A%26B+%231%2B2&" in geocode and "#" not in geocode


async def test_a_forecast_with_a_missing_series_still_answers(project: Paths) -> None:
    daily = {
        "time": ["2026-10-06", "2026-10-07"],
        "weather_code": [0, 61],
        "temperature_2m_max": [14],  # cut short
    }
    transport, _ = mock_openmeteo(daily=daily)
    async with Kernel(project, http_transport=transport) as k:
        result = await call(k, "forecast", place="Vienna", days=2)
    first, second = result.output["days"]
    assert first["high"] == "14°C" and first["rain_chance"] == "n/a" and first["low"] == "n/a"
    assert second["conditions"] == "light rain" and second["high"] == "n/a"
