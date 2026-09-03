"""NOAA IBTrACS — International Best Track Archive for Climate Stewardship.

Historical tropical cyclone tracks from NOAA NCEI. Filters to Western Pacific
basin (WP) and flags tracks that passed through the Philippine Area of
Responsibility. Uses the ERDDAP tabledap CSV endpoint — stable URL, no auth.
https://www.ncei.noaa.gov/products/international-best-track-archive
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime, timezone

from ph_civic_data_mcp.models.climate import HistoricalTyphoon
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import failure_envelope
from ph_civic_data_mcp.utils.http import (
    CLIENT,
    MAX_RETRIES,
    RETRY_DELAYS,
    RETRY_STATUSES,
    RETRYABLE_EXCEPTIONS,
    log_stderr,
)

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


def _accumulate_storm(header: list[str], row: list[str], storms: dict[str, dict]) -> None:
    """Parse one IBTrACS CSV data row and fold it into its storm's aggregate.

    Exact port of the per-row logic the old buffer-then-loop version ran, one
    row at a time, so the streaming rewrite in _stream_storms changes nothing
    about which storms match the PAR filter or what their aggregated stats
    come out to.
    """

    def col(name: str) -> str | None:
        if name not in header:
            return None
        idx = header.index(name)
        return row[idx] if idx < len(row) else None

    sid = col("sid")
    if not sid:
        return
    lat = _f(col("latitude"))
    lng = _f(col("longitude"))
    # wmo_wind is often null for WP storms; fall back to JTWC (usa) then JMA (tokyo).
    wind = _f(col("wmo_wind"))
    if wind is None:
        wind = _f(col("usa_wind"))
    if wind is None:
        wind = _f(col("tokyo_wind"))
    pres = _f(col("wmo_pres"))
    if pres is None:
        pres = _f(col("usa_pres"))
    if pres is None:
        pres = _f(col("tokyo_pres"))
    t = _parse_time(col("iso_time"))

    entry = storms.setdefault(
        sid,
        {
            "sid": sid,
            "name": col("name") or "UNNAMED",
            "season": int(float(col("season") or 0)) or 0,
            "basin": col("basin") or "WP",
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


async def _stream_storms(url: str) -> tuple[int, dict[str, dict]]:
    """Stream the ERDDAP CSV and aggregate storms row by row.

    Reads response.aiter_lines() instead of buffering the whole body into
    response.text and then a full list(reader) of every row, which is what
    the old code did before parsing a single one. The since-1980 file runs
    to tens of thousands of rows, most for storms nobody asked for.

    No early exit once `limit` storms match: the caller sorts matches by
    start_time_utc descending and keeps the most recent `limit`, and this
    feed arrives in the archive's own (oldest-first) row order, so stopping
    early would silently swap "the `limit` most recent PAR storms" for "the
    first `limit` PAR storms encountered" — the wrong set, not just a
    truncated one. This function still reads every row; it only avoids
    holding the whole body and every parsed row in memory at once.

    Retries the connection the way fetch_with_retry does, reusing the same
    status codes, exception classes, and delay ladder. fetch_with_retry
    itself can't wrap a streaming response, and a streaming read can't be
    resumed mid-body, so a retry here restarts the whole read and resets its
    own row count and storms dict per attempt.
    """
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        line_count = 0
        header: list[str] | None = None
        seen_units_row = False
        storms: dict[str, dict] = {}
        try:
            async with CLIENT.stream("GET", url) as response:
                if response.status_code in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue
                response.raise_for_status()
                async for raw_line in response.aiter_lines():
                    line_count += 1
                    row = next(csv.reader([raw_line]))
                    if header is None:
                        header = row
                        continue
                    if not seen_units_row:
                        seen_units_row = True
                        continue
                    _accumulate_storm(header, row, storms)
            return line_count, storms
        except RETRYABLE_EXCEPTIONS as exc:
            last_exc = exc
            if attempt < MAX_RETRIES - 1:
                await asyncio.sleep(RETRY_DELAYS[attempt])
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("unreachable")


@mcp.tool(
    title="Historical typhoon tracks through the PAR",
    tags={"ibtracs", "noaa", "philippines", "typhoon"},
    annotations={
        "title": "Historical typhoon tracks through the PAR",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_historical_typhoons_ph(year: int | None = None, limit: int = 30) -> list[dict] | dict:
    """Historical tropical cyclone tracks that passed through the Philippine AOR.

    Sourced from NOAA IBTrACS (International Best Track Archive), the
    authoritative global archive for tropical cyclone tracks. Filtered to the
    Western Pacific basin and coordinates inside the Philippine Area of
    Responsibility, aggregated per storm. The result streams the source CSV
    row by row and returns the most recent storms that crossed the PAR,
    sorted by start time. Returns peak intensity, minimum pressure, and
    track period. Examples:

      get_historical_typhoons_ph()                     # last 3 years, up to 30 storms
      get_historical_typhoons_ph(year=2024)             # season 2024, up to 30 storms
      get_historical_typhoons_ph(year=2024, limit=10)   # season 2024, up to 10 storms

    On failure: a stream error or a response with no data rows returns a
    dict. data_status is "unavailable", upstream_error is true, and results
    is empty, with the real error text in caveats.

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
        line_count, storms = await _stream_storms(full_url)
    except Exception as exc:
        log_stderr(f"IBTrACS error: {exc}")
        return failure_envelope(
            "NOAA IBTrACS",
            base_url,
            f"IBTrACS ERDDAP endpoint unavailable ({type(exc).__name__}: {exc}).",
            license="Public domain (NOAA)",
        )

    if line_count < 3:
        return failure_envelope(
            "NOAA IBTrACS",
            base_url,
            "IBTrACS response had no data rows — likely an upstream/format issue.",
            license="Public domain (NOAA)",
        )

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
