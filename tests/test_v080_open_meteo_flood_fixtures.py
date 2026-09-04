"""Offline fixtures for Open-Meteo flood forecasts.

Fixture body is the real probe response recorded in
tmp/ulw-20260903/r3-fixtures/open_meteo_flood.json (7-day forecast for a
point near Metro Manila). Covers: a real success, caching, a transport
failure, a schema-drift body, an empty daily.time, a PSGC outage during
location resolution, and the two invalid_request cases (bad day count,
unresolved place).
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import open_meteo_flood as flood_module
from ph_civic_data_mcp.utils.cache import CACHES


def _json_response(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


FLOOD_PAYLOAD = {
    "latitude": 14.575005,
    "longitude": 121.025024,
    "generationtime_ms": 1.3331174850463867,
    "utc_offset_seconds": 0,
    "timezone": "GMT",
    "timezone_abbreviation": "GMT",
    "elevation": 6.0,
    "daily_units": {
        "time": "iso8601",
        "river_discharge": "m³/s",
        "river_discharge_max": "m³/s",
    },
    "daily": {
        "time": [
            "2026-09-03",
            "2026-09-04",
            "2026-09-05",
            "2026-09-06",
            "2026-09-07",
            "2026-09-08",
            "2026-09-09",
        ],
        "river_discharge": [298.92, 276.97, 250.77, 251.93, 255.45, 265.07, 298.23],
        "river_discharge_max": [327.10, 326.34, 342.56, 485.41, 527.45, 519.00, 569.16],
    },
}


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["open_meteo_flood"].clear()
    yield
    CACHES["open_meteo_flood"].clear()


@pytest.mark.asyncio
async def test_a_success_response_returns_seven_days_and_caches(monkeypatch):
    calls = {"n": 0}

    async def _fake(client, method, url, **kwargs):
        calls["n"] += 1
        return _json_response(method, url, FLOOD_PAYLOAD)

    monkeypatch.setattr(flood_module, "fetch_with_retry", _fake)

    first = await flood_module.get_flood_forecast("Cagayan de Oro")
    assert first["data_status"] == "success"
    assert first["upstream_error"] is False
    assert first["validation_error"] is False
    assert len(first["days"]) == 7
    assert first["days"][0] == {
        "date": "2026-09-03",
        "river_discharge_m3s": pytest.approx(298.92),
        "river_discharge_max_m3s": pytest.approx(327.10),
        "river_discharge_min_m3s": None,
    }
    assert first["units"]["river_discharge_m3s"] == "m³/s"
    assert first["forecast_days"] == 7
    assert first["past_days"] == 0
    assert "GloFAS" in first["note"]
    assert first["source"] == "Open-Meteo Flood API (GloFAS), CC BY 4.0"
    assert len(CACHES["open_meteo_flood"]) == 1

    before = calls["n"]
    again = await flood_module.get_flood_forecast("Cagayan de Oro")
    assert again == first
    assert calls["n"] == before


@pytest.mark.asyncio
async def test_an_upstream_failure_is_never_cached(monkeypatch):
    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("flood-api down")

    monkeypatch.setattr(flood_module, "fetch_with_retry", _boom)

    result = await flood_module.get_flood_forecast("Marikina")
    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert result["days"] == []
    assert any("ConnectError" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["open_meteo_flood"]) == 0


@pytest.mark.asyncio
async def test_a_schema_drift_body_is_reported_not_a_crash(monkeypatch):
    """A bare list instead of the documented object body must not raise."""

    async def _drifted(client, method, url, **kwargs):
        return _json_response(method, url, ["unexpected", "shape"])

    monkeypatch.setattr(flood_module, "fetch_with_retry", _drifted)

    result = await flood_module.get_flood_forecast("Davao")
    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert result["days"] == []
    assert len(CACHES["open_meteo_flood"]) == 0


@pytest.mark.asyncio
async def test_an_empty_daily_time_is_indeterminate_and_never_cached(monkeypatch):
    payload = {**FLOOD_PAYLOAD, "daily": {**FLOOD_PAYLOAD["daily"], "time": []}}

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, payload)

    monkeypatch.setattr(flood_module, "fetch_with_retry", _fake)

    result = await flood_module.get_flood_forecast("Cebu")
    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert result["days"] == []
    assert len(CACHES["open_meteo_flood"]) == 0


@pytest.mark.asyncio
async def test_dates_without_a_discharge_series_are_indeterminate_not_null_success(monkeypatch):
    # Codex pass on v0.8.0: only daily.time was required, so a body with
    # dates and no river_discharge list cached as a success full of nulls.
    payload = {**FLOOD_PAYLOAD, "daily": {"time": ["2026-09-05"]}}

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, payload)

    monkeypatch.setattr(flood_module, "fetch_with_retry", _fake)

    result = await flood_module.get_flood_forecast("Marikina", forecast_days=1)
    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert result["days"] == []
    assert "river_discharge" in result["caveats"][0]
    assert len(CACHES["open_meteo_flood"]) == 0


@pytest.mark.asyncio
async def test_an_empty_river_discharge_beside_three_dates_is_indeterminate_not_cached(
    monkeypatch,
):
    payload = {
        **FLOOD_PAYLOAD,
        "daily": {
            "time": ["2026-09-05", "2026-09-06", "2026-09-07"],
            "river_discharge": [],
        },
    }

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, payload)

    monkeypatch.setattr(flood_module, "fetch_with_retry", _fake)

    result = await flood_module.get_flood_forecast("Marikina", forecast_days=3)
    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert result["days"] == []
    assert "river_discharge" in result["caveats"][0]
    assert len(CACHES["open_meteo_flood"]) == 0


@pytest.mark.asyncio
async def test_a_short_river_discharge_beside_three_dates_is_indeterminate_not_cached(
    monkeypatch,
):
    payload = {
        **FLOOD_PAYLOAD,
        "daily": {
            "time": ["2026-09-05", "2026-09-06", "2026-09-07"],
            "river_discharge": [250.5, 260.1],
        },
    }

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, payload)

    monkeypatch.setattr(flood_module, "fetch_with_retry", _fake)

    result = await flood_module.get_flood_forecast("Marikina", forecast_days=3)
    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert result["days"] == []
    assert "river_discharge" in result["caveats"][0]
    assert len(CACHES["open_meteo_flood"]) == 0


@pytest.mark.asyncio
async def test_twenty_concurrent_cold_calls_fetch_once(monkeypatch):
    # Last receipt pass on v0.8.0: every sibling source single-flights its
    # cold cache miss, and flood did not, so twenty callers sent twenty GETs.
    import asyncio

    calls = {"n": 0}

    async def _fake(client, method, url, **kwargs):
        calls["n"] += 1
        await asyncio.sleep(0.02)
        return _json_response(method, url, FLOOD_PAYLOAD)

    monkeypatch.setattr(flood_module, "fetch_with_retry", _fake)

    results = await asyncio.gather(
        *(flood_module.get_flood_forecast("Marikina") for _ in range(20))
    )

    assert calls["n"] == 1
    assert all(r["data_status"] == "success" for r in results)
    assert len(CACHES["open_meteo_flood"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_number", ["NaN", "inf", "-Infinity"])
async def test_a_non_finite_discharge_reads_as_null_not_as_a_number(monkeypatch, bad_number):
    payload = {
        **FLOOD_PAYLOAD,
        "daily": {"time": ["2026-09-05"], "river_discharge": [bad_number]},
    }

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, payload)

    monkeypatch.setattr(flood_module, "fetch_with_retry", _fake)

    result = await flood_module.get_flood_forecast("Marikina", forecast_days=1)
    assert result["data_status"] == "success"
    assert result["days"][0]["river_discharge_m3s"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_value", [0, 31, "seven", 7.5, True])
async def test_a_bad_forecast_days_is_invalid_request(monkeypatch, bad_value):
    async def _must_not_call(client, method, url, **kwargs):
        raise AssertionError("a rejected forecast_days must never reach the upstream")

    monkeypatch.setattr(flood_module, "fetch_with_retry", _must_not_call)

    result = await flood_module.get_flood_forecast("Cebu", forecast_days=bad_value)
    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["days"] == []
    assert len(CACHES["open_meteo_flood"]) == 0


@pytest.mark.asyncio
async def test_an_unresolved_place_is_invalid_request(monkeypatch):
    from ph_civic_data_mcp.sources import psgc as psgc_module

    async def _no_match(query):
        return {"matched": False, "caveats": [f"No match for {query!r}"]}

    monkeypatch.setattr(psgc_module, "resolve_ph_location", _no_match)

    async def _must_not_call(client, method, url, **kwargs):
        raise AssertionError("an unresolved place must never reach the upstream")

    monkeypatch.setattr(flood_module, "fetch_with_retry", _must_not_call)

    result = await flood_module.get_flood_forecast("Wakanda")
    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["location"] == "Wakanda"
    assert result["days"] == []
    assert len(CACHES["open_meteo_flood"]) == 0


@pytest.mark.asyncio
async def test_a_psgc_outage_during_resolution_is_upstream_error_not_unknown_place(monkeypatch):
    """A GeoResolveError must read as an outage, never as an unresolved place.

    Mirrors test_get_weather_forecast_reports_upstream_error_on_psgc_outage
    in tests/test_v031_fixes.py.
    """
    from ph_civic_data_mcp.sources import psgc as psgc_module

    async def _broken(query):
        raise ConnectionError("PSGC down")

    monkeypatch.setattr(psgc_module, "resolve_ph_location", _broken)

    async def _must_not_call(client, method, url, **kwargs):
        raise AssertionError("a PSGC outage must never reach the flood upstream")

    monkeypatch.setattr(flood_module, "fetch_with_retry", _must_not_call)

    result = await flood_module.get_flood_forecast("Marawi")
    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert result["validation_error"] is False
    assert result["days"] == []
    assert any("PSGC down" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["open_meteo_flood"]) == 0
