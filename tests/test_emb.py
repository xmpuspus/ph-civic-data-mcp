"""AQICN integration tests. Skipped when AQICN_TOKEN not set."""

from __future__ import annotations

import os

import pytest

from ph_civic_data_mcp.sources.emb import get_air_quality


@pytest.mark.asyncio
async def test_missing_token_is_graceful() -> None:
    saved = os.environ.pop("AQICN_TOKEN", None)
    try:
        result = await get_air_quality("Manila")
        assert "caveats" in result
        assert any("AQICN_TOKEN" in c for c in result["caveats"])
    finally:
        if saved:
            os.environ["AQICN_TOKEN"] = saved


@pytest.mark.asyncio
async def test_unknown_city_is_graceful() -> None:
    os.environ["AQICN_TOKEN"] = os.environ.get("AQICN_TOKEN", "dummy-for-unknown-test")
    result = await get_air_quality("Wakanda")
    assert "caveats" in result


@pytest.mark.asyncio
async def test_manila_with_token() -> None:
    token = os.environ.get("AQICN_TOKEN")
    if not token or token == "dummy-for-unknown-test":
        pytest.skip("AQICN_TOKEN not set — skipping live AQICN test")
    result = await get_air_quality("Manila")
    assert result["source"] == "AQICN"
    if "caveats" not in result:
        assert isinstance(result["aqi"], int)
        assert result["aqi"] >= 0
        assert result["aqi_category"]
