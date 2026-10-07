"""Current weather and a short forecast for a place.

Uses Open-Meteo, which is free and needs no account or API key. Requests go through
Starlight's shared web client, so the rate limits and the size/time caps apply.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes -> plain description.
CODES: dict[int, str] = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    56: "freezing drizzle", 57: "heavy freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light rain showers", 81: "rain showers", 82: "violent rain showers",
    85: "light snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}  # fmt: skip


def describe(code: Any) -> str:
    try:
        return CODES.get(int(code), f"code {code}")
    except (TypeError, ValueError):
        return "unknown"


class Config(BaseModel):
    units: Literal["metric", "imperial"] = "metric"
    default_place: str = ""  # used when you just ask "what's the weather"
    forecast_days: int = Field(default=3, ge=1, le=14)


class WeatherModule(Module):
    Config = Config

    async def setup(self, ctx: ModuleContext) -> None:
        self.config: Config = ctx.config

    @property
    def _units(self) -> dict[str, str]:
        if self.config.units == "imperial":
            return {
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "deg": "°F",
                "spd": "mph",
            }
        return {"temperature_unit": "celsius", "wind_speed_unit": "kmh", "deg": "°C", "spd": "km/h"}

    async def _geocode(self, place: str) -> dict[str, Any]:
        place = (place or self.config.default_place).strip()
        if not place:
            raise ToolError("which place? (or set default_place in config/modules/weather.yaml)")
        response = await self.ctx.http.get(
            f"{GEOCODE}?name={place}&count=1&language=en&format=json", respect_robots=False
        )
        results = (response.json() or {}).get("results") or []
        if not results:
            raise ToolError(f"could not find a place called '{place}'")
        top = results[0]
        return {
            "name": top.get("name"),
            "country": top.get("country"),
            "admin1": top.get("admin1"),
            "latitude": top["latitude"],
            "longitude": top["longitude"],
        }

    @tool
    async def now(self, place: str = "") -> dict[str, Any]:
        """The current weather for a place (a city or "City, Country"). Uses your default
        place if you give none."""
        spot = await self._geocode(place)
        units = self._units
        url = (
            f"{FORECAST}?latitude={spot['latitude']}&longitude={spot['longitude']}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,"
            "wind_speed_10m,is_day&timezone=auto"
            f"&temperature_unit={units['temperature_unit']}&wind_speed_unit={units['wind_speed_unit']}"
        )
        current = (await self._fetch(url)).get("current") or {}
        temp = current.get("temperature_2m")
        feels = current.get("apparent_temperature")
        sky = describe(current.get("weather_code"))
        return {
            "place": self._place_name(spot),
            "summary": f"{sky}, {temp}{units['deg']} (feels like {feels}{units['deg']})",
            "temperature": f"{temp}{units['deg']}",
            "feels_like": f"{feels}{units['deg']}",
            "conditions": sky,
            "humidity": f"{current.get('relative_humidity_2m')}%",
            "wind": f"{current.get('wind_speed_10m')} {units['spd']}",
            "is_day": bool(current.get("is_day")),
        }

    @tool
    async def forecast(self, place: str = "", days: int = 0) -> dict[str, Any]:
        """A day-by-day forecast for a place (up to two weeks)."""
        spot = await self._geocode(place)
        units = self._units
        count = min(days or self.config.forecast_days, 14)
        url = (
            f"{FORECAST}?latitude={spot['latitude']}&longitude={spot['longitude']}"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
            f"&timezone=auto&forecast_days={count}"
            f"&temperature_unit={units['temperature_unit']}&wind_speed_unit={units['wind_speed_unit']}"
        )
        daily = (await self._fetch(url)).get("daily") or {}
        dates = daily.get("time") or []
        days_out = [
            {
                "date": dates[i],
                "conditions": describe((daily.get("weather_code") or [])[i]),
                "high": f"{(daily.get('temperature_2m_max') or [])[i]}{units['deg']}",
                "low": f"{(daily.get('temperature_2m_min') or [])[i]}{units['deg']}",
                "rain_chance": f"{(daily.get('precipitation_probability_max') or [None])[i]}%",
            }
            for i in range(len(dates))
        ]
        return {"place": self._place_name(spot), "days": days_out}

    async def _fetch(self, url: str) -> dict[str, Any]:
        from starlight.core.http import HttpError

        try:
            response = await self.ctx.http.get(
                url, respect_robots=False, use_cache=False
            )  # current data
        except HttpError as exc:
            raise ToolError(f"could not reach the weather service: {exc}") from exc
        if not response.ok:
            raise ToolError(f"the weather service returned HTTP {response.status}")
        data = response.json()
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _place_name(spot: dict[str, Any]) -> str:
        parts = [spot.get("name"), spot.get("admin1"), spot.get("country")]
        return ", ".join(str(p) for p in parts if p)
