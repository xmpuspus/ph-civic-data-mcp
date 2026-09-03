"""Offline fixtures for Open-Meteo air quality. No live coverage existed
before v0.7.0; tests/test_v020_sources.py hits the real API and is `live`-marked.

Covers: the UTC timezone fix, a real success, an empty-but-valid current
block, an upstream failure, and a schema-drift body.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import open_meteo_aq as aq_module
from ph_civic_data_mcp.utils.cache import CACHES


def _json_response(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


CURRENT_PAYLOAD = {
    "current": {
        "time": "2026-09-03T15:00",
        "pm10": 8.5,
        "pm2_5": 6.9,
        "carbon_monoxide": 120.0,
        "nitrogen_dioxide": 5.1,
        "sulphur_dioxide": 1.2,
        "ozone": 40.0,
        "european_aqi": 22,
        "us_aqi": 29,
    }
}


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["open_meteo_aq"].clear()
    yield
    CACHES["open_meteo_aq"].clear()


@pytest.mark.asyncio
async def test_the_request_asks_open_meteo_for_utc_not_manila(monkeypatch):
    seen_params: dict = {}

    async def _fake(client, method, url, **kwargs):
        seen_params.update(kwargs.get("params", {}))
        return _json_response(method, url, CURRENT_PAYLOAD)

    monkeypatch.setattr(aq_module, "fetch_with_retry", _fake)

    await aq_module.get_air_quality("Manila")
    assert seen_params.get("timezone") == "UTC"


@pytest.mark.asyncio
async def test_the_returned_timestamp_carries_an_explicit_utc_marker(monkeypatch):
    """A naive Manila-local reading must never be mislabelled as UTC."""

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, CURRENT_PAYLOAD)

    monkeypatch.setattr(aq_module, "fetch_with_retry", _fake)

    result = await aq_module.get_air_quality("Manila")
    measured_at = result["measured_at"]
    assert measured_at.endswith("Z") or measured_at.endswith("+00:00"), measured_at
    # "15:00" is the UTC value only because the request itself asked for UTC;
    # reading it back confirms the label and the wall-clock value agree.
    assert measured_at.startswith("2026-09-03T15:00")


@pytest.mark.asyncio
async def test_a_success_response_caches(monkeypatch):
    calls = {"n": 0}

    async def _fake(client, method, url, **kwargs):
        calls["n"] += 1
        return _json_response(method, url, CURRENT_PAYLOAD)

    monkeypatch.setattr(aq_module, "fetch_with_retry", _fake)

    first = await aq_module.get_air_quality("Manila")
    assert first["pm2_5"] == pytest.approx(6.9)
    assert first["us_aqi"] == 29
    assert first["aqi_category"] == "Good"
    assert len(CACHES["open_meteo_aq"]) == 1

    before = calls["n"]
    again = await aq_module.get_air_quality("Manila")
    assert again == first
    assert calls["n"] == before


@pytest.mark.asyncio
async def test_an_empty_current_block_is_indeterminate_and_never_cached(monkeypatch):
    """A body with no time and no pollutant field is not a real reading.

    v0.7.0 fix: the old code assigned the server clock to measured_at and
    cached this as a valid zero reading. It must now return indeterminate
    and skip the cache.
    """

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, {"current": {}})

    monkeypatch.setattr(aq_module, "fetch_with_retry", _fake)

    result = await aq_module.get_air_quality("Manila")
    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert any("time" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["open_meteo_aq"]) == 0


@pytest.mark.asyncio
async def test_an_upstream_failure_is_never_cached(monkeypatch):
    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("open-meteo down")

    monkeypatch.setattr(aq_module, "fetch_with_retry", _boom)

    result = await aq_module.get_air_quality("Manila")
    assert result["upstream_error"] is True
    assert any("ConnectError" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["open_meteo_aq"]) == 0


@pytest.mark.asyncio
async def test_an_unknown_location_is_invalid_request_not_a_bare_dict(monkeypatch):
    """v0.7.0 fix: a hand-built dict with no data_status raised KeyError on
    read. It must now carry validation_error and never call the upstream.
    """
    calls = {"n": 0}

    async def _fake(client, method, url, **kwargs):
        calls["n"] += 1
        return _json_response(method, url, CURRENT_PAYLOAD)

    monkeypatch.setattr(aq_module, "fetch_with_retry", _fake)

    result = await aq_module.get_air_quality("Atlantis")
    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["location"] == "Atlantis"
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_an_unparseable_time_is_indeterminate_and_never_cached(monkeypatch):
    """v0.7.0 fix: a present but bad current.time fell through to the server
    clock and was published and cached as a real reading.
    """
    payload = {
        "current": {
            **CURRENT_PAYLOAD["current"],
            "time": "not-a-date",
            "us_aqi": 42,
        }
    }

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, payload)

    monkeypatch.setattr(aq_module, "fetch_with_retry", _fake)

    result = await aq_module.get_air_quality("Manila")
    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert any("not-a-date" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["open_meteo_aq"]) == 0


@pytest.mark.asyncio
async def test_a_schema_drift_body_is_reported_not_a_crash(monkeypatch):
    """A bare list instead of the documented object body must not raise."""

    async def _drifted(client, method, url, **kwargs):
        return _json_response(method, url, ["unexpected", "shape"])

    monkeypatch.setattr(aq_module, "fetch_with_retry", _drifted)

    result = await aq_module.get_air_quality("Manila")
    assert result["upstream_error"] is True
    assert len(CACHES["open_meteo_aq"]) == 0
