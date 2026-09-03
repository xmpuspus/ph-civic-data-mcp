"""Offline tests for the v0.7.0 IBTrACS streaming rewrite.

The old code read response.text into one string, then list(reader) into one
list, before it filtered a single row. These tests run the streaming
replacement (_stream_storms) against a small in-memory CSV, so the fix is
checked without a real multi-megabyte NOAA file.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import ibtracs as ibtracs_module
from ph_civic_data_mcp.utils.cache import CACHES

CSV_HEADER = (
    "sid,name,season,basin,iso_time,latitude,longitude,"
    "wmo_wind,wmo_pres,usa_wind,usa_pres,tokyo_wind,tokyo_pres"
)
CSV_UNITS = ",,,,,degrees_north,degrees_east,kt,mb,kt,mb,kt,mb"

# STORMA has two track points inside the PAR box (lat 5-25, lng 115-135).
# STORMB stays outside the PAR box the whole time, so it must not appear in
# the result even though it is a real, fully-formed storm.
SUCCESS_ROWS = [
    "2024001,STORMA,2024,WP,2024-01-01T00:00:00Z,10.0,120.0,65,970,,,,,",
    "2024001,STORMA,2024,WP,2024-01-01T06:00:00Z,12.0,122.0,70,965,,,,,",
    "2024002,STORMB,2024,WP,2024-02-01T00:00:00Z,30.0,150.0,50,990,,,,,",
]


def _csv_body(rows: list[str]) -> str:
    return "\n".join([CSV_HEADER, CSV_UNITS, *rows]) + "\n"


def _mock_client(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["ibtracs_tracks"].clear()
    yield


@pytest.mark.asyncio
async def test_stream_success_filters_by_par_and_aggregates_by_storm(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_csv_body(SUCCESS_ROWS))

    monkeypatch.setattr(ibtracs_module, "CLIENT", _mock_client(handler))

    results = await ibtracs_module.get_historical_typhoons_ph(year=2024, limit=10)

    assert isinstance(results, list)
    assert len(results) == 1
    storm = results[0]
    assert storm["sid"] == "2024001"
    assert storm["name"] == "STORMA"
    assert storm["passed_within_par"] is True
    assert storm["track_points"] == 2
    assert storm["max_wind_kt"] == 70.0
    assert storm["min_pressure_mb"] == 965.0


@pytest.mark.asyncio
async def test_stream_empty_response_returns_no_data_failure(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        # Header only: fewer than 3 total CSV lines, same threshold the old
        # buffered version used.
        return httpx.Response(200, text=CSV_HEADER + "\n")

    monkeypatch.setattr(ibtracs_module, "CLIENT", _mock_client(handler))

    result = await ibtracs_module.get_historical_typhoons_ph(year=2024, limit=10)

    assert result["upstream_error"] is True
    assert result["results"] == []
    assert "no data rows" in result["caveats"][0]


@pytest.mark.asyncio
async def test_stream_upstream_failure_returns_envelope(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    monkeypatch.setattr(ibtracs_module, "CLIENT", _mock_client(handler))

    result = await ibtracs_module.get_historical_typhoons_ph(year=2024, limit=10)

    assert result["upstream_error"] is True
    assert result["results"] == []
    assert "unavailable" in result["caveats"][0]


@pytest.mark.asyncio
async def test_stream_header_units_and_one_empty_row_is_no_data_not_cached(monkeypatch):
    """A header, a units row, and one empty data row must not read as real
    data. Codex cross-model finding: line_count counted physical CSV lines,
    so these three lines passed the old `< 3` check and cached a bare []."""
    empty_row = ",,,,,,,,,,,,"
    body = "\n".join([CSV_HEADER, CSV_UNITS, empty_row]) + "\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    monkeypatch.setattr(ibtracs_module, "CLIENT", _mock_client(handler))

    result = await ibtracs_module.get_historical_typhoons_ph(year=2024, limit=10)

    assert result["upstream_error"] is True
    assert result["results"] == []
    assert len(CACHES["ibtracs_tracks"]) == 0


@pytest.mark.asyncio
async def test_stream_schema_drift_missing_sid_column_is_upstream_error(monkeypatch):
    """Codex cross-model finding: a header carrying "storm_id" instead of
    "sid" parsed every row to nothing, and the old code read that as a
    legitimate zero-storm season, so a real drift cached as a false empty
    answer. The header check now catches the missing column right away."""
    drifted_header = CSV_HEADER.replace("sid,", "storm_id,")
    body = "\n".join([drifted_header, CSV_UNITS, SUCCESS_ROWS[0]]) + "\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    monkeypatch.setattr(ibtracs_module, "CLIENT", _mock_client(handler))

    result = await ibtracs_module.get_historical_typhoons_ph(year=2024, limit=10)

    assert result["upstream_error"] is True
    assert result["results"] == []
    assert len(CACHES["ibtracs_tracks"]) == 0
