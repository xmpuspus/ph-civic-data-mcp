"""Offline fixtures for NASA POWER. No live coverage existed before v0.7.0;
tests/test_v020_sources.py hits the real API and is `live`-marked.

Covers: a real success, an empty-but-valid parameter block, an upstream
failure, and a schema-drift body (non-object response).
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import nasa_power as power_module
from ph_civic_data_mcp.utils.cache import CACHES


def _json_response(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


SUCCESS_PAYLOAD = {
    "properties": {
        "parameter": {
            "ALLSKY_SFC_SW_DWN": {"20260401": 5.2, "20260402": 5.4},
            "T2M": {"20260401": 28.1, "20260402": 27.9},
            "PRECTOTCORR": {"20260401": 0.0, "20260402": 3.1},
            "WS2M": {"20260401": 2.1, "20260402": 1.8},
        }
    }
}


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["nasa_power"].clear()
    yield
    CACHES["nasa_power"].clear()


@pytest.mark.asyncio
async def test_a_success_response_reads_all_four_parameters_and_caches(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, SUCCESS_PAYLOAD)

    monkeypatch.setattr(power_module, "fetch_with_retry", _fake)

    result = await power_module.get_solar_and_climate(14.5995, 120.9842, "2026-04-01", "2026-04-02")
    assert len(result["days"]) == 2
    first = result["days"][0]
    assert first["solar_irradiance_kwh_m2"] == pytest.approx(5.2)
    assert first["temp_c"] == pytest.approx(28.1)
    assert first["precipitation_mm"] == pytest.approx(0.0)
    assert len(CACHES["nasa_power"]) == 1


@pytest.mark.asyncio
async def test_an_empty_parameter_block_is_a_valid_response_and_still_caches(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, {"properties": {"parameter": {}}})

    monkeypatch.setattr(power_module, "fetch_with_retry", _fake)

    result = await power_module.get_solar_and_climate(14.5995, 120.9842, "2026-04-01", "2026-04-02")
    assert result["days"] == []
    assert len(CACHES["nasa_power"]) == 1


@pytest.mark.asyncio
async def test_an_upstream_failure_is_never_cached(monkeypatch):
    async def _boom(client, method, url, **kwargs):
        raise httpx.ReadTimeout("nasa power slow")

    monkeypatch.setattr(power_module, "fetch_with_retry", _boom)

    result = await power_module.get_solar_and_climate(14.5995, 120.9842, "2026-04-01", "2026-04-02")
    assert result["upstream_error"] is True
    assert any("ReadTimeout" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["nasa_power"]) == 0


@pytest.mark.asyncio
async def test_a_schema_drift_body_is_reported_not_a_crash(monkeypatch):
    """A bare list instead of the documented object body must not raise."""

    async def _drifted(client, method, url, **kwargs):
        return _json_response(method, url, ["unexpected", "shape"])

    monkeypatch.setattr(power_module, "fetch_with_retry", _drifted)

    result = await power_module.get_solar_and_climate(14.5995, 120.9842, "2026-04-01", "2026-04-02")
    assert result["upstream_error"] is True
    assert len(CACHES["nasa_power"]) == 0


@pytest.mark.asyncio
async def test_a_span_over_the_cap_is_rejected_before_any_fetch(monkeypatch):
    """A 400-day span must never reach the network."""

    def _unexpected(client, method, url, **kwargs):
        raise AssertionError("fetch_with_retry must not be called for a span over the cap")

    monkeypatch.setattr(power_module, "fetch_with_retry", _unexpected)

    result = await power_module.get_solar_and_climate(14.5995, 120.9842, "2025-01-01", "2026-02-05")
    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["days"] == []
    assert len(CACHES["nasa_power"]) == 0


@pytest.mark.asyncio
async def test_a_span_within_the_cap_still_fetches(monkeypatch):
    """A 30-day span is well under the cap and must fetch normally."""

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, SUCCESS_PAYLOAD)

    monkeypatch.setattr(power_module, "fetch_with_retry", _fake)

    result = await power_module.get_solar_and_climate(14.5995, 120.9842, "2026-03-01", "2026-03-31")
    assert not result.get("validation_error")
    assert len(result["days"]) == 2


@pytest.mark.asyncio
async def test_a_non_dict_parameter_block_is_reported_not_a_crash(monkeypatch):
    """A malformed 'parameter' field (a list, not an object) must not raise."""

    async def _drifted(client, method, url, **kwargs):
        return _json_response(method, url, {"properties": {"parameter": ["broken"]}})

    monkeypatch.setattr(power_module, "fetch_with_retry", _drifted)

    result = await power_module.get_solar_and_climate(14.5995, 120.9842, "2026-04-01", "2026-04-02")
    assert not result.get("upstream_error")
    assert result["days"] == []
