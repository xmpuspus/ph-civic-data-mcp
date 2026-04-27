"""PAGASA — weather forecast, typhoons, alerts.

Primary: PAGASA TenDay API (requires PAGASA_API_TOKEN).
Fallback: Open-Meteo (free, no key) — automatic when token absent.

Landmines (from validation log):
- 3: PANaHON banned as fallback; use Open-Meteo instead
- 4: PAGASA Excel files discontinued Aug 31, 2025; never reference
"""

from __future__ import annotations

import os
import re
from datetime import date as date_cls, datetime, timezone

from bs4 import BeautifulSoup

from ph_civic_data_mcp.models.weather import DailyForecast, Typhoon, WeatherForecast
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.geo import city_to_coords, normalize_region, resolve_to_coords
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

PAGASA_API_BASE = "https://tenday.pagasa.dost.gov.ph/api/v1"
OPEN_METEO_BASE = "https://api.open-meteo.com/v1/forecast"
PAGASA_TC_BULLETIN_URL = (
    "https://bagong.pagasa.dost.gov.ph/tropical-cyclone/severe-weather-bulletin"
)
PAGASA_MAIN_URL = "https://bagong.pagasa.dost.gov.ph/"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _wind_direction(degrees: float | None) -> str | None:
    if degrees is None:
        return None
    dirs = [
        "N",
        "NNE",
        "NE",
        "ENE",
        "E",
        "ESE",
        "SE",
        "SSE",
        "S",
        "SSW",
        "SW",
        "WSW",
        "W",
        "WNW",
        "NW",
        "NNW",
    ]
    idx = int((degrees + 11.25) // 22.5) % 16
    return dirs[idx]


async def _open_meteo_forecast(location: str, lat: float, lng: float, days: int) -> dict:
    params = {
        "latitude": lat,
        "longitude": lng,
        "daily": ",".join(
            [
                "temperature_2m_max",
                "temperature_2m_min",
                "precipitation_sum",
                "windspeed_10m_max",
                "winddirection_10m_dominant",
                "weathercode",
            ]
        ),
        "timezone": "Asia/Manila",
        "forecast_days": days,
    }
    response = await fetch_with_retry(CLIENT, "GET", OPEN_METEO_BASE, params=params)
    response.raise_for_status()
    payload = response.json()
    daily = payload.get("daily", {})

    daily_forecasts: list[DailyForecast] = []
    dates = daily.get("time", [])
    for i, d in enumerate(dates):
        try:
            iso_date = date_cls.fromisoformat(d)
        except ValueError:
            continue
        wind_deg = (
            daily.get("winddirection_10m_dominant", [None])[i]
            if i < len(daily.get("winddirection_10m_dominant", []))
            else None
        )
        daily_forecasts.append(
            DailyForecast(
                date=iso_date,
                temp_min_c=_safe_get(daily, "temperature_2m_min", i),
                temp_max_c=_safe_get(daily, "temperature_2m_max", i),
                rainfall_mm=_safe_get(daily, "precipitation_sum", i),
                wind_speed_kph=_safe_get(daily, "windspeed_10m_max", i),
                wind_direction=_wind_direction(wind_deg),
                weather_description=_weather_code_description(_safe_get(daily, "weathercode", i)),
            )
        )

    forecast = WeatherForecast(
        location=location,
        forecast_issued=_now(),
        days=daily_forecasts,
        data_source="open_meteo",
        data_retrieved_at=_now(),
    )
    return forecast.model_dump(mode="json")


def _safe_get(d: dict, key: str, idx: int) -> float | None:
    arr = d.get(key)
    if not arr or idx >= len(arr):
        return None
    val = arr[idx]
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _weather_code_description(code: float | None) -> str | None:
    if code is None:
        return None
    codes = {
        0: "Clear sky",
        1: "Mainly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Depositing rime fog",
        51: "Light drizzle",
        53: "Moderate drizzle",
        55: "Dense drizzle",
        61: "Slight rain",
        63: "Moderate rain",
        65: "Heavy rain",
        71: "Slight snow fall",
        73: "Moderate snow fall",
        75: "Heavy snow fall",
        80: "Slight rain showers",
        81: "Moderate rain showers",
        82: "Violent rain showers",
        95: "Thunderstorm",
        96: "Thunderstorm with slight hail",
        99: "Thunderstorm with heavy hail",
    }
    return codes.get(int(code), f"Weather code {int(code)}")


async def _pagasa_api_forecast(location: str, days: int, token: str) -> dict | None:
    headers = {"Authorization": f"Bearer {token}"}
    params = {"municipality": location}
    try:
        response = await fetch_with_retry(
            CLIENT, "GET", f"{PAGASA_API_BASE}/tenday/full", params=params, headers=headers
        )
        if response.status_code == 404:
            params = {"province": location}
            response = await fetch_with_retry(
                CLIENT,
                "GET",
                f"{PAGASA_API_BASE}/tenday/full",
                params=params,
                headers=headers,
            )
        response.raise_for_status()
    except Exception as exc:
        log_stderr(f"PAGASA TenDay API error: {exc}; falling back to Open-Meteo")
        return None

    payload = response.json()
    if not payload:
        return None

    raw_days = (
        payload.get("days")
        or payload.get("forecast")
        or (payload[0].get("days") if isinstance(payload, list) and payload else [])
    )
    daily_forecasts: list[DailyForecast] = []
    for entry in raw_days[:days]:
        try:
            d = date_cls.fromisoformat(str(entry.get("date", ""))[:10])
        except ValueError:
            continue
        daily_forecasts.append(
            DailyForecast(
                date=d,
                temp_min_c=entry.get("min_temp") or entry.get("tmin"),
                temp_max_c=entry.get("max_temp") or entry.get("tmax"),
                rainfall_mm=entry.get("rainfall") or entry.get("precip"),
                wind_speed_kph=entry.get("wind_speed"),
                wind_direction=entry.get("wind_direction"),
                weather_description=entry.get("weather") or entry.get("description"),
            )
        )

    if not daily_forecasts:
        return None

    forecast = WeatherForecast(
        location=location,
        forecast_issued=_now(),
        days=daily_forecasts,
        data_source="pagasa_api",
        data_retrieved_at=_now(),
    )
    return forecast.model_dump(mode="json")


@mcp.tool()
async def get_weather_forecast(location: str, days: int = 3) -> dict:
    """Get weather forecast for a Philippine location.

    Uses PAGASA TenDay API when PAGASA_API_TOKEN is set, Open-Meteo otherwise.

    Args:
        location: Municipality, city, or province name.
        days: Forecast days (1-10, default 3).
    """
    days = max(1, min(int(days), 10))
    key = cache_key({"tool": "weather", "location": location.lower(), "days": days})
    cache = CACHES["pagasa_forecast"]
    if key in cache:
        return cache[key]

    token = os.environ.get("PAGASA_API_TOKEN")
    if token:
        result = await _pagasa_api_forecast(location, days, token)
        if result:
            cache[key] = result
            return result

    coords = await resolve_to_coords(location)
    if coords is None:
        coords = city_to_coords(location)
    if coords is None:
        return {
            "location": location,
            "caveats": [
                f"No coordinates known for '{location}'. Try a major PH city (Manila, Cebu, Davao).",
            ],
            "days": [],
            "data_source": "open_meteo",
            "data_retrieved_at": _now().isoformat(),
            "source": "Open-Meteo",
            "source_url": "https://api.open-meteo.com/v1/forecast",
            "license": "Open-Meteo CC-BY 4.0",
        }

    lat, lng = coords
    try:
        result = await _open_meteo_forecast(location, lat, lng, days)
    except Exception as exc:
        log_stderr(f"get_weather_forecast error: {exc}")
        return {
            "location": location,
            "caveats": [f"Open-Meteo fetch failed: {type(exc).__name__}"],
            "days": [],
            "data_source": "open_meteo",
            "data_retrieved_at": _now().isoformat(),
        }

    cache[key] = result
    return result


@mcp.tool()
async def get_active_typhoons() -> list[dict]:
    """Get active tropical cyclones in/near the Philippine Area of Responsibility (PAR).

    Returns empty list if none active.
    """
    key = cache_key({"endpoint": "typhoons"})
    cache = CACHES["pagasa_typhoons"]
    if key in cache:
        return cache[key]

    try:
        response = await fetch_with_retry(CLIENT, "GET", PAGASA_TC_BULLETIN_URL)
        response.raise_for_status()
    except Exception as exc:
        log_stderr(f"get_active_typhoons error: {exc}")
        return []

    soup = BeautifulSoup(response.text, "lxml")
    text = soup.get_text(" ", strip=True)
    text_norm = re.sub(r"\s+", " ", text)

    if re.search(r"No\s+Active\s+Tropical\s+Cyclone", text_norm, re.IGNORECASE):
        cache[key] = []
        return []

    bulletin_no = None
    bulletin_match = re.search(r"Tropical Cyclone Bulletin(?:\s*No\.?\s*(\d+))?", text_norm)
    if bulletin_match and bulletin_match.group(1):
        bulletin_no = int(bulletin_match.group(1))

    local_names: list[str] = []
    for match in re.finditer(
        r"(?:Tropical Depression|Tropical Storm|Severe Tropical Storm|Typhoon|Super Typhoon)\s+\"?([A-Z][A-Za-z]+)\"?",
        text_norm,
    ):
        name = match.group(1)
        if name.lower() not in {"bulletin", "warning", "advisory", "information"}:
            local_names.append(name)

    if not local_names:
        cache[key] = []
        return []

    seen = set()
    unique_names: list[str] = []
    for name in local_names:
        if name.lower() not in seen:
            seen.add(name.lower())
            unique_names.append(name)

    results: list[dict] = []
    for name in unique_names:
        category_match = re.search(
            r"(Tropical Depression|Tropical Storm|Severe Tropical Storm|Super Typhoon|Typhoon)\s+\"?"
            + re.escape(name),
            text_norm,
        )
        category = category_match.group(1) if category_match else "Tropical Cyclone"

        wind_match = re.search(
            r"maximum (?:sustained )?winds of\s*([\d.,]+)\s*(?:km/?h|kph)", text_norm, re.IGNORECASE
        )
        max_winds = None
        if wind_match:
            try:
                max_winds = float(wind_match.group(1).replace(",", ""))
            except ValueError:
                max_winds = None

        within_par = bool(
            re.search(r"inside (?:the )?PAR|within (?:the )?PAR", text_norm, re.IGNORECASE)
        )

        signal_numbers: dict[str, int] = {}
        for signal_match in re.finditer(
            r"(?:TCWS|Signal)\s*(?:No\.)?\s*([1-5])[^.]*?:?\s*([A-Z][A-Za-z,\s.-]{3,120})",
            text_norm,
        ):
            try:
                level = int(signal_match.group(1))
                area = signal_match.group(2).strip().rstrip(".,;")
                if area and len(area) < 200:
                    signal_numbers[area[:100]] = level
            except ValueError:
                continue

        typhoon = Typhoon(
            local_name=name,
            international_name=None,
            category=category,
            max_winds_kph=max_winds,
            within_par=within_par,
            signal_numbers=signal_numbers,
            bulletin_datetime=_now(),
        )
        results.append(
            {
                **typhoon.model_dump(mode="json"),
                "bulletin_number": bulletin_no,
                "source": "PAGASA",
                "bulletin_url": PAGASA_TC_BULLETIN_URL,
                "data_retrieved_at": _now().isoformat(),
            }
        )

    cache[key] = results
    return results


@mcp.tool()
async def get_weather_alerts(region: str | None = None) -> list[dict]:
    """Get active PAGASA weather alerts and advisories.

    Args:
        region: e.g. "NCR", "Region VII", "CALABARZON". None returns all.
    """
    key = cache_key({"endpoint": "alerts", "region": region})
    cache = CACHES["pagasa_alerts"]
    if key in cache:
        return cache[key]

    try:
        response = await fetch_with_retry(CLIENT, "GET", PAGASA_MAIN_URL)
        response.raise_for_status()
    except Exception as exc:
        log_stderr(f"get_weather_alerts error: {exc}")
        return []

    soup = BeautifulSoup(response.text, "lxml")
    text = soup.get_text(" ", strip=True)
    text_norm = re.sub(r"\s+", " ", text)

    if re.search(r"No\s+Active\s+Warnings?", text_norm, re.IGNORECASE):
        cache[key] = []
        return []

    alerts: list[dict] = []
    for pattern, alert_type in [
        (r"Heavy Rainfall (?:Warning|Advisory)", "Heavy Rainfall"),
        (r"Thunderstorm (?:Watch|Warning|Advisory)", "Thunderstorm"),
        (r"Flood (?:Watch|Warning|Advisory)", "Flood"),
        (r"Gale Warning", "Gale"),
    ]:
        for match in re.finditer(pattern, text_norm, re.IGNORECASE):
            start = max(0, match.start() - 50)
            end = min(len(text_norm), match.end() + 200)
            context = text_norm[start:end]
            alert = {
                "alert_type": alert_type,
                "severity": "Advisory",
                "description": context,
                "affected_areas": normalize_region(region) or "Multiple regions",
                "issued_datetime": _now().isoformat(),
                "valid_until": None,
                "source": "PAGASA",
                "data_retrieved_at": _now().isoformat(),
            }
            alerts.append(alert)
            break  # one per category

    cache[key] = alerts
    return alerts
