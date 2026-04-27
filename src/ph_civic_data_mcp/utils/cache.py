"""In-memory TTL caches. No disk writes. One cache per source with spec-frozen TTLs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cachetools import TTLCache

CACHES: dict[str, TTLCache[str, Any]] = {
    "phivolcs_earthquakes": TTLCache(maxsize=10, ttl=300),
    "phivolcs_volcanoes": TTLCache(maxsize=10, ttl=1800),
    "phivolcs_bulletins": TTLCache(maxsize=100, ttl=3600),
    "pagasa_forecast": TTLCache(maxsize=100, ttl=3600),
    "pagasa_typhoons": TTLCache(maxsize=5, ttl=600),
    "pagasa_alerts": TTLCache(maxsize=10, ttl=600),
    "philgeps_data": TTLCache(maxsize=50, ttl=21600),
    "psa_population": TTLCache(maxsize=50, ttl=86400),
    "psa_poverty": TTLCache(maxsize=50, ttl=86400),
    "nasa_power": TTLCache(maxsize=100, ttl=86400),
    "open_meteo_aq": TTLCache(maxsize=50, ttl=900),
    "modis_ndvi": TTLCache(maxsize=50, ttl=86400),
    "usgs_events": TTLCache(maxsize=20, ttl=600),
    "ibtracs_tracks": TTLCache(maxsize=20, ttl=86400),
    "world_bank": TTLCache(maxsize=50, ttl=86400),
    "psgc_resolve": TTLCache(maxsize=200, ttl=86400),
    "psgc_browse": TTLCache(maxsize=200, ttl=86400),
    "infra_projects": TTLCache(maxsize=50, ttl=21600),
}


def cache_key(params: dict[str, Any]) -> str:
    """Deterministic cache key from a parameter dict."""
    payload = json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(payload.encode()).hexdigest()
