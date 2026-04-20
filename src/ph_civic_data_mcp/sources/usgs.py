"""USGS FDSN earthquake API — global catalog filtered to Philippine bbox.

Cross-reference to PHIVOLCS. USGS publishes Mww/Mwc solutions that sometimes
differ from PHIVOLCS's local magnitude; having both lets agents reconcile.
https://earthquake.usgs.gov/fdsnws/event/1/
"""

from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta, timezone

from ph_civic_data_mcp.models.climate import USGSEarthquake
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

USGS_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# Philippine Area of Responsibility bbox (approximate)
PH_BBOX = {
    "minlatitude": 4.0,
    "maxlatitude": 22.0,
    "minlongitude": 115.0,
    "maxlongitude": 130.0,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


@mcp.tool()
async def get_usgs_earthquakes_ph(
    start_date: str | None = None,
    end_date: str | None = None,
    min_magnitude: float = 4.0,
    limit: int = 50,
) -> list[dict]:
    """Philippine-region earthquakes from USGS, cross-reference to PHIVOLCS.

    Returns events inside the PH bounding box (lat 4..22, lng 115..130) that
    USGS has catalogued, including international-standard Mww/Mwc magnitudes
    and depth solutions. Complements PHIVOLCS with global-network analysis.

    Args:
        start_date: ISO date (YYYY-MM-DD). Defaults to 30 days ago.
        end_date: ISO date (YYYY-MM-DD). Defaults to today.
        min_magnitude: Minimum magnitude (default 4.0 to keep noise low).
        limit: Max events to return (default 50, USGS hard-caps at 20000).
    """
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

    ckey = cache_key(
        {
            "tool": "usgs",
            "sd": sd.isoformat(),
            "ed": ed.isoformat(),
            "mag": min_magnitude,
            "limit": limit,
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
        "limit": limit,
        "orderby": "time",
        **PH_BBOX,
    }

    try:
        response = await fetch_with_retry(CLIENT, "GET", USGS_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        log_stderr(f"USGS error: {exc}")
        return []

    features = payload.get("features") or []
    events: list[dict] = []
    for feature in features:
        event = _parse_event(feature)
        if event is not None:
            events.append(event.model_dump(mode="json"))

    cache[ckey] = events
    return events
