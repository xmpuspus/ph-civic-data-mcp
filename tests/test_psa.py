"""Live PSA PXWeb integration tests."""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.psa import get_population_stats, get_poverty_stats


@pytest.mark.asyncio
async def test_population_national() -> None:
    result = await get_population_stats()
    assert "population" in result
    assert result["population"] > 100_000_000, "PH pop should exceed 100M"
    assert result["source"] == "PSA"
    assert result["reference_note"]


@pytest.mark.asyncio
async def test_population_ncr() -> None:
    result = await get_population_stats(region="NCR")
    assert "population" in result
    assert result["population"] > 10_000_000


@pytest.mark.asyncio
async def test_population_unknown_region() -> None:
    result = await get_population_stats(region="Wakanda")
    assert "caveats" in result


@pytest.mark.asyncio
async def test_poverty_national() -> None:
    result = await get_poverty_stats()
    assert "poverty_incidence_pct" in result
    # 2023 national was ~10.9%
    assert 0 < result["poverty_incidence_pct"] < 50
    assert result["source"] == "PSA"
    assert result["reference_year"] >= 2023


@pytest.mark.asyncio
async def test_poverty_region() -> None:
    result = await get_poverty_stats(region="Bicol")
    assert "poverty_incidence_pct" in result or "caveats" in result
