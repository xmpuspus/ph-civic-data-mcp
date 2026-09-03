"""Live contract for search_hdx_datasets against the real HDX CKAN API.

sort=metadata_modified desc means the top result can change between runs, so
this asserts on shape and key presence, never on one named dataset. A
positively identified outage skips; a schema change or an empty Philippines
group fails, because that is the drift this file exists to catch.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.hdx import search_hdx_datasets
from tests.live_helpers import skip_if_outage

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_a_broad_query_returns_philippine_datasets_with_a_license_each() -> None:
    result = await search_hdx_datasets("philippines", rows=5)
    skip_if_outage(result, "HDX package_search")

    assert result["data_status"] == "success", result.get("caveats")
    assert result["upstream_error"] is False
    assert result["total_count"] >= 1
    assert 1 <= len(result["datasets"]) <= 5
    for ds in result["datasets"]:
        assert ds["name"]
        assert ds["title"]
        assert "license_id" in ds
        assert "license_title" in ds
        assert "license_url" in ds
        assert ds["hdx_url"].startswith("https://data.humdata.org/dataset/")
        assert ds["hdx_url"].endswith(ds["name"])
        assert isinstance(ds["resources"], list)
        assert len(ds["resources"]) <= 20


@pytest.mark.asyncio
async def test_a_nonsense_query_still_returns_a_valid_shape() -> None:
    """The Philippines group filter narrows results; an odd keyword can
    legitimately answer empty. Either way the shape contract must hold."""
    result = await search_hdx_datasets("zzznonexistentkeywordxyz123")
    skip_if_outage(result, "HDX package_search")

    assert result["data_status"] in ("success", "empty")
    assert result["upstream_error"] is False
    assert isinstance(result["datasets"], list)
    assert isinstance(result["total_count"], int)


@pytest.mark.asyncio
async def test_a_caller_mistake_is_never_reported_as_an_outage() -> None:
    result = await search_hdx_datasets("", rows=5)

    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["data_status"] == "invalid_request"
