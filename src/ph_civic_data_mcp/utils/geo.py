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


def normalize_region(name: str | None) -> str | None:
    if not name:
        return None
    key = name.strip().lower()
    return REGION_ALIASES.get(key, name.strip())


def city_to_coords(city: str) -> tuple[float, float] | None:
    """Direct CITY_COORDS lookup (sync, no network). Used as a fallback path."""
    if not city:
        return None
    key = city.strip().lower()
    key = key.replace(" city", "").strip()
    if key in CITY_COORDS:
        return CITY_COORDS[key]
    key_with_suffix = f"{key} city"
    if key_with_suffix in CITY_COORDS:
        return CITY_COORDS[key_with_suffix]
    if city.strip().lower() in CITY_COORDS:
        return CITY_COORDS[city.strip().lower()]
    return None


async def resolve_to_coords(query: str) -> tuple[float, float] | None:
    """Async resolver: try PSGC name canonicalisation, then CITY_COORDS.

    PSGC does not expose coordinates, so this function normalises a query like
    "Sta. Mesa, Manila" to its canonical PSGC name (e.g. "Manila") and then
    looks up CITY_COORDS. Network failures degrade silently to the direct
    CITY_COORDS path.
    """
    if not query:
        return None

    direct = city_to_coords(query)
    if direct is not None:
        return direct

    try:
        from ph_civic_data_mcp.sources.psgc import resolve_ph_location

        resolved = await resolve_ph_location(query)
    except Exception:
        return None

    if not isinstance(resolved, dict) or not resolved.get("matched", True):
        return None
    name = resolved.get("name")
    if not name:
        return None
    return city_to_coords(name)
