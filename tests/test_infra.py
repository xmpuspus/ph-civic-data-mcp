"""Unit tests for infra source — mocks _fetch_notices to avoid PhilGEPS network."""

from __future__ import annotations

from datetime import date

import pytest

from ph_civic_data_mcp.models.procurement import ProcurementRecord
from ph_civic_data_mcp.sources import infra as infra_module
from ph_civic_data_mcp.utils.cache import CACHES


def _record(
    *,
    title: str,
    agency: str = "Department of Public Works and Highways - Region III",
    region: str | None = "Region III",
    mode: str | None = "Public Bidding",
    status: str | None = "Open",
    date_published: date | None = date(2025, 4, 15),
    approved_budget: float | None = None,
    reference_number: str | None = None,
) -> ProcurementRecord:
    return ProcurementRecord(
        reference_number=reference_number or f"REF-{abs(hash(title)) % 10**8}",
        title=title,
        agency=agency,
        region=region,
        mode_of_procurement=mode,
        approved_budget=approved_budget,
        currency="PHP",
        status=status,
        date_published=date_published,
    )


SAMPLE_RECORDS = [
    _record(
        title="Construction of Flood Control Structure along Pampanga River",
        approved_budget=120_000_000,
        reference_number="PHILGEPS-INF-001",
    ),
    _record(
        title="Rehabilitation of Barangay Road in San Fernando",
        approved_budget=None,
        reference_number="PHILGEPS-INF-002",
    ),
    _record(
        title="Construction of Bridge over Pasig River",
        agency="DPWH NCR District",
        region="NCR",
        approved_budget=850_000_000,
        reference_number="PHILGEPS-INF-003",
    ),
    _record(
        title="Supply of office stationery",
        agency="DepEd",
        approved_budget=1_500_000,
        reference_number="PHILGEPS-NON-001",
    ),
    _record(
        title="Construction of School Building Phase 2",
        agency="DPWH Region V",
        region="Region V",
        approved_budget=45_000_000,
        reference_number="PHILGEPS-INF-004",
    ),
    _record(
        title="Construction of Flood Control Structure along Pampanga River",
        agency="Department of Public Works and Highways - Region III",
        region="Region III",
        approved_budget=None,
        reference_number="PHILGEPS-INF-005",
    ),
]


@pytest.fixture(autouse=True)
def _mock_notices(monkeypatch):
    async def _stub():
        return list(SAMPLE_RECORDS)

    monkeypatch.setattr("ph_civic_data_mcp.sources.infra._fetch_notices", _stub)
    CACHES["infra_projects"].clear()
    yield


@pytest.mark.asyncio
async def test_search_infra_projects_keyword_filter():
    results = await infra_module.search_infra_projects(keyword="flood control")
    assert results
    assert all("flood control" in r["title"].lower() for r in results)
    for r in results:
        assert r["source"] == "PhilGEPS"
        assert r["source_url"]
        assert r["license"]
        assert "data_retrieved_at" in r
        assert r["category"] == "flood control"


@pytest.mark.asyncio
async def test_search_infra_excludes_non_infra():
    """Office stationery should not appear in any infra search."""
    results = await infra_module.search_infra_projects(keyword="stationery")
    assert results == []


@pytest.mark.asyncio
async def test_search_infra_min_cost_filter():
    results = await infra_module.search_infra_projects(min_cost_php=100_000_000)
    assert results
    assert all((r["cost_php"] or 0) >= 100_000_000 for r in results)


@pytest.mark.asyncio
async def test_search_infra_region_filter():
    results = await infra_module.search_infra_projects(region="ncr")
    assert results
    # All matched records must reference NCR somewhere
    for r in results:
        text = f"{r['title']} {r['agency']} {r['region'] or ''}".lower()
        assert "ncr" in text


@pytest.mark.asyncio
async def test_get_infra_project_by_id():
    result = await infra_module.get_infra_project("PHILGEPS-INF-003")
    assert result["matched"] is True
    assert "Bridge" in result["title"]
    assert result["cost_php"] == 850_000_000
    assert result["source_url"]


@pytest.mark.asyncio
async def test_get_infra_project_unknown_id():
    result = await infra_module.get_infra_project("DOES-NOT-EXIST")
    assert result["matched"] is False
    assert result["caveats"]


@pytest.mark.asyncio
async def test_summarize_infra_spending_aggregates():
    summary = await infra_module.summarize_infra_spending()
    assert summary["total_count"] >= 4  # excludes the stationery non-infra
    assert "by_category" in summary
    assert summary["by_category"].get("flood control", 0) >= 1
    assert summary["disclaimer"]
    assert summary["source"] == "PhilGEPS"
    assert summary["source_url"]
    assert summary["license"]
    # total_value should be the sum of known approved budgets among filtered infra rows
    known = [
        r.approved_budget
        for r in (SAMPLE_RECORDS[0], SAMPLE_RECORDS[2], SAMPLE_RECORDS[4])
        if r.approved_budget
    ]
    assert summary["total_value_php"] == pytest.approx(sum(known))


@pytest.mark.asyncio
async def test_search_graceful_when_upstream_dies(monkeypatch):
    async def _boom():
        raise RuntimeError("philgeps offline")

    monkeypatch.setattr("ph_civic_data_mcp.sources.infra._fetch_notices", _boom)
    CACHES["infra_projects"].clear()
    results = await infra_module.search_infra_projects(keyword="flood")
    # Empty list, never raises
    assert results == []


def test_categorize():
    """Pure helper unit test."""

    class R:
        def __init__(self, title: str, mode: str | None = None) -> None:
            self.title = title
            self.mode_of_procurement = mode

    assert infra_module._categorize(R("Flood Control project")) == "flood control"
    assert infra_module._categorize(R("Construction of Bridge")) == "bridge"
    assert infra_module._categorize(R("Asphalt Overlay on highway")) == "road / highway"
    assert (
        infra_module._categorize(R("Construction of School Building Phase 2")) == "school building"
    )
