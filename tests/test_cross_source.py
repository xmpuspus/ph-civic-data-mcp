"""Cross-source assess_area_risk tests."""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.cross_source import assess_area_risk


@pytest.mark.asyncio
async def test_assess_area_risk_returns_structured_output() -> None:
    result = await assess_area_risk("Manila")
    assert result["location"] == "Manila"
    assert result["earthquake_risk_level"] in {"Low", "Moderate", "High", "Very High"}
    assert "recent_earthquakes_30d" in result
    assert isinstance(result["recent_earthquakes_30d"], int)
    assert "caveats" in result
    assert result["source"] == "PHIVOLCS + PAGASA"


@pytest.mark.asyncio
async def test_assess_area_risk_unknown_location() -> None:
    result = await assess_area_risk("Wakanda")
    assert result["location"] == "Wakanda"
    # Earthquake filter will return 0, typhoons list likely empty
    assert result["earthquake_risk_level"] == "Low"
