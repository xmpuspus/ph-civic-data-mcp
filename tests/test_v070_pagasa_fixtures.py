"""Offline fixtures for the PAGASA TenDay API path (_pagasa_api_forecast).

tests/test_v031_fixes.py and tests/test_v050_fixes.py already mock
get_weather_alerts and get_active_typhoons offline; tests/test_pagasa.py is
`live`-marked. The TenDay/get_weather_forecast path itself had no offline
coverage before v0.7.0.

Covers: a real 0mm rainfall reading surviving as 0 (not falling through to
'precip'), a bare list of non-dict items not crashing the parser, a real
success, an upstream failure with fallback, and a schema-drift 'days' field.
"""

from __future__ import annotations

import os

import httpx
import pytest

from ph_civic_data_mcp.sources import pagasa as pagasa_module
from ph_civic_data_mcp.utils.cache import CACHES


def _json_response(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


TOKEN = "test-token"


@pytest.fixture(autouse=True)
def _clear_state(monkeypatch):
    monkeypatch.setenv("PAGASA_API_TOKEN", TOKEN)
    CACHES["pagasa_forecast"].clear()
    yield
    CACHES["pagasa_forecast"].clear()


@pytest.mark.asyncio
async def test_a_real_zero_rainfall_reading_survives_as_zero(monkeypatch):
    """`rainfall or precip` treated a real 0mm day as missing and fell to precip."""
    payload = {
        "days": [
            {"date": "2026-08-13", "rainfall": 0, "precip": 12.5, "min_temp": 24, "max_temp": 31}
        ]
    }

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, payload)

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module._pagasa_api_forecast("Manila", 1, TOKEN)
    assert result is not None
    assert result["days"][0]["rainfall_mm"] == 0, "a real 0mm reading must not become 12.5"


@pytest.mark.asyncio
async def test_a_missing_rainfall_key_falls_back_to_precip(monkeypatch):
    payload = {"days": [{"date": "2026-08-13", "precip": 9.0, "min_temp": 24, "max_temp": 31}]}

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, payload)

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module._pagasa_api_forecast("Manila", 1, TOKEN)
    assert result["days"][0]["rainfall_mm"] == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_a_bare_list_of_non_dict_items_does_not_crash(monkeypatch):
    """Schema drift: PAGASA sends a list of strings instead of the documented object."""

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, ["error", "come back later", None])

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module._pagasa_api_forecast("Manila", 3, TOKEN)
    assert result is None, "an unparseable body must fall back, never crash"


@pytest.mark.asyncio
async def test_a_days_list_of_non_dict_entries_does_not_crash(monkeypatch):
    """Schema drift one level down: 'days' itself holds non-dict rows."""
    payload = {"days": ["not-a-day", None, {"date": "2026-08-13", "rainfall": 4}]}

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, payload)

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module._pagasa_api_forecast("Manila", 3, TOKEN)
    assert result is not None
    assert len(result["days"]) == 1
    assert result["days"][0]["rainfall_mm"] == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_a_real_success_response_is_cached_by_get_weather_forecast(monkeypatch):
    payload = {
        "days": [
            {"date": "2026-08-13", "rainfall": 2.0, "min_temp": 24, "max_temp": 31},
            {"date": "2026-08-14", "rainfall": 0, "min_temp": 23, "max_temp": 30},
        ]
    }
    calls = {"n": 0}

    async def _fake(client, method, url, **kwargs):
        calls["n"] += 1
        return _json_response(method, url, payload)

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    first = await pagasa_module.get_weather_forecast("Manila", days=2)
    assert first["data_source"] == "pagasa_api"
    assert len(first["days"]) == 2
    assert first["days"][1]["rainfall_mm"] == 0

    before = calls["n"]
    again = await pagasa_module.get_weather_forecast("Manila", days=2)
    assert again == first
    assert calls["n"] == before, "second call must be served from cache"


@pytest.mark.asyncio
async def test_a_tenday_outage_falls_back_to_open_meteo(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        if "tenday" in url:
            raise httpx.ConnectError("tenday down")
        return _json_response(
            method,
            url,
            {
                "daily": {
                    "time": ["2026-08-13"],
                    "temperature_2m_max": [31.0],
                    "temperature_2m_min": [24.0],
                    "precipitation_sum": [0.0],
                    "windspeed_10m_max": [10.0],
                    "winddirection_10m_dominant": [90.0],
                    "weathercode": [1],
                }
            },
        )

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module.get_weather_forecast("Manila", days=1)
    assert result["data_source"] == "open_meteo"
    assert not result.get("upstream_error")
    assert len(result["days"]) == 1


@pytest.mark.asyncio
async def test_both_sources_down_is_reported_and_not_cached(monkeypatch):
    os.environ.pop("PAGASA_API_TOKEN", None)

    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("everything is down")

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _boom)

    result = await pagasa_module.get_weather_forecast("Manila", days=1)
    assert result["upstream_error"] is True
    assert any("ConnectError" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["pagasa_forecast"]) == 0
