"""Smoke tests for v0.2.0 sources. Live-hits real APIs; skip if network unavailable."""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.ibtracs import get_historical_typhoons_ph
from ph_civic_data_mcp.sources.modis_ndvi import get_vegetation_index
from ph_civic_data_mcp.sources.nasa_power import get_solar_and_climate
from ph_civic_data_mcp.sources.open_meteo_aq import get_air_quality
from ph_civic_data_mcp.sources.usgs import get_usgs_earthquakes_ph
from ph_civic_data_mcp.sources.world_bank import get_world_bank_indicator


@pytest.mark.asyncio
async def test_nasa_power_returns_manila_week() -> None:
    result = await get_solar_and_climate(
        14.5995, 120.9842, start_date="2026-04-01", end_date="2026-04-07"
    )
    assert result["source"] == "NASA POWER"
    assert result["latitude"] == 14.5995
    assert len(result["days"]) >= 5
    first = result["days"][0]
    assert first["solar_irradiance_kwh_m2"] is not None
    assert first["temp_c"] is not None
    assert 20 <= first["temp_c"] <= 35, "Manila April temp should be within tropical range"


@pytest.mark.asyncio
async def test_open_meteo_air_quality_manila() -> None:
    result = await get_air_quality("Manila")
    assert result["source"] == "Open-Meteo Air Quality"
    assert result["location"] == "Manila"
    assert result["pm2_5"] is not None
    assert result["us_aqi"] is not None
    assert result["aqi_category"] in {
        "Good",
        "Moderate",
        "Unhealthy for Sensitive Groups",
        "Unhealthy",
        "Very Unhealthy",
        "Hazardous",
    }


@pytest.mark.asyncio
async def test_open_meteo_air_quality_unknown_city_graceful() -> None:
    result = await get_air_quality("Atlantis")
    assert "caveats" in result


@pytest.mark.asyncio
async def test_modis_ndvi_returns_composites() -> None:
    result = await get_vegetation_index(
        15.58, 121.0, start_date="2026-01-01", end_date="2026-03-20"
    )
    assert result["source"] == "NASA MODIS via ORNL DAAC"
    assert result["product"] == "MOD13Q1"
    samples = result.get("samples", [])
    assert len(samples) >= 2, "Should get at least 2 composites over 11-week window"
    first = samples[0]
    assert -1 <= (first.get("ndvi") or 0) <= 1
    if first.get("evi") is not None:
        assert -1 <= first["evi"] <= 1


@pytest.mark.asyncio
async def test_usgs_returns_ph_bbox_events() -> None:
    results = await get_usgs_earthquakes_ph(min_magnitude=5.0, limit=20)
    assert isinstance(results, list)
    for event in results:
        assert event["source"] == "USGS FDSN"
        assert 4.0 <= event["latitude"] <= 22.0
        assert 115.0 <= event["longitude"] <= 130.0
        assert event["magnitude"] >= 5.0


@pytest.mark.asyncio
async def test_ibtracs_historical_typhoons() -> None:
    results = await get_historical_typhoons_ph(limit=5)
    assert isinstance(results, list)
    for storm in results:
        assert storm["source"] == "NOAA IBTrACS"
        assert storm["basin"] == "WP"
        assert storm["passed_within_par"] is True
        assert storm["track_points"] >= 1


@pytest.mark.asyncio
async def test_world_bank_gdp_alias() -> None:
    result = await get_world_bank_indicator("gdp", per_page=5)
    assert result["source"] == "World Bank Open Data"
    assert result["country_iso3"] == "PHL"
    assert result["indicator_id"] == "NY.GDP.MKTP.CD"
    assert len(result["observations"]) >= 3
    latest = result["observations"][0]
    assert latest["value"] > 100_000_000_000, "PH GDP should be > $100B"


@pytest.mark.asyncio
async def test_world_bank_raw_code() -> None:
    result = await get_world_bank_indicator("SP.POP.TOTL", per_page=5)
    assert result["indicator_id"] == "SP.POP.TOTL"
    assert len(result["observations"]) >= 1
