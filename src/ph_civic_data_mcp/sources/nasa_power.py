"""NASA POWER — Prediction Of Worldwide Energy Resources.

Daily solar irradiance + climate point data, 1981→present. No auth.
https://power.larc.nasa.gov/docs/services/api/temporal/daily/
"""

from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta, timezone

from ph_civic_data_mcp.models.climate import SolarClimate, SolarClimateDay
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import failure_result
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

NASA_POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

PARAMETERS = "ALLSKY_SFC_SW_DWN,T2M,PRECTOTCORR,WS2M"

# A span with no cap let "1981-01-01" to today return 16,683 daily rows in
# one response. NASA POWER's own daily coverage starts in 1981, so a year is
# generous for any solar-site or historical-climate check this tool serves.
MAX_SPAN_DAYS = 366


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_yyyymmdd(d: date_cls) -> str:
    return d.strftime("%Y%m%d")


def _yyyymmdd_to_date(s: str) -> date_cls | None:
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        return None


def _sanitize(val: float | None) -> float | None:
    if val is None:
        return None
    if val <= -999:
        return None
    return float(val)


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


@mcp.tool(
    title="Solar irradiance and climate at a point",
    tags={"climate", "nasa", "philippines", "solar"},
    annotations={
        "title": "Solar irradiance and climate at a point",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_solar_and_climate(
    latitude: float,
    longitude: float,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Daily solar irradiance and climate variables from NASA POWER for any coordinate.

    Returns daily solar irradiance, 2m temperature, corrected precipitation,
    and 2m wind speed, in kWh per m2, Celsius, mm, and m per second. Useful
    for solar-site screening, farm planning, and historical climate checks. Examples:

      get_solar_and_climate(14.5995, 120.9842)                              # Manila, last 14 days
      get_solar_and_climate(14.5995, 120.9842, "2026-04-01", "2026-04-02")  # a fixed 2-day window

    On failure: an upstream fetch failure or a malformed response body
    returns data_status "unavailable", with upstream_error true, days [],
    and the real error text in caveats. A bad start_date or end_date string
    falls back to the default window instead of failing. An end_date before
    start_date, or a span over 366 days, returns data_status
    "invalid_request", with validation_error true and days [].

    Args:
        latitude: Decimal degrees, WGS84.
        longitude: Decimal degrees, WGS84.
        start_date: ISO date string (YYYY-MM-DD). Defaults to 14 days ago.
        end_date: ISO date string (YYYY-MM-DD). Defaults to today. The span
                  from start_date to end_date cannot exceed 366 days.
    """
    today = _now().date()
    try:
        sd = date_cls.fromisoformat(start_date) if start_date else today - timedelta(days=14)
    except ValueError:
        sd = today - timedelta(days=14)
    try:
        ed = date_cls.fromisoformat(end_date) if end_date else today
    except ValueError:
        ed = today

    if ed < sd:
        return failure_result(
            "NASA POWER",
            NASA_POWER_URL,
            f"end_date {ed.isoformat()} is before start_date {sd.isoformat()}. "
            "Swap the two dates and try again.",
            validation_error=True,
            latitude=latitude,
            longitude=longitude,
            start_date=sd.isoformat(),
            end_date=ed.isoformat(),
            days=[],
        )

    span_days = (ed - sd).days
    if span_days > MAX_SPAN_DAYS:
        return failure_result(
            "NASA POWER",
            NASA_POWER_URL,
            f"Requested span is {span_days} days. The cap is {MAX_SPAN_DAYS} days. "
            "Narrow start_date and end_date.",
            validation_error=True,
            latitude=latitude,
            longitude=longitude,
            start_date=sd.isoformat(),
            end_date=ed.isoformat(),
            days=[],
        )

    ckey = cache_key(
        {
            "tool": "nasa_power",
            "lat": latitude,
            "lng": longitude,
            "sd": sd.isoformat(),
            "ed": ed.isoformat(),
        }
    )
    cache = CACHES["nasa_power"]
    if ckey in cache:
        return cache[ckey]

    params = {
        "parameters": PARAMETERS,
        "community": "RE",
        "latitude": latitude,
        "longitude": longitude,
        "start": _to_yyyymmdd(sd),
        "end": _to_yyyymmdd(ed),
        "format": "JSON",
    }

    try:
        response = await fetch_with_retry(CLIENT, "GET", NASA_POWER_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"NASA POWER returned a non-object body: {type(payload).__name__}")
    except Exception as exc:
        log_stderr(f"NASA POWER error: {exc}")
        return failure_result(
            "NASA POWER",
            NASA_POWER_URL,
            f"NASA POWER fetch failed: {type(exc).__name__}: {exc}",
            latitude=latitude,
            longitude=longitude,
            start_date=sd.isoformat(),
            end_date=ed.isoformat(),
            days=[],
        )

    properties = _as_dict(payload.get("properties"))
    data = _as_dict(properties.get("parameter"))
    solar = _as_dict(data.get("ALLSKY_SFC_SW_DWN"))
    t2m = _as_dict(data.get("T2M"))
    precip = _as_dict(data.get("PRECTOTCORR"))
    wind = _as_dict(data.get("WS2M"))

    dates = sorted(set(solar) | set(t2m) | set(precip) | set(wind))
    days: list[SolarClimateDay] = []
    for key in dates:
        d = _yyyymmdd_to_date(key)
        if d is None:
            continue
        days.append(
            SolarClimateDay(
                date=d,
                solar_irradiance_kwh_m2=_sanitize(solar.get(key)),
                temp_c=_sanitize(t2m.get(key)),
                precipitation_mm=_sanitize(precip.get(key)),
                windspeed_ms=_sanitize(wind.get(key)),
            )
        )

    result = SolarClimate(
        latitude=latitude,
        longitude=longitude,
        start_date=sd,
        end_date=ed,
        days=days,
        data_retrieved_at=_now(),
    ).model_dump(mode="json")
    cache[ckey] = result
    return result
