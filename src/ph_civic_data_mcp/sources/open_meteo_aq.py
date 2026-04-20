"""Open-Meteo Air Quality API — public, no auth.

Fills the gap left by AQICN removal in v0.1.8. PM2.5, PM10, NO2, SO2, O3, CO
plus European and US AQI, for any lat/lng. Coverage confirmed live for PH.
https://open-meteo.com/en/docs/air-quality-api
"""

from __future__ import annotations

from datetime import datetime, timezone

from ph_civic_data_mcp.models.climate import AirQuality
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.geo import city_to_coords
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

OPEN_METEO_AQ_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aqi_category(aqi: int | None) -> str | None:
    """Open-Meteo uses the US EPA category scale for us_aqi."""
    if aqi is None:
        return None
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Moderate"
    if aqi <= 150:
        return "Unhealthy for Sensitive Groups"
    if aqi <= 200:
        return "Unhealthy"
    if aqi <= 300:
        return "Very Unhealthy"
    return "Hazardous"


def _first(val: list | None) -> float | int | None:
    if not val:
        return None
    return val[0] if isinstance(val, list) else val


def _to_int(val: float | int | None) -> int | None:
    if val is None:
        return None
    try:
        return int(round(float(val)))
    except (TypeError, ValueError):
        return None


@mcp.tool()
async def get_air_quality(location: str) -> dict:
    """Real-time air quality for a Philippine city via Open-Meteo (no API key).

    Returns PM2.5, PM10, CO, NO2, SO2, O3 plus European AQI and US AQI with
    category interpretation. Covers ~80 major PH cities via local coordinate
    table. For unlisted locations, caller can pass coordinates directly via
    the latitude/longitude form in a future version.

    Args:
        location: City or municipality name (e.g. "Manila", "Cebu City", "Davao").
    """
    ckey = cache_key({"tool": "aq", "loc": location.strip().lower()})
    cache = CACHES["open_meteo_aq"]
    if ckey in cache:
        return cache[ckey]

    coords = city_to_coords(location)
    if coords is None:
        return {
            "location": location,
            "caveats": [f"No coordinates known for '{location}'. Try a major PH city."],
            "source": "Open-Meteo Air Quality",
            "data_retrieved_at": _now().isoformat(),
        }

    lat, lng = coords
    params = {
        "latitude": lat,
        "longitude": lng,
        "current": ",".join(
            [
                "pm10",
                "pm2_5",
                "carbon_monoxide",
                "nitrogen_dioxide",
                "sulphur_dioxide",
                "ozone",
                "european_aqi",
                "us_aqi",
            ]
        ),
        "timezone": "Asia/Manila",
    }

    try:
        response = await fetch_with_retry(CLIENT, "GET", OPEN_METEO_AQ_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        log_stderr(f"Open-Meteo AQ error: {exc}")
        return {
            "location": location,
            "latitude": lat,
            "longitude": lng,
            "caveats": [f"Open-Meteo AQ fetch failed: {type(exc).__name__}"],
            "source": "Open-Meteo Air Quality",
            "data_retrieved_at": _now().isoformat(),
        }

    current = payload.get("current", {}) or {}
    measured_at_raw = current.get("time")
    try:
        measured_at = datetime.fromisoformat(measured_at_raw) if measured_at_raw else _now()
        if measured_at.tzinfo is None:
            measured_at = measured_at.replace(tzinfo=timezone.utc)
    except ValueError:
        measured_at = _now()

    us_aqi = _to_int(current.get("us_aqi"))
    eu_aqi = _to_int(current.get("european_aqi"))

    aq = AirQuality(
        location=location,
        latitude=lat,
        longitude=lng,
        measured_at=measured_at,
        pm2_5=current.get("pm2_5"),
        pm10=current.get("pm10"),
        carbon_monoxide=current.get("carbon_monoxide"),
        nitrogen_dioxide=current.get("nitrogen_dioxide"),
        sulphur_dioxide=current.get("sulphur_dioxide"),
        ozone=current.get("ozone"),
        european_aqi=eu_aqi,
        us_aqi=us_aqi,
        aqi_category=_aqi_category(us_aqi),
        data_retrieved_at=_now(),
    ).model_dump(mode="json")

    cache[ckey] = aq
    return aq
