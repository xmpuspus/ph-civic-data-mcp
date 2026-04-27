"""Live smoke tests for v0.3.0. Hits real upstream endpoints.

Run with:  uv run pytest -v -m live tests/test_v030_live.py
Skipped by default; opt-in via -m live.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.server import get_data_freshness
from ph_civic_data_mcp.sources import psgc as psgc_module
from ph_civic_data_mcp.sources import infra as infra_module
from ph_civic_data_mcp.sources import cross_source as cs

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_live_resolve_cebu_city():
    result = await psgc_module.resolve_ph_location("Cebu City")
    assert result.get("matched") is not False
    assert result["psgc_code"]
    assert "cebu" in result["name"].lower()
    assert result["source"] == "PSGC"
    assert result["source_url"]
    assert result["license"]


@pytest.mark.asyncio
async def test_live_resolve_pampanga():
    result = await psgc_module.resolve_ph_location("Pampanga")
    assert result.get("matched") is not False
    assert result["psgc_code"]
    assert result["level"] in ("province", "region", "city", "city-municipality")


@pytest.mark.asyncio
async def test_live_list_regions():
    regions = await psgc_module.list_admin_units()
    assert isinstance(regions, list)
    assert len(regions) >= 17  # 17 PH regions
    names = [r["name"] for r in regions]
    assert any("Capital" in n or "NCR" in n for n in names)


@pytest.mark.asyncio
async def test_live_hierarchy_for_resolved_city():
    resolved = await psgc_module.resolve_ph_location("Cebu City")
    code = resolved["psgc_code"]
    hierarchy = await psgc_module.get_location_hierarchy(code)
    chain = hierarchy["chain"]
    assert chain
    levels = [link["level"] for link in chain]
    # Cebu City -> Cebu province -> Region VII
    assert levels[0] == "region"


@pytest.mark.asyncio
async def test_live_search_infra_keyword_flood():
    """PhilGEPS: latest ~100 notices may or may not contain flood-control items.
    Either way, response shape must be valid."""
    results = await infra_module.search_infra_projects(keyword="flood")
    assert isinstance(results, list)
    for r in results:
        assert r["source"] == "PhilGEPS"
        assert r["source_url"]
        assert r["license"]
        assert "data_retrieved_at" in r


@pytest.mark.asyncio
async def test_live_summarize_returns_disclaimer():
    summary = await infra_module.summarize_infra_spending()
    assert summary["disclaimer"]
    assert summary["source"] == "PhilGEPS"
    assert summary["source_url"]
    assert summary["total_count"] >= 0


@pytest.mark.asyncio
async def test_live_flag_anomalies_returns_disclaimer_and_metadata():
    result = await cs.flag_infra_anomalies(min_cost_php=50_000_000)
    assert result["disclaimer"]
    assert result["source"] == "PhilGEPS + PHIVOLCS + PAGASA"
    assert "flagged" in result
    assert "rules_summary" in result
    assert "assessment_datetime" in result


@pytest.mark.asyncio
async def test_live_data_freshness():
    freshness = await get_data_freshness()
    assert freshness["server_version"] == "0.3.0"
    assert isinstance(freshness["sources"], list)
    assert any(s["source"] == "PSGC" for s in freshness["sources"])
