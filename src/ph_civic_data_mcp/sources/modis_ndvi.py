"""NASA MODIS MOD13Q1 vegetation indices via ORNL DAAC Global Subsets REST.

250m NDVI + EVI at any lat/lng as a 16-day composite time series. No auth.
https://modis.ornl.gov/data/modis_webservice.html
"""

from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta, timezone

from ph_civic_data_mcp.models.climate import VegetationIndex, VegetationSample
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

ORNL_BASE = "https://modis.ornl.gov/rst/api/v1"
PRODUCT = "MOD13Q1"
NDVI_BAND = "_250m_16_days_NDVI"
EVI_BAND = "_250m_16_days_EVI"
NDVI_SCALE = 0.0001  # MOD13Q1 scale factor


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _date_to_modis(d: date_cls) -> str:
    return d.strftime("A%Y%j")


def _modis_to_date(s: str) -> str:
    """Keep the raw 'A2026081' composite label — users can decode if needed."""
    return s


async def _fetch_subset(latitude: float, longitude: float, start: str, end: str, band: str) -> dict | None:
    url = f"{ORNL_BASE}/{PRODUCT}/subset"
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "band": band,
        "startDate": start,
        "endDate": end,
        "kmAboveBelow": 0,
        "kmLeftRight": 0,
    }
    try:
        response = await fetch_with_retry(
            CLIENT, "GET", url, params=params, headers={"Accept": "application/json"}
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        log_stderr(f"MODIS ORNL error ({band}): {exc}")
        return None


@mcp.tool()
async def get_vegetation_index(
    latitude: float,
    longitude: float,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """NASA MODIS MOD13Q1 NDVI + EVI vegetation index at any coordinate.

    NDVI (Normalized Difference Vegetation Index) ranges -1..1. Higher values
    indicate denser healthy vegetation. EVI is more sensitive in high-biomass
    areas. Composite period is 16 days at 250m resolution. Useful for
    agricultural monitoring, deforestation tracking, drought stress indicators.

    Args:
        latitude: Decimal degrees, WGS84.
        longitude: Decimal degrees, WGS84.
        start_date: ISO date (YYYY-MM-DD). Defaults to ~90 days ago.
        end_date: ISO date (YYYY-MM-DD). Defaults to today.
    """
    today = _now().date()
    try:
        sd = date_cls.fromisoformat(start_date) if start_date else today - timedelta(days=90)
    except ValueError:
        sd = today - timedelta(days=90)
    try:
        ed = date_cls.fromisoformat(end_date) if end_date else today
    except ValueError:
        ed = today
    if sd > ed:
        sd, ed = ed, sd

    ckey = cache_key(
        {"tool": "modis", "lat": latitude, "lng": longitude, "sd": sd.isoformat(), "ed": ed.isoformat()}
    )
    cache = CACHES["modis_ndvi"]
    if ckey in cache:
        return cache[ckey]

    start_m = _date_to_modis(sd)
    end_m = _date_to_modis(ed)

    ndvi_payload = await _fetch_subset(latitude, longitude, start_m, end_m, NDVI_BAND.lstrip("_"))
    evi_payload = await _fetch_subset(latitude, longitude, start_m, end_m, EVI_BAND.lstrip("_"))

    samples: dict[str, VegetationSample] = {}
    for payload in (ndvi_payload, evi_payload):
        if not payload:
            continue
        subset = payload.get("subset", []) or []
        for entry in subset:
            composite_date = entry.get("calendar_date") or entry.get("modis_date")
            if not composite_date:
                continue
            raw = entry.get("data") or []
            if not raw:
                continue
            try:
                raw_val = float(raw[0])
            except (TypeError, ValueError):
                continue
            if raw_val <= -3000:
                continue
            value = raw_val * NDVI_SCALE
            band_name = entry.get("band", "")
            sample = samples.setdefault(
                composite_date, VegetationSample(composite_date=composite_date)
            )
            if "NDVI" in band_name.upper():
                sample.ndvi = round(value, 4)
            elif "EVI" in band_name.upper():
                sample.evi = round(value, 4)

    ordered = sorted(samples.values(), key=lambda s: s.composite_date)

    result = VegetationIndex(
        latitude=latitude,
        longitude=longitude,
        product=PRODUCT,
        band=f"NDVI+EVI (250m, 16-day composite)",
        samples=ordered,
        data_retrieved_at=_now(),
    ).model_dump(mode="json")

    if not ordered:
        result["caveats"] = [
            "No MODIS composites returned. Pixel may be over water, or the date "
            "range may not include a completed 16-day composite."
        ]

    cache[ckey] = result
    return result
