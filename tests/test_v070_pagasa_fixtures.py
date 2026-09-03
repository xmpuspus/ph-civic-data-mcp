"""Offline fixtures for the PAGASA TenDay API path (_pagasa_api_forecast) and
the get_active_typhoons bulletin parser.

tests/test_v031_fixes.py and tests/test_v050_fixes.py already mock
get_weather_alerts and get_active_typhoons offline; tests/test_pagasa.py is
`live`-marked. The TenDay/get_weather_forecast path itself had no offline
coverage before v0.7.0.

Covers: a real 0mm rainfall reading surviving as 0 (not falling through to
'precip'), a bare list of non-dict items not crashing the parser, a real
success, an upstream failure with fallback, a schema-drift 'days' field, and
an unrecognized bulletin page that must not be read as "no active typhoons".
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
async def test_a_real_zero_temperature_reading_survives_as_zero(monkeypatch):
    """Same falsy-zero bug as rainfall: `min_temp or tmin` dropped a real 0."""
    payload = {
        "days": [
            {
                "date": "2026-08-13",
                "rainfall": 1.0,
                "min_temp": 0,
                "tmin": 7,
                "max_temp": 0,
                "tmax": 15,
            }
        ]
    }

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, payload)

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module._pagasa_api_forecast("Mount Pulag", 1, TOKEN)
    assert result is not None
    day = result["days"][0]
    assert day["temp_min_c"] == 0, "a real 0 degree low must not become 7"
    assert day["temp_max_c"] == 0, "a real 0 degree high must not become 15"


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


def _html_response(method: str, url: str, text: str) -> httpx.Response:
    return httpx.Response(200, text=text, request=httpx.Request(method, url))


@pytest.mark.asyncio
async def test_an_unrecognized_bulletin_page_is_indeterminate_not_cached_empty(monkeypatch):
    """Neither the 'no active cyclone' marker nor a cyclone name matches, so
    this must not be read as, and cached as, 'no active typhoons'."""
    CACHES["pagasa_typhoons"].clear()

    async def _fake(client, method, url, **kwargs):
        return _html_response(
            method, url, "<html><body>Typhoon bulletin format changed</body></html>"
        )

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module.get_active_typhoons()
    assert isinstance(result, dict)
    assert result["upstream_error"] is True
    assert result["data_status"] == "indeterminate"
    assert any("not recognized" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["pagasa_typhoons"]) == 0


@pytest.mark.asyncio
async def test_the_explicit_no_active_marker_still_returns_and_caches_empty(monkeypatch):
    """The confirmed 'no active cyclone' state must keep returning a bare []."""
    CACHES["pagasa_typhoons"].clear()

    async def _fake(client, method, url, **kwargs):
        return _html_response(
            method, url, "<html><body>No Active Tropical Cyclone in the PAR</body></html>"
        )

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module.get_active_typhoons()
    assert result == []
    assert len(CACHES["pagasa_typhoons"]) == 1


@pytest.mark.asyncio
async def test_open_meteo_empty_daily_time_is_indeterminate_not_cached(monkeypatch):
    """A 200 with daily.time: [] must not become a clean days: [] success.

    days is always 1-10, so an empty daily.time array is malformed upstream
    data, not a real zero-day forecast.
    """

    async def _fake(client, method, url, **kwargs):
        if "tenday" in url:
            raise httpx.ConnectError("tenday down")
        return _json_response(method, url, {"daily": {"time": []}})

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module.get_weather_forecast("Manila", days=1)
    assert result["upstream_error"] is True
    assert result["data_status"] == "indeterminate"
    assert len(CACHES["pagasa_forecast"]) == 0


@pytest.mark.asyncio
async def test_an_unrecognized_alerts_page_is_indeterminate_not_cached_empty(monkeypatch):
    """Neither the 'no active warnings' marker nor a recognized shape
    matches, so this must not be read as, and cached as, 'no active
    warnings'."""
    CACHES["pagasa_alerts"].clear()

    async def _fake(client, method, url, **kwargs):
        return _html_response(
            method,
            url,
            "<html><body><section>Heavy Rainfall Warning for NCR</section></body></html>",
        )

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module.get_weather_alerts()
    assert isinstance(result, dict)
    assert result["upstream_error"] is True
    assert result["data_status"] == "indeterminate"
    assert len(CACHES["pagasa_alerts"]) == 0


@pytest.mark.asyncio
async def test_the_explicit_no_active_warnings_marker_still_returns_and_caches_empty(monkeypatch):
    """The confirmed 'no active warnings' state must keep returning a bare []."""
    CACHES["pagasa_alerts"].clear()

    async def _fake(client, method, url, **kwargs):
        return _html_response(method, url, "<html><body>No Active Warnings</body></html>")

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module.get_weather_alerts()
    assert result == []
    assert len(CACHES["pagasa_alerts"]) == 1


@pytest.mark.asyncio
async def test_open_meteo_scalar_daily_time_is_indeterminate_not_cached(monkeypatch):
    """A 200 with daily.time as a bare string must not iterate character by
    character into a garbage days list. It must take the same malformed
    path as a missing or empty daily.time list."""

    async def _fake(client, method, url, **kwargs):
        if "tenday" in url:
            raise httpx.ConnectError("tenday down")
        return _json_response(method, url, {"daily": {"time": "2026-09-04"}})

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _fake)

    result = await pagasa_module.get_weather_forecast("Manila", days=1)
    assert result["upstream_error"] is True
    assert result["data_status"] == "indeterminate"
    assert len(CACHES["pagasa_forecast"]) == 0


@pytest.mark.asyncio
async def test_tenday_non_json_body_falls_back_to_open_meteo(monkeypatch):
    """A 200 whose body is not JSON must fall back like a fetch failure,
    never raise JSONDecodeError out of the tool."""

    async def _fake(client, method, url, **kwargs):
        if "tenday" in url:
            return httpx.Response(200, text="not-json", request=httpx.Request(method, url))
        return _json_response(
            method,
            url,
            {
                "daily": {
                    "time": ["2026-09-04"],
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
