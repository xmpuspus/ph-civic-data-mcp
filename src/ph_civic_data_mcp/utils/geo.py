"""Philippine geography helpers: region aliases + city coordinates + PSGC bridge.

Region normalization accepts common aliases used by PAGASA/PSA (e.g. "Metro Manila" → "NCR",
"Cordillera" → "CAR"). City coordinate table covers the 50 largest PH cities for Open-Meteo
lookup when PAGASA token is unavailable.

In v0.3.0 the resolver consults the PSGC source first to canonicalise a free-text place
name, then looks up coordinates from CITY_COORDS. PSGC does not currently publish lat/lng
itself, so CITY_COORDS remains the authoritative coordinate table. When network access
is unavailable (tests) the resolver falls back to the cheap CITY_COORDS-only path.
"""

from __future__ import annotations

from math import atan2, cos, radians, sin, sqrt

REGION_ALIASES: dict[str, str] = {
    "metro manila": "NCR",
    "national capital region": "NCR",
    "ncr": "NCR",
    "cordillera": "CAR",
    "cordillera administrative region": "CAR",
    "car": "CAR",
    "ilocos": "Region I",
    "ilocos region": "Region I",
    "region 1": "Region I",
    "region i": "Region I",
    "cagayan valley": "Region II",
    "region 2": "Region II",
    "region ii": "Region II",
    "central luzon": "Region III",
    "region 3": "Region III",
    "region iii": "Region III",
    "calabarzon": "Region IV-A",
    "region 4a": "Region IV-A",
    "region iv-a": "Region IV-A",
    "mimaropa": "MIMAROPA",
    "region 4b": "MIMAROPA",
    "region iv-b": "MIMAROPA",
    "bicol": "Region V",
    "bicol region": "Region V",
    "region 5": "Region V",
    "region v": "Region V",
    "western visayas": "Region VI",
    "region 6": "Region VI",
    "region vi": "Region VI",
    "central visayas": "Region VII",
    "region 7": "Region VII",
    "region vii": "Region VII",
    "eastern visayas": "Region VIII",
    "region 8": "Region VIII",
    "region viii": "Region VIII",
    "zamboanga peninsula": "Region IX",
    "region 9": "Region IX",
    "region ix": "Region IX",
    "northern mindanao": "Region X",
    "region 10": "Region X",
    "region x": "Region X",
    "davao region": "Region XI",
    "region 11": "Region XI",
    "region xi": "Region XI",
    "soccsksargen": "Region XII",
    "region 12": "Region XII",
    "region xii": "Region XII",
    "caraga": "Region XIII",
    "region 13": "Region XIII",
    "region xiii": "Region XIII",
    "barmm": "BARMM",
    "bangsamoro": "BARMM",
    "armm": "BARMM",
}


