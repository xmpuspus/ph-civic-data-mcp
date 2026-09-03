"""v0.7.0: World Bank must never cache a transient empty-but-200 response.

A 200 with no usable rows can mean two different things. World Bank's own
`total` count in the response metadata is the only way to tell them apart:
`total` at 0 is a real "this indicator has no data" answer, worth caching.
`total` above 0 with nothing usable back is a transient response, which must
raise instead of caching a false zero.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import world_bank as wb
from ph_civic_data_mcp.utils.cache import CACHES


def _wb_response(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["world_bank"].clear()
    yield
    CACHES["world_bank"].clear()


@pytest.mark.asyncio
async def test_fetch_observations_raises_on_a_degenerate_payload(monkeypatch):
    """metadata says 3 rows exist; the body sends none. Not a real zero."""

    async def _degenerate(client, method, url, **kwargs):
        return _wb_response(method, url, [{"page": 1, "pages": 1, "total": 3}, []])

    monkeypatch.setattr(wb, "fetch_with_retry", _degenerate)

    with pytest.raises(wb.WorldBankUpstreamError):
        await wb._fetch_observations("SP.POP.TOTL", 5)


@pytest.mark.asyncio
async def test_a_degenerate_payload_is_never_cached(monkeypatch):
    async def _degenerate(client, method, url, **kwargs):
        return _wb_response(method, url, [{"page": 1, "pages": 1, "total": 3}, []])

    monkeypatch.setattr(wb, "fetch_with_retry", _degenerate)

    result = await wb.get_world_bank_indicator("SP.POP.TOTL", per_page=5)
    assert result["upstream_error"] is True
    assert result["observations"] == []
    assert any("SP.POP.TOTL" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["world_bank"]) == 0


@pytest.mark.asyncio
async def test_a_missing_total_is_never_cached_as_a_real_zero(monkeypatch):
    """metadata carries no total at all. That is not a confirmed zero."""

    async def _no_metadata(client, method, url, **kwargs):
        return _wb_response(method, url, [{}, []])

    monkeypatch.setattr(wb, "fetch_with_retry", _no_metadata)

    result = await wb.get_world_bank_indicator("SP.POP.TOTL", per_page=5)
    assert result["upstream_error"] is True
    assert result["data_status"] in ("unavailable", "indeterminate")
    assert len(CACHES["world_bank"]) == 0


@pytest.mark.asyncio
async def test_a_real_zero_answer_still_caches(monkeypatch):
    """metadata says total is 0. The indicator truly has no data; cache it."""

    async def _real_zero(client, method, url, **kwargs):
        return _wb_response(method, url, [{"page": 1, "pages": 1, "total": 0}, []])

    monkeypatch.setattr(wb, "fetch_with_retry", _real_zero)

    result = await wb.get_world_bank_indicator("SP.POP.TOTL", per_page=5)
    assert not result.get("upstream_error")
    assert result["observations"] == []
    assert len(CACHES["world_bank"]) == 1


@pytest.mark.asyncio
async def test_a_real_payload_caches_and_is_served_from_cache(monkeypatch):
    calls = {"n": 0}

    async def _real(client, method, url, **kwargs):
        calls["n"] += 1
        return _wb_response(
            method,
            url,
            [
                {"page": 1, "pages": 1, "total": 1},
                [{"date": "2025", "value": 123.4, "indicator": {"value": "GDP"}}],
            ],
        )

    monkeypatch.setattr(wb, "fetch_with_retry", _real)

    first = await wb.get_world_bank_indicator("NY.GDP.MKTP.CD", per_page=5)
    assert not first.get("upstream_error")
    assert first["observations"][0]["value"] == pytest.approx(123.4)
    assert len(CACHES["world_bank"]) == 1

    before = calls["n"]
    again = await wb.get_world_bank_indicator("NY.GDP.MKTP.CD", per_page=5)
    assert again == first
    assert calls["n"] == before, "second call must be served from cache, not fetched again"


@pytest.mark.asyncio
async def test_a_null_data_array_raises_and_is_not_cached(monkeypatch):
    async def _null_data(client, method, url, **kwargs):
        return _wb_response(method, url, [{"page": 1, "total": 5}, None])

    monkeypatch.setattr(wb, "fetch_with_retry", _null_data)

    result = await wb.get_world_bank_indicator("SP.POP.TOTL", per_page=5)
    assert result["upstream_error"] is True
    assert len(CACHES["world_bank"]) == 0


@pytest.mark.asyncio
async def test_a_non_numeric_row_is_skipped_and_named_in_a_caveat(monkeypatch):
    """One bad row next to one good row: publish the good one, skip the bad one."""

    async def _mixed(client, method, url, **kwargs):
        return _wb_response(
            method,
            url,
            [
                {"page": 1, "pages": 1, "total": 2},
                [
                    {"date": "2025", "value": "not-a-number", "indicator": {"value": "GDP"}},
                    {"date": "2024", "value": 123.4, "indicator": {"value": "GDP"}},
                ],
            ],
        )

    monkeypatch.setattr(wb, "fetch_with_retry", _mixed)

    result = await wb.get_world_bank_indicator("NY.GDP.MKTP.CD", per_page=5)
    assert not result.get("upstream_error")
    assert len(result["observations"]) == 1
    assert result["observations"][0]["value"] == pytest.approx(123.4)
    assert any("1" in c and "skip" in c.lower() for c in result["caveats"]), result["caveats"]
    assert len(CACHES["world_bank"]) == 1


@pytest.mark.asyncio
async def test_every_row_non_numeric_is_never_cached(monkeypatch):
    """Every row fails to convert. Treat the response as degenerate, not a real zero."""

    async def _all_bad(client, method, url, **kwargs):
        return _wb_response(
            method,
            url,
            [
                {"page": 1, "pages": 1, "total": 2},
                [
                    {"date": "2025", "value": "not-a-number", "indicator": {"value": "GDP"}},
                    {"date": "2024", "value": "also-not-a-number", "indicator": {"value": "GDP"}},
                ],
            ],
        )

    monkeypatch.setattr(wb, "fetch_with_retry", _all_bad)

    result = await wb.get_world_bank_indicator("NY.GDP.MKTP.CD", per_page=5)
    assert result["upstream_error"] is True
    assert result["observations"] == []
    assert len(CACHES["world_bank"]) == 0
