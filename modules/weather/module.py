"""Current weather and a short forecast for a place.

Uses Open-Meteo, which is free and needs no account or API key. Requests go through
Starlight's shared web client, so the rate limits and the size/time caps apply.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from starlight.core import Module, ModuleContext, ToolError, tool
from starlight.core.http import HttpError

GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"
AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"  # pollen: Europe only
POLLEN = {
    "alder_pollen": "alder", "birch_pollen": "birch", "grass_pollen": "grass",
    "mugwort_pollen": "mugwort", "olive_pollen": "olive", "ragweed_pollen": "ragweed",
}  # fmt: skip
# Grains per cubic metre -> how much it bothers someone who is allergic.
POLLEN_LEVELS = (
    (1, "none"),
    (10, "low"),
    (50, "moderate"),
    (200, "high"),
    (float("inf"), "very high"),
)
# The European air quality index (0 good ... 100+ extremely poor).
AQI_LEVELS = ((20, "good"), (40, "fair"), (60, "moderate"), (80, "poor"), (100, "very poor"),
              (float("inf"), "extremely poor"))  # fmt: skip


def level(value: float, levels: tuple[tuple[float, str], ...]) -> str:
    return next(name for limit, name in levels if value < limit)


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


def at(series: Any, index: int) -> Any:
    """``series[index]``, or None when the service left that series out or cut it short."""
    return series[index] if isinstance(series, list) and 0 <= index < len(series) else None


def shown(value: Any, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value}{suffix}"


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
        place = (place or self.config.default_place or self.ctx.profile().get("home", "")).strip()
        if not place:
            raise ToolError(
                "which place? (or tell me where you live, or set default_place in "
                "config/modules/weather.yaml)"
            )
        query = urlencode({"name": place, "count": 1, "language": "en", "format": "json"})
        results = (await self._fetch(f"{GEOCODE}?{query}", use_cache=True)).get("results") or []
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
        deg = units["deg"]
        return {
            "place": self._place_name(spot),
            "summary": f"{sky}, {shown(temp, deg)} (feels like {shown(feels, deg)})",
            "temperature": shown(temp, deg),
            "feels_like": shown(feels, deg),
            "conditions": sky,
            "humidity": shown(current.get("relative_humidity_2m"), "%"),
            "wind": shown(current.get("wind_speed_10m"), f" {units['spd']}"),
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
        codes = daily.get("weather_code")
        highs, lows = daily.get("temperature_2m_max"), daily.get("temperature_2m_min")
        rain = daily.get("precipitation_probability_max")
        days_out = [
            {
                "date": date,
                "conditions": describe(at(codes, i)),
                "high": shown(at(highs, i), units["deg"]),
                "low": shown(at(lows, i), units["deg"]),
                "rain_chance": shown(at(rain, i), "%"),
            }
            for i, date in enumerate(dates)
        ]
        return {"place": self._place_name(spot), "days": days_out}

    @tool
    async def air(self, place: str = "") -> dict[str, Any]:
        """Pollen (alder, birch, grass, mugwort, olive, ragweed - in Europe), air quality
        and UV for today and the next two days, for a place."""
        spot = await self._geocode(place)
        fields = ",".join([*POLLEN, "european_aqi", "uv_index"])
        url = (
            f"{AIR}?latitude={spot['latitude']}&longitude={spot['longitude']}"
            f"&hourly={fields}&forecast_days=3&timezone=auto"
        )
        hourly = (await self._fetch(url)).get("hourly") or {}
        times = hourly.get("time") or []
        days: dict[str, dict[str, float]] = {}
        for i, stamp in enumerate(times):
            day = days.setdefault(str(stamp)[:10], {})
            for key in (*POLLEN, "european_aqi", "uv_index"):
                value = at(hourly.get(key), i)
                if isinstance(value, int | float):
                    day[key] = max(day.get(key, value), value)
        out = []
        for date, peak in days.items():
            pollen = {
                name: f"{level(peak[key], POLLEN_LEVELS)} ({peak[key]:.0f})"
                for key, name in POLLEN.items()
                if key in peak and peak[key] >= 1
            }
            entry: dict[str, Any] = {"date": date, "pollen": pollen or "none in the air"}
            if not any(key in peak for key in POLLEN):
                entry["pollen"] = "no pollen data for this region (Europe only)"
            if "european_aqi" in peak:
                entry["air"] = (
                    f"{level(peak['european_aqi'], AQI_LEVELS)} ({peak['european_aqi']:.0f})"
                )
            if "uv_index" in peak:
                entry["uv_max"] = round(peak["uv_index"], 1)
            out.append(entry)
        return {"place": self._place_name(spot), "days": out}

    async def _fetch(self, url: str, use_cache: bool = False) -> dict[str, Any]:
        """The service's JSON answer; the weather itself is never cached (current data)."""
        try:
            response = await self.ctx.http.get(url, respect_robots=False, use_cache=use_cache)
        except HttpError as exc:
            raise ToolError(f"could not reach the weather service: {exc}") from exc
        if not response.ok:
            raise ToolError(f"the weather service returned HTTP {response.status}")
        try:
            data = response.json()
        except ValueError as exc:
            raise ToolError("the weather service sent an answer I could not read") from exc
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _place_name(spot: dict[str, Any]) -> str:
        parts = [spot.get("name"), spot.get("admin1"), spot.get("country")]
        return ", ".join(str(p) for p in parts if p)
