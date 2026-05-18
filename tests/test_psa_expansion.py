"""Live PSA-expansion integration tests (v0.4.0). Skips gracefully offline.

Mirrors tests/test_phivolcs.py: hits the real PSA OpenSTAT PXWeb API and
asserts the response shape + envelope, with graceful-degradation paths covered.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.psa import (
    get_health_indicators,
    get_inflation_stats,
    get_labor_stats,
)


def _has_envelope(result: dict) -> None:
    assert result["source"] == "PSA"
    assert result["source_url"].startswith("http")
    assert result["license"]
    assert result["data_retrieved_at"]


@pytest.mark.asyncio
async def test_inflation_national_returns_real_figure() -> None:
    result = await get_inflation_stats()
    _has_envelope(result)
    if result.get("caveats"):
        pytest.skip(f"PSA CPI unavailable: {result['caveats']}")
    assert isinstance(result["headline_inflation_pct"], float)
    # PH CPI inflation has stayed within a sane band; guard against unit bugs.
    assert -5.0 <= result["headline_inflation_pct"] <= 60.0
    assert result["reference_period"]
    assert result["base_year"] == "2018"


@pytest.mark.asyncio
async def test_inflation_region_differs_from_national_shape() -> None:
    result = await get_inflation_stats("NCR")
    _has_envelope(result)
    if result.get("caveats"):
        pytest.skip(f"PSA CPI regional unavailable: {result['caveats']}")
    assert "national capital" in result["area"].lower() or "ncr" in result["area"].lower()
    assert isinstance(result["headline_inflation_pct"], float)


@pytest.mark.asyncio
async def test_inflation_unknown_area_is_graceful() -> None:
    result = await get_inflation_stats("zzz_not_a_region")
    _has_envelope(result)
    assert result["caveats"]
    assert result["headline_inflation_pct"] is None


@pytest.mark.asyncio
async def test_labor_stats_returns_rates() -> None:
    result = await get_labor_stats()
    _has_envelope(result)
    if not result.get("reference_period"):
        pytest.skip(f"PSA LFS unavailable: {result.get('caveats')}")
    for field in (
        "employment_rate_pct",
        "unemployment_rate_pct",
        "underemployment_rate_pct",
        "labor_force_participation_rate_pct",
    ):
        assert isinstance(result[field], float), field
        assert 0.0 <= result[field] <= 100.0
    assert result["reference_period"]


@pytest.mark.asyncio
async def test_labor_region_records_national_caveat() -> None:
    result = await get_labor_stats("Cebu")
    _has_envelope(result)
    assert any("national" in c.lower() for c in result["caveats"])


@pytest.mark.asyncio
async def test_health_default_set_returns_indicators() -> None:
    result = await get_health_indicators()
    assert result["source"] == "PSA"
    assert isinstance(result["available_indicators"], list)
    assert result["available_indicators"]
    if not result["indicators"]:
        pytest.skip(f"PSA 1D unavailable: {result.get('caveats')}")
    first = result["indicators"][0]
    assert first["indicator"]
    assert first["source"] == "PSA"
    assert "source_table" in first


@pytest.mark.asyncio
async def test_health_specific_indicator_match() -> None:
    result = await get_health_indicators("fertility")
    assert result["source"] == "PSA"
    if not result["indicators"]:
        pytest.skip(f"PSA 1D unavailable: {result.get('caveats')}")
    assert any("fertility" in i["indicator"].lower() for i in result["indicators"])


@pytest.mark.asyncio
async def test_health_no_match_is_graceful() -> None:
    result = await get_health_indicators("zzz_not_an_indicator")
    assert result["source"] == "PSA"
    assert result["indicators"] == []
    assert result["caveats"]
    assert result["available_indicators"]
