"""NOAA IBTrACS — International Best Track Archive for Climate Stewardship.

Historical tropical cyclone tracks from NOAA NCEI. Filters to Western Pacific
basin (WP) and flags tracks that passed through the Philippine Area of
Responsibility. Uses the ERDDAP tabledap CSV endpoint — stable URL, no auth.
https://www.ncei.noaa.gov/products/international-best-track-archive
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from ph_civic_data_mcp.models.climate import HistoricalTyphoon
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

ERDDAP_LAST3Y_URL = "https://erddap.aoml.noaa.gov/hdb/erddap/tabledap/IBTRACS_last3years.csv"
ERDDAP_SINCE1980_URL = "https://erddap.aoml.noaa.gov/hdb/erddap/tabledap/IBTrACS_since1980_1.csv"

# Columns we want (ERDDAP names them `latitude`/`longitude`, not `lat`/`lon`)
ERDDAP_COLUMNS = [
    "sid",
    "name",
    "season",
    "basin",
    "iso_time",
    "latitude",
    "longitude",
    "wmo_wind",
    "wmo_pres",
    "usa_wind",
    "usa_pres",
    "tokyo_wind",
    "tokyo_pres",
]

PAR_MIN_LAT, PAR_MAX_LAT = 5.0, 25.0
PAR_MIN_LNG, PAR_MAX_LNG = 115.0, 135.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _f(val: str | None) -> float | None:
    if val is None or val == "" or val.upper() == "NAN":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _parse_time(val: str | None) -> datetime | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _in_par(lat: float | None, lng: float | None) -> bool:
    if lat is None or lng is None:
        return False
    return PAR_MIN_LAT <= lat <= PAR_MAX_LAT and PAR_MIN_LNG <= lng <= PAR_MAX_LNG


@mcp.tool()
async def get_historical_typhoons_ph(year: int | None = None, limit: int = 30) -> list[dict]:
    """Historical tropical cyclone tracks that passed through the Philippine AOR.

    Sourced from NOAA IBTrACS (International Best Track Archive) — the
    authoritative global archive for tropical cyclone tracks. Filtered to the
    Western Pacific basin + coordinates inside the Philippine Area of
    Responsibility, aggregated per storm. Returns peak intensity, minimum
    pressure, and track period.

    Args:
        year: Season year. None returns recent (last 3 years).
        limit: Max storms to return (default 30).
    """
    limit = max(1, min(int(limit), 100))
    ckey = cache_key({"tool": "ibtracs", "year": year, "limit": limit})
    cache = CACHES["ibtracs_tracks"]
    if ckey in cache:
        return cache[ckey]

    base_url = ERDDAP_LAST3Y_URL if year is None else ERDDAP_SINCE1980_URL
    # ERDDAP tabledap query: cols after ?, filters after & (column names raw, values quoted if string)
    query_parts = [",".join(ERDDAP_COLUMNS), 'basin="WP"']
    if year is not None:
        query_parts.append(f"season={int(year)}")
    full_url = f"{base_url}?{'&'.join(query_parts)}"

    try:
        response = await fetch_with_retry(CLIENT, "GET", full_url)
        response.raise_for_status()
        text = response.text
    except Exception as exc:
        log_stderr(f"IBTrACS error: {exc}")
        return []

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if len(rows) < 3:
        return []

    header = rows[0]
    # rows[1] is units, rows[2:] is data
    data_rows = rows[2:]

    def col(name: str, row: list[str]) -> str | None:
        if name not in header:
            return None
        idx = header.index(name)
        return row[idx] if idx < len(row) else None

    storms: dict[str, dict] = {}
    for row in data_rows:
        sid = col("sid", row)
        if not sid:
            continue
        lat = _f(col("latitude", row))
        lng = _f(col("longitude", row))
        # wmo_wind is often null for WP storms; fall back to JTWC (usa) then JMA (tokyo).
        wind = _f(col("wmo_wind", row))
        if wind is None:
            wind = _f(col("usa_wind", row))
        if wind is None:
            wind = _f(col("tokyo_wind", row))
        pres = _f(col("wmo_pres", row))
        if pres is None:
            pres = _f(col("usa_pres", row))
        if pres is None:
            pres = _f(col("tokyo_pres", row))
        t = _parse_time(col("iso_time", row))

        entry = storms.setdefault(
            sid,
            {
                "sid": sid,
                "name": col("name", row) or "UNNAMED",
                "season": int(float(col("season", row) or 0)) or 0,
                "basin": col("basin", row) or "WP",
                "max_wind_kt": None,
                "min_pressure_mb": None,
                "start_time_utc": None,
                "end_time_utc": None,
                "track_points": 0,
                "passed_within_par": False,
            },
        )
        entry["track_points"] += 1
        if wind is not None:
            prev = entry["max_wind_kt"]
            if prev is None or wind > prev:
                entry["max_wind_kt"] = wind
        if pres is not None:
            prev = entry["min_pressure_mb"]
            if prev is None or pres < prev:
                entry["min_pressure_mb"] = pres
        if t is not None:
            if entry["start_time_utc"] is None or t < entry["start_time_utc"]:
                entry["start_time_utc"] = t
            if entry["end_time_utc"] is None or t > entry["end_time_utc"]:
                entry["end_time_utc"] = t
        if _in_par(lat, lng):
            entry["passed_within_par"] = True

    results: list[dict] = []
    par_storms = [s for s in storms.values() if s["passed_within_par"]]
    par_storms.sort(
        key=lambda s: s.get("start_time_utc") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    for entry in par_storms[:limit]:
        storm = HistoricalTyphoon(
            sid=entry["sid"],
            name=entry["name"],
            season=entry["season"],
            basin=entry["basin"],
            max_wind_kt=entry["max_wind_kt"],
            min_pressure_mb=entry["min_pressure_mb"],
            start_time_utc=entry["start_time_utc"],
            end_time_utc=entry["end_time_utc"],
            track_points=entry["track_points"],
            passed_within_par=entry["passed_within_par"],
            data_retrieved_at=_now(),
        )
        results.append(storm.model_dump(mode="json"))

    cache[ckey] = results
    return results
