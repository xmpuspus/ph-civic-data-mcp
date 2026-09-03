"""Offline tests for the v0.7.0 PSA defect fixes.

Covers, with no live HTTP:
- _pick_latest_table must not silently serve a backcast era table when the
  current-era candidate fails on a transient fetch error.
- get_inflation_stats must surface that same failure as an envelope, never a
  figure quietly read from the wrong era table.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import psa as psa_module
from ph_civic_data_mcp.utils.cache import CACHES

# Two CPI "year-on-year changes by commodity group" era tables, the shape PSA
# actually publishes: near-identical titles, one current, one backcasted.
CPI_ERA_ENTRIES = [
    {
        "id": "current.px",
        "type": "t",
        "text": "Year-on-Year Changes by Commodity Group, All Items, 2018=100",
    },
    {
        "id": "backcast.px",
        "type": "t",
        "text": "Year-on-Year Changes by Commodity Group, All Items, 2012=100, Backcasted",
    },
]

BACKCAST_META = {
    "title": "Year-on-Year Changes by Commodity Group, 2012=100, Backcasted",
    "variables": [
        {
            "code": "Year",
            "text": "Year",
            "time": True,
            "values": ["0"],
            "valueTexts": ["1994"],
        }
    ],
}


def _resp(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


def _clear_state() -> None:
    CACHES["psa_browse"].clear()
    CACHES["psa_prices"].clear()
    psa_module._DISCOVERY_CACHE.clear()


def _install_cpi_catalog(monkeypatch):
    """Route the CPI subpath at CPI_ERA_ENTRIES; current.px always fails."""

    async def _fake(client, method, url, **kwargs):
        if url.endswith("/DB/2M/PI/CPI/2018NEW/"):
            return _resp(method, url, CPI_ERA_ENTRIES)
        if url.endswith("current.px"):
            # Every retry inside the real fetch_with_retry has already been
            # exhausted by the time this fake stands in for it.
            raise httpx.ConnectError("openstat down")
        if url.endswith("backcast.px"):
            return _resp(method, url, BACKCAST_META)
        return httpx.Response(404, text="not found", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)


@pytest.mark.asyncio
async def test_pick_latest_table_raises_when_the_current_era_candidate_fails(monkeypatch):
    """A transient failure on the newer candidate must not hand back the older one."""
    _clear_state()
    _install_cpi_catalog(monkeypatch)

    with pytest.raises(psa_module.PSAUpstreamError):
        await psa_module._pick_latest_table(
            "2M/PI/CPI/2018NEW",
            ["year-on-year changes", "by commodity group"],
            ["core"],
        )
    assert len(psa_module._DISCOVERY_CACHE) == 0, "a failed pick must never be cached as latest"


@pytest.mark.asyncio
async def test_inflation_returns_a_failure_envelope_not_a_stale_backcast_figure(monkeypatch):
    """get_inflation_stats must not silently publish the backcasted 1994 table."""
    _clear_state()
    _install_cpi_catalog(monkeypatch)

    result = await psa_module.get_inflation_stats()

    assert result["upstream_error"] is True
    assert result["headline_inflation_pct"] is None
    assert result["reference_period"] is None
    assert any("ConnectError" in c for c in result["caveats"]), result["caveats"]
    assert "1994" not in " ".join(result["caveats"])
    assert len(CACHES["psa_prices"]) == 0
