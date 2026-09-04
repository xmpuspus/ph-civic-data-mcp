"""Open-Meteo Flood API: daily river discharge from the GloFAS model, no auth.

Matches the existing Open-Meteo client pattern (open_meteo_aq.py): a fetch
helper that raises on any non-success, a parse step that treats a malformed
200 body as indeterminate, and a tool that returns through failure_result.
https://open-meteo.com/en/docs/flood-api
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_INDETERMINATE,
    DATA_STATUS_SUCCESS,
    failure_result,
)
from ph_civic_data_mcp.utils.geo import GeoResolveError, resolve_to_coords
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

OPEN_METEO_FLOOD_URL = "https://flood-api.open-meteo.com/v1/flood"
OPEN_METEO_FLOOD_SOURCE = "Open-Meteo Flood API (GloFAS), CC BY 4.0"

# The upstream field caps forecast_days at 210 and past_days at 730. A
# response that wide is not useful for a chat agent and blows past the
# offline-fixture size cap, so this tool holds a much smaller range.
MIN_FORECAST_DAYS = 1
MAX_FORECAST_DAYS = 30
MIN_PAST_DAYS = 0
MAX_PAST_DAYS = 30

RIVER_DISCHARGE_NOTE = (
    "River discharge is a GloFAS model value for the nearest river cell, "
    "not a gauge reading. Treat it as one signal among many for flood risk."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _to_float(val: object) -> float | None:
    # float() accepts "NaN" and "inf", which are not JSON numbers and would
    # publish an unusable discharge as a success. Same rule as world_bank.
    if val is None or isinstance(val, bool):
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _bad_day_count(value: object, low: int, high: int) -> bool:
    """True for anything but a plain int inside [low, high].

    A bool is a subclass of int in Python, so a caller-supplied True or
    False must be rejected by name, before the range check runs.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return True
    return not (low <= value <= high)


