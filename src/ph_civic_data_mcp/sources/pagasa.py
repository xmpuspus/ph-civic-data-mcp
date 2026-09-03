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
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_INDETERMINATE,
    failure_envelope,
    failure_result,
)
from ph_civic_data_mcp.utils.geo import city_to_coords, resolve_to_coords
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

PAGASA_LICENSE = "Public — PAGASA bulletin pages"

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

    # `payload.get(...)` before a shape check crashed on a bare list of
    # non-dict items: Python evaluates the first `.get()` call regardless of
    # the isinstance guard later in the same `or` chain, so that guard was
    # dead code. Check the shape before touching `.get()` anywhere.
    if isinstance(payload, dict):
        raw_days = payload.get("days") or payload.get("forecast") or []
    elif isinstance(payload, list) and payload and isinstance(payload[0], dict):
        raw_days = payload[0].get("days") or []
    else:
        log_stderr(f"PAGASA TenDay API returned an unexpected shape: {type(payload).__name__}")
        return None
    if not isinstance(raw_days, list):
        log_stderr(f"PAGASA TenDay API 'days' field is not a list: {type(raw_days).__name__}")
        return None

    daily_forecasts: list[DailyForecast] = []
    for entry in raw_days[:days]:
        if not isinstance(entry, dict):
            continue
        try:
            d = date_cls.fromisoformat(str(entry.get("date", ""))[:10])
        except ValueError:
            continue
        # A real 0mm rainfall reading is falsy, so `entry.get("rainfall") or
        # entry.get("precip")` silently replaced it with the fallback key.
        rainfall = entry.get("rainfall")
        if rainfall is None:
            rainfall = entry.get("precip")
        # Same falsy-zero bug for temperature: a real 0 degree reading (rare
        # in the lowlands, real on Mount Pulag) fell through to the fallback.
        temp_min = entry.get("min_temp")
        if temp_min is None:
            temp_min = entry.get("tmin")
        temp_max = entry.get("max_temp")
        if temp_max is None:
            temp_max = entry.get("tmax")
        daily_forecasts.append(
            DailyForecast(
                date=d,
                temp_min_c=temp_min,
                temp_max_c=temp_max,
                rainfall_mm=rainfall,
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


@mcp.tool(
    title="Philippine weather forecast",
    tags={"forecast", "pagasa", "philippines", "weather"},
    annotations={
        "title": "Philippine weather forecast",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_weather_forecast(location: str, days: int = 3) -> dict:
    """Get the weather forecast for a Philippine location.

    Uses the PAGASA TenDay API when PAGASA_API_TOKEN is set, and falls
    back to Open-Meteo when the token is absent or the PAGASA call fails.
    This tool sets no `data_status` field on a success or an
    unknown-location result. Check `data_source` and `caveats` instead.
    Examples:

      get_weather_forecast("Manila")             3-day default forecast
      get_weather_forecast("Cebu City", days=2)  2-day forecast
      get_weather_forecast("Wakanda")             unknown location, no coordinates found

    On failure, a location with no known coordinates returns days: [] and
    a caveat, with no data_status or upstream_error key. An Open-Meteo
    fetch failure returns data_status "unavailable", upstream_error: true,
    days: [], and the real error in caveats.

    Args:
        location: Municipality, city, or province name.
        days: Forecast days (1-10, default 3).

    Returns: location, forecast_issued, days (date, temp_min_c, temp_max_c,
    rainfall_mm, wind_speed_kph, wind_direction, weather_description),
    data_source (pagasa_api|open_meteo), data_retrieved_at.
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
        return failure_result(
            "Open-Meteo",
            OPEN_METEO_BASE,
            f"Open-Meteo fetch failed ({type(exc).__name__}: {exc})",
            location=location,
            days=[],
            data_source="open_meteo",
        )

    cache[key] = result
    return result


@mcp.tool(
    title="Active tropical cyclones in the PAR",
    tags={"hazard", "pagasa", "philippines", "typhoon"},
    annotations={
        "title": "Active tropical cyclones in the PAR",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_active_typhoons() -> list[dict] | dict:
    """Get active tropical cyclones in/near the Philippine Area of Responsibility (PAR).

    Returns an empty list when no cyclone is active. This tool parses the
    live PAGASA bulletin page with regular expressions. A bulletin wording
    change can miss a cyclone, but the "no active" state itself is
    reliably detected.

    Examples:
        get_active_typhoons()   # only call form, no arguments

    On failure:
        When the PAGASA bulletin page is unreachable, this tool returns
        data_status "unavailable", upstream_error: true, results: [], and
        the real error in caveats. That shape never matches a genuine "no
        active typhoons" answer, which is a bare empty list. When the page
        loads but neither the "no active cyclone" marker nor a cyclone name
        matches, this tool returns data_status "indeterminate" instead of
        guessing at "no active typhoons".

    Returns: list of typhoons, each with local_name, international_name,
    category, max_winds_kph, within_par, signal_numbers, bulletin_number,
    source, bulletin_url, data_retrieved_at. Or the failure dict above.
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
        return failure_envelope(
            "PAGASA",
            PAGASA_TC_BULLETIN_URL,
            f"PAGASA tropical cyclone bulletin unavailable ({type(exc).__name__}: {exc}). "
            "This is an upstream failure, not an absence of active typhoons.",
            license=PAGASA_LICENSE,
        )

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
        # Neither the "No Active Tropical Cyclone" marker nor a cyclone name
        # matched. A wording or markup change would look identical to this,
        # so caching [] here would read as "no typhoon" for the cache TTL.
        return failure_result(
            "PAGASA",
            PAGASA_TC_BULLETIN_URL,
            "PAGASA tropical cyclone bulletin format was not recognized: "
            "neither the 'no active cyclone' marker nor a cyclone name matched.",
            data_status=DATA_STATUS_INDETERMINATE,
            license=PAGASA_LICENSE,
            results=[],
        )

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


@mcp.tool(
    title="PAGASA weather alerts",
    tags={"alert", "pagasa", "philippines", "weather"},
    annotations={
        "title": "PAGASA weather alerts",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_weather_alerts(region: str | None = None) -> list[dict] | dict:
    """Get active PAGASA weather alerts and advisories.

    The PAGASA homepage embeds alert names such as "Heavy Rainfall
    Warning" in its navigation menu and breadcrumbs, as well as in real
    active-warning sections. This tool reliably detects the "No Active
    Warnings" state, but it cannot yet isolate a real active warning from
    that navigation text. To avoid a fabricated advisory, this tool
    returns a bare empty list for the confirmed "no active warnings"
    state. It returns the same bare empty list for the ambiguous state,
    where the page is reachable but the warning text cannot be trusted.
    For real-time advisories, call bagong.pagasa.dost.gov.ph directly.
    Examples:

      get_weather_alerts()              no region argument
      get_weather_alerts(region="NCR")  region only changes the cache key

    On failure, when the PAGASA homepage is unreachable, this tool
    returns data_status "unavailable", upstream_error: true, results: [],
    and the real error in caveats. A reachable page always returns a bare
    empty list, whether warnings are active or the text is ambiguous.

    Args:
        region: For example "NCR", "Region VII", "CALABARZON". None returns all.

    Returns: list of alerts (currently always empty, pending a reliable
    parser), or the failure dict above on an outage.
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
        return failure_envelope(
            "PAGASA",
            PAGASA_MAIN_URL,
            f"PAGASA homepage unavailable ({type(exc).__name__}: {exc}). "
            "Alert state unknown — this is an upstream failure, not 'no active warnings'.",
            license=PAGASA_LICENSE,
        )

    soup = BeautifulSoup(response.text, "lxml")
    text = soup.get_text(" ", strip=True)
    text_norm = re.sub(r"\s+", " ", text)

    if re.search(r"No\s+Active\s+Warnings?", text_norm, re.IGNORECASE):
        cache[key] = []
        return []

    # Conservative path: PAGASA homepage embeds alert names ("Heavy Rainfall
    # Warning", "Flood Advisory", "Gale Warning") in its nav menu, breadcrumbs,
    # and footer alongside any genuinely active alerts. Until we have a
    # structural way to isolate the active section, returning [] is safer
    # than risking fabricated advisories pulled from chrome text. Audit
    # 2026-05-01 documented the previous regex matching menu strings.
    log_stderr(
        "get_weather_alerts: page reachable but parser cannot reliably "
        "distinguish active alerts from PAGASA homepage chrome — returning []"
    )
    cache[key] = []
    return []
