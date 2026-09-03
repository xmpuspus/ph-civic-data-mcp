"""USGS FDSN earthquake API — global catalog filtered to Philippine bbox.

Cross-reference to PHIVOLCS. USGS publishes Mww/Mwc solutions that sometimes
differ from PHIVOLCS's local magnitude; having both lets agents reconcile.
https://earthquake.usgs.gov/fdsnws/event/1/
"""

from __future__ import annotations

from datetime import date as date_cls, datetime, timedelta, timezone

from ph_civic_data_mcp.models.climate import USGSEarthquake
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_INDETERMINATE,
    failure_envelope,
    failure_result,
)
from ph_civic_data_mcp.utils.geo import haversine_km
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

# USGS's documented maximum for the maxradiuskm circle-search parameter.
MAX_RADIUS_KM = 20001.6


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _tsunami_flag(value: object) -> bool:
    """Coerce a USGS tsunami field to bool.

    USGS sends 0, 1, "0", "1", or a bool. Python's bool("0") is True, so a
    plain cast turned "no tsunami" into a tsunami. Treat 1, 1.0, "1", and
    True as True. Treat everything else, including a malformed value, as
    False with no crash.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value == "1"
    return False


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

    # A magnitude, depth, or coordinate that will not convert to a float
    # skips this one feature, the same way a missing mag does. Codex
    # cross-model finding: mag: "bad" used to raise ValueError out of the
    # whole query, so one malformed feature lost every event in the batch.
    try:
        magnitude = float(mag)
        depth_km = float(coords[2]) if len(coords) >= 3 and coords[2] is not None else None
        latitude = float(coords[1])
        longitude = float(coords[0])
    except (TypeError, ValueError):
        return None

    return USGSEarthquake(
        datetime_utc=dt,
        magnitude=magnitude,
        magnitude_type=props.get("magType"),
        depth_km=depth_km,
        latitude=latitude,
        longitude=longitude,
        place=props.get("place") or "Philippine region",
        usgs_event_id=feature.get("id", ""),
        felt_reports=props.get("felt"),
        tsunami=_tsunami_flag(props.get("tsunami")),
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
    USGS catalogs, including international-standard Mww/Mwc magnitudes and
    depth solutions. Complements PHIVOLCS with global-network analysis. Give
    center_lat, center_lon, and radius_km together to filter to one place,
    and each matched event then carries a distance_km field. Give all three
    together, or leave out all three. Examples:

      get_usgs_earthquakes_ph()                              last 30 days, magnitude 4.0+
      get_usgs_earthquakes_ph(start_date="2026-08-01", end_date="2026-08-31")
      get_usgs_earthquakes_ph(center_lat=14.5995, center_lon=120.9842, radius_km=50)  near Manila

    On failure: an invalid trio, an out-of-range radius_km, center_lat, or
    center_lon gives validation_error true and data_status "invalid_request".
    The same happens for a start_date or end_date that is not YYYY-MM-DD. An
    unreachable USGS API, or a payload that is not a GeoJSON
    FeatureCollection, gives upstream_error true and data_status
    "unavailable". A nonempty features list where every feature fails to
    parse gives upstream_error true and data_status "indeterminate", and is
    never cached. All three return results: [] with the real error in
    caveats.

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
    if radius_km is not None and not (0 < radius_km <= MAX_RADIUS_KM):
        return failure_result(
            "USGS",
            USGS_URL,
            f"radius_km must be above 0 and at most {MAX_RADIUS_KM}, got {radius_km}.",
            license="Public domain (USGS)",
            validation_error=True,
            results=[],
        )
    if center_lat is not None and not (-90.0 <= center_lat <= 90.0):
        return failure_result(
            "USGS",
            USGS_URL,
            f"center_lat must be between -90 and 90, got {center_lat}.",
            license="Public domain (USGS)",
            validation_error=True,
            results=[],
        )
    if center_lon is not None and not (-180.0 <= center_lon <= 180.0):
        return failure_result(
            "USGS",
            USGS_URL,
            f"center_lon must be between -180 and 180, got {center_lon}.",
            license="Public domain (USGS)",
            validation_error=True,
            results=[],
        )

    today = _now().date()
    if start_date is not None:
        try:
            sd = date_cls.fromisoformat(start_date)
        except ValueError:
            return failure_result(
                "USGS",
                USGS_URL,
                f"start_date {start_date!r} is not a valid YYYY-MM-DD date.",
                license="Public domain (USGS)",
                validation_error=True,
                results=[],
            )
    else:
        sd = today - timedelta(days=30)
    if end_date is not None:
        try:
            ed = date_cls.fromisoformat(end_date)
        except ValueError:
            return failure_result(
                "USGS",
                USGS_URL,
                f"end_date {end_date!r} is not a valid YYYY-MM-DD date.",
                license="Public domain (USGS)",
                validation_error=True,
                results=[],
            )
    else:
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

    # Codex cross-model finding: a 200 with a non-object body (a bare list, a
    # string) reached `.get()` outside the except block and crashed the tool
    # instead of returning the failure envelope.
    #
    # Codex cross-model finding: `payload.get("features", [])` reads a
    # missing key the same as an empty list, so a 200 with body `{}` passed
    # this guard, returned a bare `[]`, and cached that false all-clear for
    # 10 minutes. Require the key to be present, not just absent-or-a-list.
    if (
        not isinstance(payload, dict)
        or "features" not in payload
        or not isinstance(payload["features"], list)
    ):
        log_stderr(f"USGS returned an unexpected payload shape: {type(payload).__name__}")
        return failure_envelope(
            "USGS",
            USGS_URL,
            f"USGS returned an unexpected payload shape ({type(payload).__name__}), "
            "not a GeoJSON FeatureCollection.",
            license="Public domain (USGS)",
        )
    features = payload["features"]
    events: list[dict] = []
    parsed_count = 0
    for feature in features:
        event = _parse_event(feature)
        if event is None:
            continue
        parsed_count += 1
        data = event.model_dump(mode="json")
        if use_radius:
            distance_km = haversine_km(center_lat, center_lon, event.latitude, event.longitude)
            if distance_km > radius_km:
                continue
            data["distance_km"] = round(distance_km, 2)
        events.append(data)
        if use_radius and len(events) >= limit:
            break

    # Codex cross-model finding: a nonempty features list where every
    # feature failed to parse (for example mag: null on all of them)
    # returned a bare [] and cached it as a real absence of earthquakes.
    # Zero features is still a genuine empty; zero parses from a nonempty
    # list is not, so it must never enter the cache.
    if features and parsed_count == 0:
        return failure_result(
            "USGS",
            USGS_URL,
            f"USGS sent {len(features)} feature(s) but none parsed "
            "(missing or malformed mag, time, or coordinates).",
            license="Public domain (USGS)",
            data_status=DATA_STATUS_INDETERMINATE,
            results=[],
        )

    cache[ckey] = events
    return events
