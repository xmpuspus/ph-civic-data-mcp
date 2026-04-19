"""AQICN / EMB air quality for Philippine cities.

Landmines (from validation log):
- 5: AQICN `demo` token returns Shanghai only. Token is REQUIRED.
- 12: Official AQICN docs use http://, not https://.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from dateutil import parser as date_parser

from ph_civic_data_mcp.models.air_quality import AirQuality
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.geo import city_to_aqicn_slug
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

AQICN_BASE = "http://api.waqi.info/feed"
TOKEN_REGISTRATION_URL = "https://aqicn.org/data-platform/token/"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aqi_category(aqi: int) -> str:
    if aqi <= 50:
        return "Good"
    if aqi <= 100:
        return "Fair"
    if aqi <= 150:
        return "Unhealthy for Sensitive Groups"
    if aqi <= 200:
        return "Unhealthy"
    if aqi <= 300:
        return "Very Unhealthy"
    return "Hazardous"


def _health_advisory(aqi: int) -> str:
    if aqi <= 50:
        return "Air quality is considered satisfactory. Enjoy outdoor activities."
    if aqi <= 100:
        return (
            "Air quality is acceptable. Unusually sensitive people should consider "
            "limiting prolonged outdoor exertion."
        )
    if aqi <= 150:
        return (
            "Sensitive groups (children, elderly, those with respiratory conditions) "
            "should reduce prolonged outdoor exertion."
        )
    if aqi <= 200:
        return (
            "Everyone may begin to experience health effects. Sensitive groups should "
            "avoid prolonged outdoor exertion."
        )
    if aqi <= 300:
        return (
            "Everyone may experience more serious health effects. Avoid outdoor exertion."
        )
    return "Health emergency. Everyone should avoid all outdoor exertion."


def _iaqi(data: dict, pollutant: str) -> float | None:
    try:
        return float(data["iaqi"][pollutant]["v"])
    except (KeyError, ValueError, TypeError):
        return None


@mcp.tool()
async def get_air_quality(city: str) -> dict:
    """Real-time air quality for a Philippine city via AQICN.

    Requires the AQICN_TOKEN environment variable.
    Get a free token (instant, 1,000 req/min) at:
    https://aqicn.org/data-platform/token/

    Args:
        city: City name. Supported: Manila, Quezon City, Cebu City, Davao City,
              Makati, Taguig, Pasig, Marikina, Mandaluyong.

    Returns AQI + pollutants (pm25, pm10, no2, so2, o3, co), dominant pollutant,
    health advisory mapped to PH EMB AQI scale.
    """
    token = os.environ.get("AQICN_TOKEN")
    if not token:
        return {
            "city": city,
            "caveats": [
                f"AQICN_TOKEN not set. Get a free token at {TOKEN_REGISTRATION_URL}",
            ],
            "source": "AQICN",
            "data_retrieved_at": _now().isoformat(),
        }

    slug = city_to_aqicn_slug(city)
    if not slug:
        return {
            "city": city,
            "caveats": [
                f"City '{city}' not in AQICN slug map. "
                "Supported: Manila, Quezon City, Cebu City, Davao City, "
                "Makati, Taguig, Pasig, Marikina, Mandaluyong."
            ],
            "source": "AQICN",
            "data_retrieved_at": _now().isoformat(),
        }

    key = cache_key({"tool": "air_quality", "slug": slug})
    cache = CACHES["emb_air_quality"]
    if key in cache:
        return cache[key]

    url = f"{AQICN_BASE}/{slug}/"
    params = {"token": token}
    try:
        response = await fetch_with_retry(CLIENT, "GET", url, params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        log_stderr(f"get_air_quality fetch error: {exc}")
        return {
            "city": city,
            "caveats": [f"AQICN fetch failed: {type(exc).__name__}"],
            "source": "AQICN",
            "data_retrieved_at": _now().isoformat(),
        }

    if payload.get("status") != "ok":
        msg = payload.get("data", "unknown AQICN error")
        return {
            "city": city,
            "caveats": [f"AQICN returned non-ok status: {msg}"],
            "source": "AQICN",
            "data_retrieved_at": _now().isoformat(),
        }

    data = payload.get("data", {})
    try:
        aqi = int(data.get("aqi", 0))
    except (ValueError, TypeError):
        aqi = 0

    measured_raw = data.get("time", {}).get("s") or ""
    measured_at = _now()
    try:
        measured_at = date_parser.parse(measured_raw)
    except (ValueError, OverflowError):
        pass

    air = AirQuality(
        city=city,
        station_name=data.get("city", {}).get("name"),
        aqi=aqi,
        aqi_category=_aqi_category(aqi),
        pm25=_iaqi(data, "pm25"),
        pm10=_iaqi(data, "pm10"),
        no2=_iaqi(data, "no2"),
        so2=_iaqi(data, "so2"),
        o3=_iaqi(data, "o3"),
        co=_iaqi(data, "co"),
        dominant_pollutant=data.get("dominentpol"),
        health_advisory=_health_advisory(aqi),
        measured_at=measured_at,
        data_retrieved_at=_now(),
    )
    result = air.model_dump(mode="json")
    cache[key] = result
    return result