CITY_COORDS: dict[str, tuple[float, float]] = {
    "manila": (14.5995, 120.9842),
    "quezon city": (14.676, 121.0437),
    "caloocan": (14.6488, 120.9674),
    "davao": (7.1907, 125.4553),
    "davao city": (7.1907, 125.4553),
    "cebu": (10.3157, 123.8854),
    "cebu city": (10.3157, 123.8854),
    "zamboanga": (6.9214, 122.079),
    "zamboanga city": (6.9214, 122.079),
    "antipolo": (14.5873, 121.176),
    "pasig": (14.5764, 121.0851),
    "taguig": (14.5176, 121.0509),
    "valenzuela": (14.7011, 120.9830),
    "dasmarinas": (14.3294, 120.9367),
    "cagayan de oro": (8.4542, 124.6319),
    "paranaque": (14.4793, 121.0198),
    "las pinas": (14.4504, 120.9883),
    "makati": (14.5547, 121.0244),
    "bacoor": (14.459, 120.929),
    "general santos": (6.1164, 125.1716),
    "bacolod": (10.6765, 122.9509),
    "muntinlupa": (14.3832, 121.0409),
    "san jose del monte": (14.8139, 121.0453),
    "calamba": (14.2117, 121.1653),
    "marikina": (14.6507, 121.1029),
    "iloilo city": (10.7202, 122.5621),
    "iloilo": (10.7202, 122.5621),
    "pasay": (14.5378, 120.9966),
    "angeles": (15.1455, 120.5876),
    "angeles city": (15.1455, 120.5876),
    "san pedro": (14.3583, 121.0478),
    "mandaluyong": (14.5794, 121.0359),
    "baguio": (16.4023, 120.596),
    "baguio city": (16.4023, 120.596),
    "lapu-lapu": (10.3103, 123.9494),
    "lapu-lapu city": (10.3103, 123.9494),
    "san fernando": (15.034, 120.685),
    "butuan": (8.9475, 125.5406),
    "mandaue": (10.3231, 123.922),
    "tarlac city": (15.4869, 120.596),
    "tarlac": (15.4869, 120.596),
    "olongapo": (14.8386, 120.2842),
    "malabon": (14.6570, 120.9563),
    "lipa": (13.9411, 121.1631),
    "lipa city": (13.9411, 121.1631),
    "cabanatuan": (15.4869, 120.9671),
    "binan": (14.3363, 121.0805),
    "san pablo": (14.0683, 121.3256),
    "navotas": (14.6667, 120.9417),
    "naga": (13.6218, 123.1948),
    "naga city": (13.6218, 123.1948),
    "legazpi": (13.1391, 123.7438),
    "legazpi city": (13.1391, 123.7438),
    "iligan": (8.228, 124.2452),
    "iligan city": (8.228, 124.2452),
    "puerto princesa": (9.7392, 118.7353),
    "tacloban": (11.2447, 125.0048),
    "tacloban city": (11.2447, 125.0048),
    "cotabato": (7.2236, 124.2464),
    "cotabato city": (7.2236, 124.2464),
    "batangas": (13.7565, 121.0583),
    "batangas city": (13.7565, 121.0583),
    "ormoc": (11.0064, 124.6075),
    "dumaguete": (9.3068, 123.3054),
    "roxas": (11.5853, 122.7511),
    "roxas city": (11.5853, 122.7511),
    "surigao": (9.7894, 125.4947),
    "surigao city": (9.7894, 125.4947),
    "laoag": (18.1978, 120.5937),
    "laoag city": (18.1978, 120.5937),
    "tagaytay": (14.1153, 120.9621),
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points.

    Shared by usgs.py and phivolcs.py, which each kept a private copy before
    v0.7.0.
    """
    earth_radius_km = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi = radians(lat2 - lat1)
    dlambda = radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlambda / 2) ** 2
    return 2 * earth_radius_km * atan2(sqrt(a), sqrt(1 - a))


def normalize_region(name: str | None) -> str | None:
    if not name:
        return None
    key = name.strip().lower()
    return REGION_ALIASES.get(key, name.strip())


def city_to_coords(city: str) -> tuple[float, float] | None:
    """Direct CITY_COORDS lookup (sync, no network). Used as a fallback path.

    Handles common PSGC name shapes:
    - "Manila" / "manila"
    - "Cebu City" / "cebu city"
    - "City of Manila" (PSGC inversion)
    - "Municipality of Tagaytay"
    - "Sta. Mesa, Manila" (multi-segment, last segment wins)
    """
    if not city:
        return None
    raw = city.strip().lower()

    # Try the literal lookup first.
    if raw in CITY_COORDS:
        return CITY_COORDS[raw]

    # Strip "city of " / "municipality of " prefixes that PSGC uses.
    for prefix in ("city of ", "municipality of "):
        if raw.startswith(prefix):
            stripped = raw[len(prefix) :].strip()
            if stripped in CITY_COORDS:
                return CITY_COORDS[stripped]
            stripped_with_city = f"{stripped} city"
            if stripped_with_city in CITY_COORDS:
                return CITY_COORDS[stripped_with_city]

    # Strip trailing " city" suffix and try.
    no_suffix = raw[:-5] if raw.endswith(" city") else raw
    if no_suffix in CITY_COORDS:
        return CITY_COORDS[no_suffix]
    with_suffix = f"{no_suffix} city"
    if with_suffix in CITY_COORDS:
        return CITY_COORDS[with_suffix]

    # Multi-segment: try the last comma-segment, then the first.
    if "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        for segment in (parts[-1], parts[0]) if parts else ():
            seg_result = city_to_coords(segment)
            if seg_result is not None:
                return seg_result

    return None


class GeoResolveError(RuntimeError):
    """PSGC name canonicalisation itself failed, so no verdict is known.

    A caller must not treat this the same as `resolve_to_coords` returning
    None. None means PSGC gave a clean answer and the place has no known
    coordinates. This error means PSGC could not answer at all, and the
    caller should report an outage, not an unknown place. Codex cross-model
    finding: `get_weather_forecast("QC")` answered "No coordinates known"
    with no `upstream_error` while PSGC was down, even though QC is a known
    alias and PSGC was the only broken part.
    """


async def resolve_to_coords(query: str) -> tuple[float, float] | None:
    """Async resolver: try PSGC name canonicalisation, then CITY_COORDS.

    PSGC does not expose coordinates, so this function normalises a query like
    "Sta. Mesa, Manila" to its canonical PSGC name (e.g. "Manila") and then
    looks up CITY_COORDS. A known city is served from CITY_COORDS first, so
    it never reaches the network.

    Raises GeoResolveError when PSGC itself failed, whether by a raised
    transport error or an `upstream_error` envelope, so a caller can turn
    that into its own failure envelope instead of reading it as "unknown
    place". Returns None only for a clean PSGC no-match.
    """
    if not query:
        return None

    direct = city_to_coords(query)
    if direct is not None:
        return direct

    from ph_civic_data_mcp.sources.psgc import resolve_ph_location

    try:
        resolved = await resolve_ph_location(query)
    except Exception as exc:
        raise GeoResolveError(str(exc)) from exc

    if isinstance(resolved, dict) and resolved.get("upstream_error"):
        caveats = resolved.get("caveats") or []
        detail = caveats[0] if caveats else "PSGC API unavailable"
        raise GeoResolveError(detail)

    if not isinstance(resolved, dict) or not resolved.get("matched", True):
        return None
    name = resolved.get("name")
    if not name:
        return None
    return city_to_coords(name)
