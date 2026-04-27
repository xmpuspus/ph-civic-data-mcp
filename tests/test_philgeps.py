"""Live PhilGEPS integration tests."""

from __future__ import annotations

import time

import pytest

from ph_civic_data_mcp.sources.philgeps import (
    get_procurement_summary,
    search_procurement,
)


@pytest.mark.asyncio
async def test_search_procurement_returns_records() -> None:
    results = await search_procurement(keyword="", limit=20)
    assert isinstance(results, list)
    assert len(results) >= 1
    first = results[0]
    assert first["title"]
    assert first["agency"]
    assert first["source"] == "PhilGEPS"
    assert first["data_retrieved_at"]


@pytest.mark.asyncio
async def test_keyword_filter() -> None:
    results = await search_procurement(keyword="zzz_no_match_xyz", limit=50)
    assert results == []


@pytest.mark.asyncio
async def test_agency_filter_case_insensitive() -> None:
    results_all = await search_procurement(keyword="", limit=50)
    if not results_all:
        pytest.skip("no procurement notices available")
    target_agency = results_all[0]["agency"]
    results = await search_procurement(keyword="", agency=target_agency[:10], limit=50)
    assert all(target_agency[:10].lower() in r["agency"].lower() for r in results)


@pytest.mark.asyncio
async def test_cache_hit_is_fast() -> None:
    # Warm the cache
    await search_procurement(keyword="", limit=10)
    start = time.perf_counter()
    results = await search_procurement(keyword="", limit=10)
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 200, f"cached search took {elapsed_ms:.1f}ms, expected <200ms"
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_procurement_summary_structure() -> None:
    summary = await get_procurement_summary()
    assert "total_count" in summary
    assert "by_mode" in summary
    assert "top_agencies" in summary
    assert summary["source"] == "PhilGEPS"
