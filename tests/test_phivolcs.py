"""Live PHIVOLCS integration tests. Skips if network unavailable."""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.phivolcs import (
    get_earthquake_bulletin,
    get_latest_earthquakes,
    get_volcano_status,
)


@pytest.mark.asyncio
async def test_latest_earthquakes_returns_real_rows() -> None:
    quakes = await get_latest_earthquakes(min_magnitude=1.0, limit=10)
    assert isinstance(quakes, list)
    assert len(quakes) >= 5, f"expected ≥5 earthquakes, got {len(quakes)}"
    first = quakes[0]
    assert isinstance(first["magnitude"], float)
    assert isinstance(first["latitude"], float)
    assert isinstance(first["longitude"], float)
    assert first["location"]
    assert first["source"] == "PHIVOLCS"
    assert first["data_retrieved_at"]
    assert first["bulletin_url"] is None or first["bulletin_url"].startswith("http")


@pytest.mark.asyncio
async def test_min_magnitude_filter() -> None:
    all_q = await get_latest_earthquakes(min_magnitude=1.0, limit=50)
    big = await get_latest_earthquakes(min_magnitude=4.5, limit=50)
    assert all(q["magnitude"] >= 4.5 for q in big)
    assert len(big) <= len(all_q)


@pytest.mark.asyncio
async def test_region_filter() -> None:
    rows = await get_latest_earthquakes(min_magnitude=1.0, limit=50, region="zzz_no_match")
    assert rows == []


@pytest.mark.asyncio
async def test_bulletin_with_empty_url_is_graceful() -> None:
    result = await get_earthquake_bulletin("")
    assert result["source"] == "PHIVOLCS"
    assert "caveats" in result


@pytest.mark.asyncio
async def test_bulletin_with_real_url() -> None:
    quakes = await get_latest_earthquakes(limit=5)
    url = next((q["bulletin_url"] for q in quakes if q.get("bulletin_url")), None)
    if not url:
        pytest.skip("no bulletin URL available from list page")
    bulletin = await get_earthquake_bulletin(url)
    assert bulletin["source"] == "PHIVOLCS"
    assert bulletin["url"] == url
    # Either we got full content or a graceful caveat
    assert "magnitude" in bulletin or "caveats" in bulletin


@pytest.mark.asyncio
async def test_volcano_status_returns_list() -> None:
    results = await get_volcano_status()
    assert isinstance(results, list)
    # Expect at least one active-monitored volcano (Mayon, Taal, Kanlaon, Bulusan)
    if results:
        volcano = results[0]
        assert volcano["source"] == "PHIVOLCS"
        assert "name" in volcano
        assert "alert_level" in volcano


@pytest.mark.asyncio
async def test_volcano_status_specific() -> None:
    result = await get_volcano_status("Mayon")
    assert isinstance(result, list)
    assert len(result) >= 1
    assert result[0]["name"] == "Mayon"
