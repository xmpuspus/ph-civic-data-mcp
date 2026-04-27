"""Unit tests for flag_infra_anomalies and get_data_freshness."""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.server import get_data_freshness
from ph_civic_data_mcp.sources import cross_source as cs


@pytest.fixture(autouse=True)
def _patch_dependencies(monkeypatch):
    async def _projects(*, region=None, province=None, limit=100, **_):
        return [
            {
                "project_id": "P-001",
                "title": "Construction of flood control along pampanga river",
                "agency": "DPWH Region III",
                "region": "Region III",
                "category": "flood control",
                "cost_php": 120_000_000,
                "progress_pct": None,
                "source_url": "https://www.philgeps.gov.ph/",
            },
            {
                "project_id": "P-002",
                "title": "Construction of flood control along pampanga river",
                "agency": "DPWH Region III",
                "region": "Region III",
                "category": "flood control",
                "cost_php": None,
                "progress_pct": None,
                "source_url": "https://www.philgeps.gov.ph/",
            },
            {
                "project_id": "P-003",
                "title": "Bridge construction in batanes",
                "agency": "DPWH NCR",
                "region": "NCR",
                "category": "bridge",
                "cost_php": 30_000_000,  # below threshold
                "progress_pct": None,
                "source_url": "https://www.philgeps.gov.ph/",
            },
        ]

    async def _quakes(min_magnitude=4.0, limit=50, **_):
        from datetime import datetime, timezone

        return [
            {
                "datetime_pst": datetime.now(timezone.utc).isoformat(),
                "magnitude": 5.4,
                "location": "12 km NW of Batanes (PHILIPPINES)",
            },
        ]

    async def _typhoons():
        return [
            {
                "local_name": "Bagyong Pepito",
                "category": "Tropical Storm",
                "signal_numbers": {"Pampanga": 1},
                "max_winds_kph": 90,
                "within_par": True,
            }
        ]

    monkeypatch.setattr(cs, "search_infra_projects", _projects)
    monkeypatch.setattr(cs, "get_latest_earthquakes", _quakes)
    monkeypatch.setattr(cs, "get_active_typhoons", _typhoons)
    yield


@pytest.mark.asyncio
async def test_flag_infra_anomalies_returns_disclaimer_and_metadata():
    result = await cs.flag_infra_anomalies()
    assert result["disclaimer"]
    assert result["source"] == "PhilGEPS + PHIVOLCS + PAGASA"
    assert result["source_url"]
    assert result["license"]
    assert "assessment_datetime" in result
    assert result["projects_examined"] == 3


@pytest.mark.asyncio
async def test_flag_high_cost_no_progress_fires():
    result = await cs.flag_infra_anomalies(min_cost_php=100_000_000)
    rules = {f["rule_fired"] for f in result["flagged"]}
    assert "high_cost_no_progress" in rules
    assert any(f["project_id"] == "P-001" for f in result["flagged"])


@pytest.mark.asyncio
async def test_flag_duplicate_titles_fires():
    result = await cs.flag_infra_anomalies()
    rules = [f["rule_fired"] for f in result["flagged"]]
    assert rules.count("duplicate_titles_same_agency") >= 2


@pytest.mark.asyncio
async def test_flag_hazard_overlap_fires():
    """Bridge in batanes should overlap quake hazard footprint ('batanes')."""
    result = await cs.flag_infra_anomalies()
    hazard_flags = [f for f in result["flagged"] if f["rule_fired"] == "hazard_overlap"]
    assert hazard_flags
    assert any("batanes" in f["title"].lower() for f in hazard_flags)


@pytest.mark.asyncio
async def test_flag_graceful_on_partial_failure(monkeypatch):
    async def _boom_quakes(*args, **kwargs):
        raise RuntimeError("phivolcs offline")

    monkeypatch.setattr(cs, "get_latest_earthquakes", _boom_quakes)
    result = await cs.flag_infra_anomalies()
    assert "PHIVOLCS fetch failed" in " ".join(result["caveats"])
    # Still returns; not crashing.
    assert "flagged" in result


@pytest.mark.asyncio
async def test_get_data_freshness_returns_full_catalog():
    result = await get_data_freshness()
    assert result["server_version"] == "0.3.0"
    assert isinstance(result["sources"], list)
    assert len(result["sources"]) >= 10
    for entry in result["sources"]:
        assert entry["source"]
        assert entry["source_url"]
        assert entry["freshness"]
        assert isinstance(entry["cache_ttl_seconds"], int)
        assert entry["license"]
