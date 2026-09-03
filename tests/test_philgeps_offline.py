"""Offline unit tests for philgeps — mocks _fetch_notices to avoid the network.

test_philgeps.py is entirely live-marked (pytestmark = pytest.mark.live), so
the R2.8 heuristic-audit fixes get their own offline file here.
"""

from __future__ import annotations

from datetime import date

import pytest

from ph_civic_data_mcp.models.procurement import ProcurementRecord
from ph_civic_data_mcp.sources import philgeps as philgeps_module
from ph_civic_data_mcp.utils.cache import CACHES


def _record(
    *,
    title: str = "Construction of Farm-to-Market Road",
    agency: str = "Department of Public Works and Highways - Region V",
    region: str | None = None,
    mode: str | None = "Public Bidding",
    approved_budget: float | None = None,
    reference_number: str | None = "REF-001",
    date_published: date | None = date(2025, 4, 15),
) -> ProcurementRecord:
    return ProcurementRecord(
        reference_number=reference_number,
        title=title,
        agency=agency,
        region=region,
        mode_of_procurement=mode,
        approved_budget=approved_budget,
        currency="PHP",
        status="Open",
        date_published=date_published,
    )


# Shaped like the real Indexes scrape: region is always None, the only
# region signal, when present, lives in the agency string.
REAL_SHAPE_RECORDS = [
    _record(
        title="Construction of Farm-to-Market Road",
        agency="Department of Public Works and Highways - Region V",
        reference_number="REF-001",
    ),
    _record(
        title="Rehabilitation of Drainage System",
        agency="DPWH NCR District Engineering Office",
        reference_number="REF-002",
    ),
    _record(
        title="Supply of Office Chairs",
        agency="Department of Education Central Office",
        reference_number="REF-003",
    ),
]


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["philgeps_data"].clear()
    yield
    CACHES["philgeps_data"].clear()


def test_infer_region_prefers_given_region():
    assert philgeps_module._infer_region("DPWH NCR", "Region III") == "Region III"


def test_infer_region_from_agency_numeral():
    text = "Department of Public Works and Highways - Region V"
    assert philgeps_module._infer_region(text, None) == "Region V"


def test_infer_region_from_agency_name():
    assert philgeps_module._infer_region("DPWH NCR District Office", None) == "NCR"


def test_infer_region_none_when_no_signal():
    assert philgeps_module._infer_region("Department of Education", None) is None


@pytest.mark.asyncio
async def test_get_procurement_summary_by_region_repaired(monkeypatch):
    async def _stub():
        return list(REAL_SHAPE_RECORDS)

    monkeypatch.setattr("ph_civic_data_mcp.sources.philgeps._fetch_notices", _stub)

    summary = await philgeps_module.get_procurement_summary()
    assert summary["by_region"].get("Region V") == 1
    assert summary["by_region"].get("NCR") == 1
    assert summary["by_region"].get("unspecified") == 1


@pytest.mark.asyncio
async def test_get_procurement_summary_rules_coverage(monkeypatch):
    async def _stub():
        return list(REAL_SHAPE_RECORDS)

    monkeypatch.setattr("ph_civic_data_mcp.sources.philgeps._fetch_notices", _stub)

    summary = await philgeps_module.get_procurement_summary()
    assert summary["rules_evaluated"] == ["by_mode", "by_region"]
    reasons = {r["rule"] for r in summary["rules_not_computable"]}
    assert "total_value_php" in reasons
    assert reasons.isdisjoint(summary["rules_evaluated"])
    assert summary["data_status"] == "success"


@pytest.mark.asyncio
async def test_get_procurement_summary_upstream_failure_sets_data_status(monkeypatch):
    async def _boom():
        raise RuntimeError("philgeps offline")

    monkeypatch.setattr("ph_civic_data_mcp.sources.philgeps._fetch_notices", _boom)

    summary = await philgeps_module.get_procurement_summary()
    assert summary["data_status"] == "unavailable"
    assert summary["upstream_error"] is True
    assert summary["rules_evaluated"] == []
    assert summary["rules_not_computable"]


@pytest.mark.asyncio
async def test_fetch_notices_header_only_table_raises(monkeypatch):
    """A page that parses but yields zero rows must raise, never cache empty.

    A seven-column header-only table (no data rows) once cached as [] for
    the full 6h TTL, which read as "no procurement activity" to a caller.
    """
    html = (
        "<table>"
        "<tr><th>Ref</th><th>Title</th><th>Mode</th><th>Class</th>"
        "<th>Agency</th><th>Published</th><th>Closes</th></tr>"
        "</table>"
    )

    class _StubResponse:
        text = html

        def raise_for_status(self) -> None:
            return None

    async def _stub_fetch(client, method, url):
        return _StubResponse()

    monkeypatch.setattr(philgeps_module, "fetch_with_retry", _stub_fetch)

    with pytest.raises(RuntimeError):
        await philgeps_module._fetch_notices()

    assert CACHES["philgeps_data"] == {}


@pytest.mark.asyncio
async def test_search_procurement_date_range_excludes_undated_record(monkeypatch):
    """A date bound must drop an undated record, not let it pass by default."""
    dated = _record(reference_number="REF-DATED", date_published=date(2026, 3, 1))
    undated = _record(reference_number="REF-UNDATED", date_published=None)

    async def _stub():
        return [dated, undated]

    monkeypatch.setattr(philgeps_module, "_fetch_notices", _stub)

    results = await philgeps_module.search_procurement(
        keyword="", date_from="2026-01-01", date_to="2026-12-31", limit=100
    )
    ref_numbers = {r["reference_number"] for r in results}
    assert "REF-DATED" in ref_numbers
    assert "REF-UNDATED" not in ref_numbers


@pytest.mark.asyncio
async def test_get_procurement_summary_year_filter_excludes_undated_record(monkeypatch):
    """A year filter must drop an undated record and name the drop in caveats."""
    dated = _record(reference_number="REF-DATED", date_published=date(2026, 3, 1))
    undated = _record(reference_number="REF-UNDATED", date_published=None)

    async def _stub():
        return [dated, undated]

    monkeypatch.setattr(philgeps_module, "_fetch_notices", _stub)

    summary = await philgeps_module.get_procurement_summary(year=2026)
    assert summary["total_count"] == 1
    assert any("1" in c for c in summary["caveats"])
