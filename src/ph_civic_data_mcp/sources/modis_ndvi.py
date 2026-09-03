"""NASA MODIS MOD13Q1 vegetation indices via ORNL DAAC Global Subsets REST.

250m NDVI + EVI at any lat/lng as a 16-day composite time series. No auth.
https://modis.ornl.gov/data/modis_webservice.html
"""

from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta, timezone

from ph_civic_data_mcp.models.climate import VegetationIndex, VegetationSample
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import DATA_STATUS_UNAVAILABLE, failure_result
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

ORNL_BASE = "https://modis.ornl.gov/rst/api/v1"
PRODUCT = "MOD13Q1"
NDVI_BAND = "_250m_16_days_NDVI"
EVI_BAND = "_250m_16_days_EVI"
NDVI_SCALE = 0.0001  # MOD13Q1 scale factor

# Same cap nasa_power.py uses. With no cap, a 45-year span went to both band
# queries at once, one request each holding the full ORNL time series.
MAX_SPAN_DAYS = 366


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _date_to_modis(d: date_cls) -> str:
    return d.strftime("A%Y%j")


def _modis_to_date(s: str) -> str:
    """Keep the raw 'A2026081' composite label — users can decode if needed."""
    return s


class MODISUpstreamError(RuntimeError):
    """An ORNL MODIS subset fetch failed, or sent a body with no dimensions.

    Raised so a transient outage can never enter the 24h TTL cache as if it
    were a real "no composite over this pixel" answer.
    """


