"""Offline fixtures for MODIS NDVI/EVI. No live coverage existed before v0.7.0;
tests/test_v020_sources.py hits ORNL for real and is `live`-marked.

Covers: a real success, a legitimate empty subset (pixel over water), an
upstream failure on both bands (never cached), a partial failure on one band
(never cached), and a schema-drift body (non-object response).
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import modis_ndvi as modis_module
from ph_civic_data_mcp.utils.cache import CACHES


def _json_response(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


NDVI_SUBSET = {
    "subset": [
        {
            "calendar_date": "2026-08-13",
            "band": "250m_16_days_NDVI",
            "data": [5200],
        }
    ]
}

EVI_SUBSET = {
    "subset": [
        {
            "calendar_date": "2026-08-13",
            "band": "250m_16_days_EVI",
            "data": [3100],
        }
    ]
}

EMPTY_SUBSET = {"subset": []}


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["modis_ndvi"].clear()
    yield
    CACHES["modis_ndvi"].clear()


@pytest.mark.asyncio
async def test_a_success_response_reads_both_bands_and_caches(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        band = kwargs.get("params", {}).get("band", "")
        payload = NDVI_SUBSET if "NDVI" in band else EVI_SUBSET
        return _json_response(method, url, payload)

    monkeypatch.setattr(modis_module, "fetch_with_retry", _fake)

    result = await modis_module.get_vegetation_index(15.58, 121.0, "2026-08-01", "2026-08-15")
    assert not result.get("upstream_error")
    assert len(result["samples"]) == 1
    assert result["samples"][0]["ndvi"] == pytest.approx(0.52)
    assert result["samples"][0]["evi"] == pytest.approx(0.31)
    assert len(CACHES["modis_ndvi"]) == 1


@pytest.mark.asyncio
async def test_an_empty_subset_is_a_real_answer_and_still_caches(monkeypatch):
    """A pixel over water returns a real 200 with no composites. Cache it."""

    async def _fake(client, method, url, **kwargs):
        return _json_response(method, url, EMPTY_SUBSET)

    monkeypatch.setattr(modis_module, "fetch_with_retry", _fake)

    result = await modis_module.get_vegetation_index(10.0, 125.0, "2026-08-01", "2026-08-15")
    assert not result.get("upstream_error")
    assert result["samples"] == []
    assert "caveats" in result
    assert len(CACHES["modis_ndvi"]) == 1


@pytest.mark.asyncio
async def test_both_bands_failing_raises_and_is_never_cached(monkeypatch):
    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("ornl down")

    monkeypatch.setattr(modis_module, "fetch_with_retry", _boom)

    result = await modis_module.get_vegetation_index(15.58, 121.0, "2026-08-01", "2026-08-15")
    assert result["upstream_error"] is True
    assert any("ConnectError" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["modis_ndvi"]) == 0


@pytest.mark.asyncio
async def test_one_band_failing_is_a_partial_answer_and_is_never_cached(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        band = kwargs.get("params", {}).get("band", "")
        if "EVI" in band:
            raise httpx.ReadTimeout("evi slow")
        return _json_response(method, url, NDVI_SUBSET)

    monkeypatch.setattr(modis_module, "fetch_with_retry", _fake)

    result = await modis_module.get_vegetation_index(15.58, 121.0, "2026-08-01", "2026-08-15")
    assert result["upstream_error"] is True
    assert len(result["samples"]) == 1, "the band that did come back is still reported"
    assert any("ReadTimeout" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["modis_ndvi"]) == 0


@pytest.mark.asyncio
async def test_a_schema_drift_body_raises_instead_of_crashing(monkeypatch):
    """ORNL sends a bare list instead of the documented object body."""

    async def _drifted(client, method, url, **kwargs):
        return _json_response(method, url, ["not", "an", "object"])

    monkeypatch.setattr(modis_module, "fetch_with_retry", _drifted)

    result = await modis_module.get_vegetation_index(15.58, 121.0, "2026-08-01", "2026-08-15")
    assert result["upstream_error"] is True
    assert len(CACHES["modis_ndvi"]) == 0


@pytest.mark.asyncio
async def test_a_body_with_no_subset_list_raises_instead_of_reading_as_empty(monkeypatch):
    """An object body without a list 'subset' field must raise, not become []."""

    async def _degraded(client, method, url, **kwargs):
        return _json_response(method, url, {"error": "temporarily unavailable"})

    monkeypatch.setattr(modis_module, "fetch_with_retry", _degraded)

    result = await modis_module.get_vegetation_index(15.58, 121.0, "2026-08-01", "2026-08-15")
    assert result["upstream_error"] is True
    assert len(CACHES["modis_ndvi"]) == 0
