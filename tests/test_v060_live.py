"""Live drift checks for the v0.6.0 PSA OpenSTAT work.

These hit the real OpenSTAT API and carry no offline fallback, so the module is
live-marked and stays out of the offline CI gate. The weekly live-drift
workflow runs them.

A transient outage degrades to a skip. A structural change fails, because that
is the drift this file exists to catch.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.psa import get_poverty_stats
from ph_civic_data_mcp.sources.psa_catalog import (
    browse_psa_catalog,
    describe_psa_dataset,
    query_psa_dataset,
)

pytestmark = pytest.mark.live


def _skip_if_upstream_down(payload: dict, label: str) -> None:
    if payload.get("upstream_error"):
        pytest.skip(f"{label} unavailable: {payload.get('caveats')}")


@pytest.mark.asyncio
async def test_poverty_resolves_through_the_live_catalog() -> None:
    """Guards the 1E -> 1F move: discovery must find poverty wherever it sits."""
    result = await get_poverty_stats()
    _skip_if_upstream_down(result, "PSA poverty")
    assert result["poverty_incidence_pct"] is not None
    assert 0 < result["poverty_incidence_pct"] < 50
    assert result["reference_year"] >= 2023
    assert "/FY/" in result["source_table"]
    assert "/DB/1E/FY/" not in result["source_table"]


@pytest.mark.asyncio
async def test_catalog_root_lists_the_poverty_subject() -> None:
    root = await browse_psa_catalog()
    _skip_if_upstream_down(root, "PSA catalog root")
    assert root["folder_count"] >= 20
    titles = {e["title"].strip().lower() for e in root["entries"]}
    assert "poverty" in titles, f"no Poverty subject in the root listing: {titles}"


@pytest.mark.asyncio
async def test_browse_describe_query_round_trip() -> None:
    """One small browse -> describe -> query pass against a stable dataset."""
    root = await browse_psa_catalog()
    _skip_if_upstream_down(root, "PSA catalog root")
    subject = next(e for e in root["entries"] if e["title"].strip().lower() == "poverty")

    groups = await browse_psa_catalog(subject["path"])
    _skip_if_upstream_down(groups, "PSA poverty subject")
    full_year = next(e for e in groups["entries"] if "full year" in e["title"].lower())

    tables = await browse_psa_catalog(full_year["path"])
    _skip_if_upstream_down(tables, "PSA full-year folder")
    assert tables["dataset_count"] >= 1
    dataset = next(e for e in tables["entries"] if e["type"] == "dataset")

    described = await describe_psa_dataset(dataset["path"])
    _skip_if_upstream_down(described, "PSA dataset metadata")
    assert described["dimensions"], "a PSA table always declares dimensions"
    assert described["time_dimensions"], "expected a Year or period dimension"

    # One cell per dimension, newest time code, so the query stays tiny.
    selections = {}
    for dim in described["dimensions"]:
        codes = [v["code"] for v in dim["values"]]
        selections[dim["code"]] = [codes[-1] if dim["is_time_like"] else codes[0]]

    result = await query_psa_dataset(dataset["path"], selections, max_rows=10)
    _skip_if_upstream_down(result, "PSA query")
    assert not result.get("validation_error"), result.get("caveats")
    assert result["requested_cells"] == 1
    assert result["row_count"] >= 1
    row = result["rows"][0]
    assert set(row) == {"keys", "labels", "value"}
    assert row["value"] is None or isinstance(row["value"], float)
    assert result["reference_period"]


@pytest.mark.asyncio
async def test_full_cube_request_is_refused_before_it_reaches_psa() -> None:
    """PSA answers a full-cube POST with a WAF 403; we must never send one."""
    root = await browse_psa_catalog()
    _skip_if_upstream_down(root, "PSA catalog root")
    subject = next(e for e in root["entries"] if e["title"].strip().lower() == "poverty")
    groups = await browse_psa_catalog(subject["path"])
    _skip_if_upstream_down(groups, "PSA poverty subject")
    full_year = next(e for e in groups["entries"] if "full year" in e["title"].lower())
    tables = await browse_psa_catalog(full_year["path"])
    _skip_if_upstream_down(tables, "PSA full-year folder")
    dataset = next(e for e in tables["entries"] if e["type"] == "dataset")

    described = await describe_psa_dataset(dataset["path"])
    _skip_if_upstream_down(described, "PSA dataset metadata")
    everything = {
        dim["code"]: [v["code"] for v in dim["values"]] for dim in described["dimensions"]
    }

    result = await query_psa_dataset(dataset["path"], everything)
    if described["total_cells"] <= 1000:
        pytest.skip("this table fits inside the cell ceiling")
    assert result["validation_error"] is True
    assert not result.get("upstream_error"), "the ceiling must stop it locally"