async def _fetch_subset(latitude: float, longitude: float, start: str, end: str, band: str) -> dict:
    """One band's MODIS subset. Raises MODISUpstreamError on a bad fetch.

    A subset with no composites in it (`{"subset": []}`) is a real, cacheable
    answer: the pixel may be over water, or the window has no completed
    16-day composite. A transport failure, a non-object body, or a body with
    no list `subset` field raises instead.
    """
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
        payload = response.json()
    except Exception as exc:
        log_stderr(f"MODIS ORNL error ({band}): {exc}")
        raise MODISUpstreamError(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise MODISUpstreamError(f"ORNL returned a non-object body for band {band!r}")
    if not isinstance(payload.get("subset"), list):
        raise MODISUpstreamError(f"ORNL returned no subset list for band {band!r}: {payload!r}")
    return payload


def _parse_band_entries(band: str, payload: dict) -> list[tuple[str, str, float]]:
    """(composite_date, band_name, raw_value) tuples for one band's subset.

    Raises MODISUpstreamError when the subset has rows but not one of them
    parses to a number. Every value coming back as a non-numeric string is
    malformed upstream data, not a real "no vegetation here" answer. A fill
    value (raw_val <= -3000, a cloud or water mask) still parses fine here,
    so an all-fill window stays the genuine empty success it already was.
    """
    subset = payload.get("subset", []) or []
    rows_with_data = 0
    entries: list[tuple[str, str, float]] = []
    for entry in subset:
        composite_date = entry.get("calendar_date") or entry.get("modis_date")
        if not composite_date:
            continue
        raw = entry.get("data") or []
        if not raw:
            continue
        rows_with_data += 1
        try:
            raw_val = float(raw[0])
        except (TypeError, ValueError):
            continue
        entries.append((composite_date, entry.get("band", ""), raw_val))
    if rows_with_data and not entries:
        raise MODISUpstreamError(
            f"ORNL sent {rows_with_data} row(s) for band {band!r} with no numeric value in any of them."
        )
    return entries


@mcp.tool(
    title="MODIS vegetation index at a point",
    tags={"modis", "nasa", "philippines", "vegetation"},
    annotations={
        "title": "MODIS vegetation index at a point",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_vegetation_index(
    latitude: float,
    longitude: float,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """NASA MODIS MOD13Q1 NDVI and EVI vegetation index at any coordinate.

    NDVI (Normalized Difference Vegetation Index) ranges -1 to 1. Higher
    values indicate denser healthy vegetation. EVI is more sensitive in
    high-biomass areas. The composite period is 16 days at 250m resolution.
    Useful for farm monitoring, deforestation tracking, and drought checks. Examples:

      get_vegetation_index(15.58, 121.0)                              # last ~90 days at a point in Isabela
      get_vegetation_index(15.58, 121.0, "2026-08-01", "2026-08-15")  # a fixed 15-day window

    On failure: both MODIS bands failing returns data_status "unavailable",
    upstream_error true, samples [], and both band errors in caveats. One
    band failing still returns the other band's samples, but data_status
    stays "unavailable" with upstream_error true. A band whose rows carry no
    numeric value counts as that band failing. A pixel with no composite in
    range, such as one over water, is a real success with samples []. A
    latitude or longitude out of range returns data_status "invalid_request",
    with validation_error true and samples []. A start_date or end_date that
    does not parse as YYYY-MM-DD, or a span over 366 days, also returns
    data_status "invalid_request", checked before any fetch.

    Args:
        latitude: Decimal degrees, WGS84.
        longitude: Decimal degrees, WGS84.
        start_date: ISO date (YYYY-MM-DD). Defaults to ~90 days ago. The
                    span from start_date to end_date cannot exceed 366 days.
        end_date: ISO date (YYYY-MM-DD). Defaults to today.
    """
    if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
        return failure_result(
            "NASA MODIS via ORNL DAAC",
            f"{ORNL_BASE}/{PRODUCT}/subset",
            f"latitude {latitude} or longitude {longitude} is out of range. "
            "Latitude must be -90 to 90. Longitude must be -180 to 180.",
            validation_error=True,
            latitude=latitude,
            longitude=longitude,
            product=PRODUCT,
            band="NDVI+EVI (250m, 16-day composite)",
            samples=[],
        )

    today = _now().date()
    # Codex cross-model finding: a start_date or end_date that failed
    # date.fromisoformat used to fall back to the default window and cache a
    # normal-looking result for dates nobody asked for. Reject it instead,
    # before any fetch, the way nasa_power.py does.
    if start_date is not None:
        try:
            sd = date_cls.fromisoformat(start_date)
        except ValueError:
            return failure_result(
                "NASA MODIS via ORNL DAAC",
                f"{ORNL_BASE}/{PRODUCT}/subset",
                f"start_date {start_date!r} is not a valid YYYY-MM-DD date.",
                validation_error=True,
                latitude=latitude,
                longitude=longitude,
                product=PRODUCT,
                band="NDVI+EVI (250m, 16-day composite)",
                samples=[],
            )
    else:
        sd = today - timedelta(days=90)
    if end_date is not None:
        try:
            ed = date_cls.fromisoformat(end_date)
        except ValueError:
            return failure_result(
                "NASA MODIS via ORNL DAAC",
                f"{ORNL_BASE}/{PRODUCT}/subset",
                f"end_date {end_date!r} is not a valid YYYY-MM-DD date.",
                validation_error=True,
                latitude=latitude,
                longitude=longitude,
                product=PRODUCT,
                band="NDVI+EVI (250m, 16-day composite)",
                samples=[],
            )
    else:
        ed = today
    if sd > ed:
        sd, ed = ed, sd

    # Same 366-day cap nasa_power.py uses, checked before any band fetch.
    span_days = (ed - sd).days
    if span_days > MAX_SPAN_DAYS:
        return failure_result(
            "NASA MODIS via ORNL DAAC",
            f"{ORNL_BASE}/{PRODUCT}/subset",
            f"Requested span is {span_days} days. The cap is {MAX_SPAN_DAYS} days. "
            "Narrow start_date and end_date.",
            validation_error=True,
            latitude=latitude,
            longitude=longitude,
            product=PRODUCT,
            band="NDVI+EVI (250m, 16-day composite)",
            samples=[],
        )

    ckey = cache_key(
        {
            "tool": "modis",
            "lat": latitude,
            "lng": longitude,
            "sd": sd.isoformat(),
            "ed": ed.isoformat(),
        }
    )
    cache = CACHES["modis_ndvi"]
    if ckey in cache:
        return cache[ckey]

    start_m = _date_to_modis(sd)
    end_m = _date_to_modis(ed)

    band_errors: list[str] = []
    payloads: list[tuple[str, dict]] = []
    for band in (NDVI_BAND.lstrip("_"), EVI_BAND.lstrip("_")):
        try:
            payloads.append((band, await _fetch_subset(latitude, longitude, start_m, end_m, band)))
        except MODISUpstreamError as exc:
            band_errors.append(str(exc))

    if not payloads:
        # Both bands failed. Never cache: a transient outage must not pin "no
        # vegetation here" for the 24h TTL.
        return failure_result(
            "NASA MODIS via ORNL DAAC",
            f"{ORNL_BASE}/{PRODUCT}/subset",
            f"MODIS ORNL subset failed for both bands: {'; '.join(band_errors)}",
            latitude=latitude,
            longitude=longitude,
            product=PRODUCT,
            band="NDVI+EVI (250m, 16-day composite)",
            samples=[],
        )

    samples: dict[str, VegetationSample] = {}
    for band, payload in payloads:
        try:
            entries = _parse_band_entries(band, payload)
        except MODISUpstreamError as exc:
            band_errors.append(str(exc))
            continue
        for composite_date, band_name, raw_val in entries:
            if raw_val <= -3000:
                continue
            value = raw_val * NDVI_SCALE
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
        band="NDVI+EVI (250m, 16-day composite)",
        samples=ordered,
        data_retrieved_at=_now(),
    ).model_dump(mode="json")

    caveats: list[str] = list(band_errors)
    if not ordered:
        caveats.append(
            "No MODIS composites returned. Pixel may be over water, or the date "
            "range may not include a completed 16-day composite."
        )
    if caveats:
        result["caveats"] = caveats

    if band_errors:
        # One band failed while the other came back. The samples that did
        # arrive are real, but this is a partial answer, so it must not
        # enter the cache under the missing band's data.
        result["data_status"] = DATA_STATUS_UNAVAILABLE
        result["upstream_error"] = True
        return result

    cache[ckey] = result
    return result
