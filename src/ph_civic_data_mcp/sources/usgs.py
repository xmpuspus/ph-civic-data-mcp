"""USGS FDSN earthquake API — global catalog filtered to Philippine bbox.

Cross-reference to PHIVOLCS. USGS publishes Mww/Mwc solutions that sometimes
differ from PHIVOLCS's local magnitude; having both lets agents reconcile.
https://earthquake.usgs.gov/fdsnws/event/1/
"""

from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt

from ph_civic_data_mcp.models.climate import USGSEarthquake
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import failure_envelope, failure_result
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# Philippine Area of Responsibility bbox (approximate)
PH_BBOX = {
    "minlatitude": 4.0,
    "maxlatitude": 22.0,
    "minlongitude": 115.0,
    "maxlongitude": 130.0,
}

# A radius query fetches this many candidates from USGS (the API's own hard
# cap) before the haversine filter runs, instead of the caller's `limit`.
# Asking USGS for only the caller's `limit` first would return the most
# recent N events across the whole PH bbox, most of which a tight radius
# then throws away, leaving far fewer than `limit` results.
RADIUS_CANDIDATE_POOL = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points.

    Local to this module because utils/geo.py belongs to another change in
    this release. usgs.py and phivolcs.py each keep a copy, the same way
    both already keep their own `_now()`.
    """
    earth_radius_km = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
    return 2 * earth_radius_km * atan2(sqrt(a), sqrt(1 - a))


def _parse_event(feature: dict) -> USGSEarthquake | None:
    props = feature.get("properties") or {}
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None

    time_ms = props.get("time")
    if time_ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(time_ms) / 1000.0, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None

    mag = props.get("mag")
    if mag is None:
        return None

    return USGSEarthquake(
        datetime_utc=dt,
        magnitude=float(mag),
        magnitude_type=props.get("magType"),
        depth_km=float(coords[2]) if len(coords) >= 3 and coords[2] is not None else None,
        latitude=float(coords[1]),
        longitude=float(coords[0]),
        place=props.get("place") or "Philippine region",
        usgs_event_id=feature.get("id", ""),
        felt_reports=props.get("felt"),
        tsunami=bool(props.get("tsunami")),
        url=props.get("url", ""),
        data_retrieved_at=_now(),
    )


@mcp.tool(
    title="USGS earthquakes near the Philippines",
    tags={"earthquake", "hazard", "philippines", "usgs"},
    annotations={
        "title": "USGS earthquakes near the Philippines",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_usgs_earthquakes_ph(
    start_date: str | None = None,
    end_date: str | None = None,
    min_magnitude: float = 4.0,
    limit: int = 50,
    center_lat: float | None = None,
    center_lon: float | None = None,
    radius_km: float | None = None,
) -> list[dict] | dict:
    """Philippine-region earthquakes from USGS, cross-reference to PHIVOLCS.

    Returns events inside the PH bounding box (lat 4..22, lng 115..130) that
    USGS has catalogued, including international-standard Mww/Mwc magnitudes
    and depth solutions. Complements PHIVOLCS with global-network analysis.

    Args:
        start_date: ISO date (YYYY-MM-DD). Defaults to 30 days ago.
        end_date: ISO date (YYYY-MM-DD). Defaults to today.
        min_magnitude: Minimum magnitude (default 4.0 to keep noise low).
        limit: Max events to return (default 50, USGS hard-caps at 20000).
        center_lat: Latitude of a search point. Give with center_lon and
                    radius_km to filter to events near one place instead of
                    the whole PH bbox.
        center_lon: Longitude of a search point. Give with center_lat and
                    radius_km.
        radius_km: Keep only events within this distance of
                   (center_lat, center_lon). Give all three of center_lat,
                   center_lon, and radius_km together, or none of them. Each
                   returned event then carries a distance_km field.
    """
    have = (center_lat is not None, center_lon is not None, radius_km is not None)
    if any(have) and not all(have):
        return failure_result(
            "USGS",
            USGS_URL,
            "center_lat, center_lon, and radius_km must all be given together, or all left out.",
            license="Public domain (USGS)",
            validation_error=True,
            results=[],
        )
    if radius_km is not None and radius_km <= 0:
        return failure_result(
            "USGS",
            USGS_URL,
            "radius_km must be a positive number.",
            license="Public domain (USGS)",
            validation_error=True,
            results=[],
        )

    today = _now().date()
    try:
        sd = date_cls.fromisoformat(start_date) if start_date else today - timedelta(days=30)
    except ValueError:
        sd = today - timedelta(days=30)
    try:
        ed = date_cls.fromisoformat(end_date) if end_date else today
    except ValueError:
        ed = today
    if sd > ed:
        sd, ed = ed, sd

    limit = max(1, min(int(limit), 500))
    use_radius = radius_km is not None

    ckey = cache_key(
        {
            "tool": "usgs",
            "sd": sd.isoformat(),
            "ed": ed.isoformat(),
            "mag": min_magnitude,
            "limit": limit,
            "center_lat": center_lat,
            "center_lon": center_lon,
            "radius_km": radius_km,
        }
    )
    cache = CACHES["usgs_events"]
    if ckey in cache:
        return cache[ckey]

    params = {
        "format": "geojson",
        "starttime": sd.isoformat(),
        "endtime": ed.isoformat(),
        "minmagnitude": min_magnitude,
        "orderby": "time",
    }
    if use_radius:
        # USGS rejects a query that mixes a rectangle with a circle, so a
        # radius search drops PH_BBOX and asks for a wide candidate pool
        # instead of `limit`; the haversine filter below narrows it back
        # down to `limit` after distance is known.
        params["latitude"] = center_lat
        params["longitude"] = center_lon
        params["maxradiuskm"] = radius_km
        params["limit"] = RADIUS_CANDIDATE_POOL
    else:
        params["limit"] = limit
        params.update(PH_BBOX)

    try:
        response = await fetch_with_retry(CLIENT, "GET", USGS_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        log_stderr(f"USGS error: {exc}")
        return failure_envelope(
            "USGS",
            USGS_URL,
            f"USGS FDSN API unavailable ({type(exc).__name__}: {exc}). "
            "This is an upstream failure, not an absence of earthquakes.",
            license="Public domain (USGS)",
        )

    features = payload.get("features") or []
    events: list[dict] = []
    for feature in features:
        event = _parse_event(feature)
        if event is None:
            continue
        data = event.model_dump(mode="json")
        if use_radius:
            distance_km = _haversine_km(center_lat, center_lon, event.latitude, event.longitude)
            if distance_km > radius_km:
                continue
            data["distance_km"] = round(distance_km, 2)
        events.append(data)
        if use_radius and len(events) >= limit:
            break

    cache[ckey] = events
    return events
