import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config import cfg
from logger import log

WMO_DESC = {
    0: "Clear sky",
    1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Foggy", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Heavy freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    66: "Freezing rain", 67: "Heavy freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
CACHE_MINUTES = 20


@dataclass
class WeatherState:
    place_name: str
    temperature: float        # °C
    feels_like: float         # °C
    weather_code: int
    weather_desc: str
    cloud_cover: int          # 0–100 %
    solar_radiation: float    # W/m² current
    is_sunny: bool
    trend: str                # human-readable forecast trend
    hourly_codes: list
    hourly_clouds: list       # % cloud per hour, next 6h
    hourly_radiation: list    # W/m² per hour, next 6h
    hourly_times: list        # ISO local times, next 6h


def _fetch(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def _trend(clouds: list, rads: list) -> str:
    if not clouds or len(clouds) < 3:
        return ""
    avg_now  = (clouds[0] + clouds[1]) / 2
    avg_late = (clouds[-2] + clouds[-1]) / 2
    delta    = avg_late - avg_now

    any_solar = any(r > 50 for r in rads)

    if avg_now < 20 and avg_late < 20:
        return "Staying clear — great solar conditions ☀️"
    if delta < -15:
        return "Clearing up soon — solar improving ☀️"
    if delta > 15 and avg_late > 70:
        return "Clouding over — solar will fade ☁️"
    if avg_now > 80 and avg_late > 80:
        return "Remaining overcast — limited solar today ☁️"
    if not any_solar:
        return "No solar expected in the next 6 hours 🌙"
    return "Mixed conditions ahead ⛅"


class WeatherClient:
    def __init__(self):
        self._lat: Optional[float] = None
        self._lon: Optional[float] = None
        self._place: str = ""
        self._cache: Optional[WeatherState] = None
        self._cache_at: Optional[datetime] = None

    def _geocode(self) -> None:
        url = NOMINATIM + "?" + urllib.parse.urlencode({
            "q": cfg.address, "format": "json", "limit": 1,
            "countrycodes": "au", "addressdetails": 1,
        })
        results = _fetch(url, headers={"User-Agent": "mcnutty-energy-manager/1.0"})
        if not results:
            raise ValueError(f"Could not geocode address: {cfg.address!r}")
        r    = results[0]
        addr = r.get("address", {})
        self._lat   = float(r["lat"])
        self._lon   = float(r["lon"])
        # Prefer suburb/town/city over street name
        self._place = (
            addr.get("suburb") or addr.get("town") or addr.get("village")
            or addr.get("city") or addr.get("county")
            or r.get("display_name", cfg.address).split(",")[0]
        ).strip()
        log.info("Weather geocoded %r → %.4f, %.4f (%s)", cfg.address, self._lat, self._lon, self._place)

    def get_weather(self) -> Optional[WeatherState]:
        if self._cache and self._cache_at:
            age_mins = (datetime.now(timezone.utc) - self._cache_at).total_seconds() / 60
            if age_mins < CACHE_MINUTES:
                return self._cache

        if self._lat is None:
            self._geocode()

        url = OPEN_METEO + "?" + urllib.parse.urlencode({
            "latitude":  self._lat,
            "longitude": self._lon,
            "current":   "temperature_2m,apparent_temperature,weather_code,cloud_cover,shortwave_radiation",
            "hourly":    "weather_code,cloud_cover,shortwave_radiation",
            "timezone":  "Australia/Adelaide",
            "forecast_days": 1,
        })
        data    = _fetch(url)
        cur     = data["current"]
        hourly  = data["hourly"]

        # Find current hour index in hourly arrays (times are local Adelaide, no offset)
        cur_hour = cur["time"][:13] + ":00"
        try:
            idx = hourly["time"].index(cur_hour)
        except ValueError:
            idx = 0

        h_codes  = hourly["weather_code"][idx:idx + 6]
        h_clouds = hourly["cloud_cover"][idx:idx + 6]
        h_rads   = hourly["shortwave_radiation"][idx:idx + 6]
        h_times  = hourly["time"][idx:idx + 6]

        cloud = cur["cloud_cover"]
        rad   = float(cur.get("shortwave_radiation") or 0)

        state = WeatherState(
            place_name      = self._place,
            temperature     = cur["temperature_2m"],
            feels_like      = cur["apparent_temperature"],
            weather_code    = cur["weather_code"],
            weather_desc    = WMO_DESC.get(cur["weather_code"], "Unknown"),
            cloud_cover     = cloud,
            solar_radiation = rad,
            is_sunny        = cloud < 30 and rad > 100,
            trend           = _trend(h_clouds, h_rads),
            hourly_codes    = h_codes,
            hourly_clouds   = h_clouds,
            hourly_radiation= h_rads,
            hourly_times    = h_times,
        )
        self._cache    = state
        self._cache_at = datetime.now(timezone.utc)
        log.info(
            "Weather: %s %.1f°C cloud=%d%% solar=%.0fW/m² | %s",
            state.weather_desc, state.temperature, state.cloud_cover, state.solar_radiation, state.trend,
        )
        return state