@mcp.tool(
    title="River flood forecast for a Philippine location",
    tags={"flood", "hazards", "hydrology", "open-meteo", "philippines"},
    annotations={
        "title": "River flood forecast for a Philippine location",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_flood_forecast(location: str, forecast_days: int = 7, past_days: int = 0) -> dict:
    """Daily river discharge forecast for a Philippine place, from Open-Meteo's GloFAS model.

    Returns a daily river_discharge_m3s series for the nearest river cell to
    the resolved location, plus its max and min bounds, in cubic meters per
    second. This is a model estimate, not a gauge reading, so treat it as
    one signal among many for flood risk. Examples:

      get_flood_forecast("Cagayan de Oro")                          # 7-day default forecast
      get_flood_forecast("Marikina", forecast_days=14, past_days=3)  # 14 days ahead, 3 days back

    On failure: an unresolved location, or a forecast_days or past_days
    value that is not a whole number or is out of range, returns
    data_status "invalid_request", with validation_error true and days [].
    A PSGC outage during location resolution, or an Open-Meteo fetch
    failure, returns data_status "unavailable", with upstream_error true
    and days []. A response with no readable daily.time entries, or a
    river_discharge list shorter than daily.time (including an empty
    list), returns data_status "indeterminate", with upstream_error true,
    days [], and is never cached.

    Args:
        location: City, municipality, or province name.
        forecast_days: Days ahead to forecast, from 1 to 30. Default 7.
        past_days: Days of past discharge to include, from 0 to 30. Default 0.
    """
    if _bad_day_count(forecast_days, MIN_FORECAST_DAYS, MAX_FORECAST_DAYS):
        return failure_result(
            OPEN_METEO_FLOOD_SOURCE,
            OPEN_METEO_FLOOD_URL,
            f"forecast_days must be a whole number from {MIN_FORECAST_DAYS} to "
            f"{MAX_FORECAST_DAYS}, got {forecast_days!r}.",
            validation_error=True,
            location=location,
            days=[],
        )
    if _bad_day_count(past_days, MIN_PAST_DAYS, MAX_PAST_DAYS):
        return failure_result(
            OPEN_METEO_FLOOD_SOURCE,
            OPEN_METEO_FLOOD_URL,
            f"past_days must be a whole number from {MIN_PAST_DAYS} to "
            f"{MAX_PAST_DAYS}, got {past_days!r}.",
            validation_error=True,
            location=location,
            days=[],
        )

    ckey = cache_key(
        {
            "tool": "flood",
            "loc": location.strip().lower() if location else location,
            "forecast_days": forecast_days,
            "past_days": past_days,
        }
    )
    cache = CACHES["open_meteo_flood"]
    if ckey in cache:
        return cache[ckey]

    try:
        coords = await resolve_to_coords(location)
    except GeoResolveError as exc:
        log_stderr(f"get_flood_forecast PSGC resolve error: {exc}")
        return failure_result(
            OPEN_METEO_FLOOD_SOURCE,
            OPEN_METEO_FLOOD_URL,
            f"PSGC location resolution failed ({exc}). Could not resolve '{location}'.",
            location=location,
            days=[],
        )
    if coords is None:
        return failure_result(
            OPEN_METEO_FLOOD_SOURCE,
            OPEN_METEO_FLOOD_URL,
            f"No coordinates known for '{location}'. Try a major PH city or province.",
            validation_error=True,
            location=location,
            days=[],
        )
    lat, lng = coords

    params = {
        "latitude": lat,
        "longitude": lng,
        "daily": "river_discharge,river_discharge_max,river_discharge_min",
        "timezone": "UTC",
        "forecast_days": forecast_days,
        "past_days": past_days,
    }

    try:
        response = await fetch_with_retry(CLIENT, "GET", OPEN_METEO_FLOOD_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(
                f"Open-Meteo Flood returned a non-object body: {type(payload).__name__}"
            )
    except Exception as exc:
        log_stderr(f"Open-Meteo Flood error: {exc}")
        return failure_result(
            OPEN_METEO_FLOOD_SOURCE,
            OPEN_METEO_FLOOD_URL,
            f"Open-Meteo Flood fetch failed: {type(exc).__name__}: {exc}",
            location=location,
            latitude=lat,
            longitude=lng,
            days=[],
        )

    daily = _as_dict(payload.get("daily"))
    times = daily.get("time")
    if not isinstance(times, list) or not times:
        # A scalar, missing, or empty daily.time is not a real forecast body.
        return failure_result(
            OPEN_METEO_FLOOD_SOURCE,
            OPEN_METEO_FLOOD_URL,
            f"Open-Meteo Flood sent no usable daily.time series: {times!r}.",
            data_status=DATA_STATUS_INDETERMINATE,
            location=location,
            latitude=lat,
            longitude=lng,
            days=[],
        )

    discharge = daily.get("river_discharge")
    if not isinstance(discharge, list):
        # Dates without the discharge series is a malformed body, not a
        # forecast of nulls. Caching it would serve empty numbers for an hour.
        return failure_result(
            OPEN_METEO_FLOOD_SOURCE,
            OPEN_METEO_FLOOD_URL,
            f"Open-Meteo Flood sent daily.time but no river_discharge list: {discharge!r}.",
            data_status=DATA_STATUS_INDETERMINATE,
            location=location,
            latitude=lat,
            longitude=lng,
            days=[],
        )
    if len(discharge) < len(times):
        # An empty or short river_discharge list still passes isinstance(list),
        # so without a length check it caches a forecast of trailing nulls.
        # river_discharge_max and river_discharge_min may stay short or
        # absent: the API omits them when the caller does not ask for them.
        return failure_result(
            OPEN_METEO_FLOOD_SOURCE,
            OPEN_METEO_FLOOD_URL,
            f"Open-Meteo Flood sent {len(times)} date(s) but only "
            f"{len(discharge)} river_discharge value(s).",
            data_status=DATA_STATUS_INDETERMINATE,
            location=location,
            latitude=lat,
            longitude=lng,
            days=[],
        )
    discharge_max = daily.get("river_discharge_max")
    discharge_min = daily.get("river_discharge_min")
    discharge_max = discharge_max if isinstance(discharge_max, list) else []
    discharge_min = discharge_min if isinstance(discharge_min, list) else []

    days: list[dict] = []
    for i, raw_date in enumerate(times):
        if not isinstance(raw_date, str) or not raw_date:
            continue
        days.append(
            {
                "date": raw_date,
                "river_discharge_m3s": _to_float(discharge[i] if i < len(discharge) else None),
                "river_discharge_max_m3s": _to_float(
                    discharge_max[i] if i < len(discharge_max) else None
                ),
                "river_discharge_min_m3s": _to_float(
                    discharge_min[i] if i < len(discharge_min) else None
                ),
            }
        )

    if not days:
        # times held only scalars or empty strings: a nonempty body that
        # still yields zero readable days is drift, never a cached [].
        return failure_result(
            OPEN_METEO_FLOOD_SOURCE,
            OPEN_METEO_FLOOD_URL,
            "Open-Meteo Flood sent a daily.time series with no readable dates.",
            data_status=DATA_STATUS_INDETERMINATE,
            location=location,
            latitude=lat,
            longitude=lng,
            days=[],
        )

    daily_units = _as_dict(payload.get("daily_units"))
    units = {
        "river_discharge_m3s": daily_units.get("river_discharge", "m³/s"),
        "river_discharge_max_m3s": daily_units.get("river_discharge_max", "m³/s"),
        "river_discharge_min_m3s": daily_units.get("river_discharge_min", "m³/s"),
    }

    result = {
        "location": location,
        "latitude": _to_float(payload.get("latitude")) or lat,
        "longitude": _to_float(payload.get("longitude")) or lng,
        "days": days,
        "units": units,
        "forecast_days": forecast_days,
        "past_days": past_days,
        "data_status": DATA_STATUS_SUCCESS,
        "upstream_error": False,
        "validation_error": False,
        "caveats": [],
        "note": RIVER_DISCHARGE_NOTE,
        "source": OPEN_METEO_FLOOD_SOURCE,
        "source_url": OPEN_METEO_FLOOD_URL,
        "data_retrieved_at": _now().isoformat(),
    }
    cache[ckey] = result
    return result
