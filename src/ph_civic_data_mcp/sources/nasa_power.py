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
    """Daily solar irradiance + climate variables from NASA POWER for any coordinate.

    Returns daily all-sky surface shortwave irradiance (kWh/m²/day), 2m temperature (°C),
    corrected precipitation (mm/day), and 2m wind speed (m/s). Useful for solar energy
    siting, agricultural planning, and historical climate analysis.

    Args:
        latitude: Decimal degrees, WGS84.
        longitude: Decimal degrees, WGS84.
        start_date: ISO date string (YYYY-MM-DD). Defaults to 14 days ago.
        end_date: ISO date string (YYYY-MM-DD). Defaults to today.
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

    if sd > ed:
        sd, ed = ed, sd

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
