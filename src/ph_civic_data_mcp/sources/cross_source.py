"""Cross-source multi-hazard risk assessment tool."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from dateutil import parser as date_parser

from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.sources.emb import get_air_quality
from ph_civic_data_mcp.sources.pagasa import get_active_typhoons
from ph_civic_data_mcp.sources.phivolcs import get_latest_earthquakes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _risk_from_activity(count: int, max_magnitude: float) -> str:
    if max_magnitude >= 6.0 or count >= 50:
        return "Very High"
    if max_magnitude >= 5.0 or count >= 20:
        return "High"
    if max_magnitude >= 4.0 or count >= 8:
        return "Moderate"
    return "Low"


@mcp.tool()
async def assess_area_risk(location: str) -> dict:
    """Multi-hazard risk assessment combining PHIVOLCS + PAGASA + AQICN.

    Makes 3 parallel upstream calls. Expect 5-10 second response time.

    Args:
        location: Municipality, city, or province name.

    Returns:
        earthquake_risk_level derived from recent 30-day seismic activity (not an
        official PHIVOLCS assessment), typhoon signal status, air quality snapshot,
        and caveats describing any failed sub-calls.
    """
    retrieved_at = _now()
    caveats: list[str] = []

    earthquakes_task = asyncio.create_task(
        get_latest_earthquakes(min_magnitude=1.0, limit=100, region=location)
    )
    typhoons_task = asyncio.create_task(get_active_typhoons())
    air_task = asyncio.create_task(get_air_quality(location))

    results = await asyncio.gather(
        earthquakes_task, typhoons_task, air_task, return_exceptions=True
    )
    earthquakes_result, typhoons_result, air_result = results

    recent_earthquakes_30d = 0
    max_magnitude_30d = 0.0
    if isinstance(earthquakes_result, BaseException):
        caveats.append(f"PHIVOLCS query failed: {type(earthquakes_result).__name__}")
        earthquakes = []
    else:
        earthquakes = earthquakes_result or []
        cutoff = retrieved_at - timedelta(days=30)
        for quake in earthquakes:
            dt_raw = quake.get("datetime_pst")
            try:
                dt = date_parser.parse(dt_raw) if isinstance(dt_raw, str) else dt_raw
            except (ValueError, TypeError):
                continue
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                recent_earthquakes_30d += 1
                max_magnitude_30d = max(max_magnitude_30d, quake.get("magnitude", 0.0))

    typhoon_signal_active = False
    active_typhoon_name: str | None = None
    if isinstance(typhoons_result, BaseException):
        caveats.append(f"PAGASA typhoon query failed: {type(typhoons_result).__name__}")
    else:
        typhoons = typhoons_result or []
        for t in typhoons:
            if t.get("signal_numbers"):
                typhoon_signal_active = True
                active_typhoon_name = t.get("local_name")
                break
        if typhoons and not active_typhoon_name:
            active_typhoon_name = typhoons[0].get("local_name")

    air_quality_aqi: int | None = None
    air_quality_category: str | None = None
    if isinstance(air_result, BaseException):
        caveats.append(f"AQICN query failed: {type(air_result).__name__}")
    else:
        air = air_result or {}
        if "caveats" in air:
            caveats.extend(air["caveats"])
        else:
            air_quality_aqi = air.get("aqi")
            air_quality_category = air.get("aqi_category")

    risk_level = _risk_from_activity(recent_earthquakes_30d, max_magnitude_30d)

    return {
        "location": location,
        "earthquake_risk_level": risk_level,
        "recent_earthquakes_30d": recent_earthquakes_30d,
        "max_magnitude_30d": max_magnitude_30d,
        "typhoon_signal_active": typhoon_signal_active,
        "active_typhoon_name": active_typhoon_name,
        "air_quality_aqi": air_quality_aqi,
        "air_quality_category": air_quality_category,
        "assessment_datetime": retrieved_at.isoformat(),
        "caveats": caveats,
        "note": (
            "earthquake_risk_level is derived from recent seismic activity, "
            "not an official PHIVOLCS hazard assessment. For emergencies refer "
            "to ndrrmc.gov.ph and official PHIVOLCS/PAGASA channels."
        ),
        "source": "PHIVOLCS + PAGASA + AQICN",
    }
