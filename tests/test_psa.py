"""Live PSA PXWeb integration tests."""

from __future__ import annotations

import pytest


from ph_civic_data_mcp.sources.psa import get_population_stats, get_poverty_stats
from tests.live_helpers import skip_if_outage as _skip_if_upstream_down

# Hits the live PSA PXWeb API with no offline fallback — live-marked so a
# transient upstream outage cannot red the offline CI gate (`pytest -x`).
#
# The guard skips on a positively identified outage only. The old one skipped
# on any `caveats` entry, which kept the weekly run green for weeks while
# population discovery returned nothing.
pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_population_national() -> None:
    result = await get_population_stats()
    _skip_if_upstream_down(result, "PSA population")
    assert "population" in result
    assert result["population"] > 100_000_000, "PH pop should exceed 100M"
    assert result["source"] == "PSA"
    assert result["reference_note"]


@pytest.mark.asyncio
async def test_population_ncr() -> None:
    result = await get_population_stats(region="NCR")
    _skip_if_upstream_down(result, "PSA population")
    assert "population" in result
    assert result["population"] > 10_000_000


@pytest.mark.asyncio
async def test_population_unknown_region() -> None:
    result = await get_population_stats(region="Wakanda")
    _skip_if_upstream_down(result, "PSA population")
    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert "caveats" in result


@pytest.mark.asyncio
async def test_poverty_national() -> None:
    result = await get_poverty_stats()
    _skip_if_upstream_down(result, "PSA poverty")
    assert "poverty_incidence_pct" in result
    # 2023 national was ~10.9%
    assert 0 < result["poverty_incidence_pct"] < 50
    assert result["source"] == "PSA"
    assert result["reference_year"] >= 2023


@pytest.mark.asyncio
async def test_poverty_region() -> None:
    result = await get_poverty_stats(region="Bicol")
    assert "poverty_incidence_pct" in result or "caveats" in result
