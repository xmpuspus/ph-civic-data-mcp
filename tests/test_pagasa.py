"""Live PAGASA + Open-Meteo integration tests."""

from __future__ import annotations

import os

import pytest

from ph_civic_data_mcp.sources.pagasa import (
    get_active_typhoons,
    get_weather_alerts,
    get_weather_forecast,
)


@pytest.mark.asyncio
async def test_weather_forecast_manila_open_meteo() -> None:
    """Fallback path must work without token."""
    os.environ.pop("PAGASA_API_TOKEN", None)
    result = await get_weather_forecast("Manila", days=3)
    assert result["location"] == "Manila"
    assert result["data_source"] in ("open_meteo", "pagasa_api")
    assert len(result["days"]) >= 1
    first_day = result["days"][0]
    assert first_day["temp_max_c"] is not None
    assert first_day["date"]


@pytest.mark.asyncio
async def test_weather_forecast_unknown_city_is_graceful() -> None:
    result = await get_weather_forecast("Wakanda", days=3)
    assert result["location"] == "Wakanda"
    assert "caveats" in result


@pytest.mark.asyncio
async def test_weather_forecast_cebu() -> None:
    result = await get_weather_forecast("Cebu City", days=2)
    assert len(result["days"]) >= 1


@pytest.mark.asyncio
async def test_active_typhoons_returns_list() -> None:
    result = await get_active_typhoons()
    assert isinstance(result, list)
    # Whether 0 or more, structure should not crash


@pytest.mark.asyncio
async def test_weather_alerts_returns_list() -> None:
    result = await get_weather_alerts()
    assert isinstance(result, list)
