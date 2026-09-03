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


# One current CPI table, two published years: 2025 (real data) and 2026 (the
# case under test). No backcast candidate, so this exercises the query-year
# loop inside get_inflation_stats, not the table-discovery step above.
CPI_QUERY_ENTRIES = [
    {
        "id": "current.px",
        "type": "t",
        "text": "Year-on-Year Changes by Commodity Group, All Items, 2018=100",
    },
]

CPI_QUERY_META = {
    "title": "Year-on-Year Changes by Commodity Group, All Items, 2018=100",
    "variables": [
        {
            "code": "Geolocation",
            "text": "Geolocation",
            "values": ["0"],
            "valueTexts": ["PHILIPPINES"],
        },
        {
            "code": "Commodity Description",
            "text": "Commodity Description",
            "values": ["0"],
            "valueTexts": ["All Items"],
        },
        {"code": "Year", "text": "Year", "values": ["0", "1"], "valueTexts": ["2025", "2026"]},
        {"code": "Period", "text": "Period", "values": ["0"], "valueTexts": ["Jan"]},
    ],
}

CPI_2025_JAN_DATA = {
    "columns": [
        {"code": "Geolocation", "type": "d"},
        {"code": "Commodity Description", "type": "d"},
        {"code": "Year", "type": "d"},
        {"code": "Period", "type": "d"},
        {"code": "CPI", "type": "c"},
    ],
    "data": [{"key": ["0", "0", "0", "0"], "values": ["3.5"]}],
}


def _install_cpi_query(monkeypatch, on_2026):
    """Route discovery to CPI_QUERY_META, then hand the year-walk POST to
    `on_2026` for year code "1" (2026) and a real January payload for "0"
    (2025)."""

    async def _fake(client, method, url, **kwargs):
        if method == "GET":
            if url.endswith("/DB/2M/PI/CPI/2018NEW/"):
                return _resp(method, url, CPI_QUERY_ENTRIES)
            if url.endswith("current.px"):
                return _resp(method, url, CPI_QUERY_META)
            return httpx.Response(404, text="not found", request=httpx.Request(method, url))
        body = kwargs.get("json") or {}
        year_sel = next(
            (q["selection"]["values"][0] for q in body.get("query", []) if q["code"] == "Year"),
            None,
        )
        if year_sel == "1":
            return on_2026(method, url)
        if year_sel == "0":
            return _resp(method, url, CPI_2025_JAN_DATA)
        return httpx.Response(404, text="not found", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)


@pytest.mark.asyncio
async def test_inflation_query_failure_on_newest_year_does_not_serve_older_year(monkeypatch):
    """A POST failure on 2026 must not silently publish the 2025 figure as latest."""
    _clear_state()

    def _fail_2026(method, url):
        raise httpx.ConnectError("openstat down")

    _install_cpi_query(monkeypatch, _fail_2026)

    result = await psa_module.get_inflation_stats()

    assert result["upstream_error"] is True
    assert result["headline_inflation_pct"] is None
    assert result["reference_period"] is None
    assert any("ConnectError" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["psa_prices"]) == 0


@pytest.mark.asyncio
async def test_inflation_genuine_empty_year_still_falls_back_to_older_year(monkeypatch):
    """A real, rowless 2026 response is a legitimate fallback to 2025, unlike a POST failure."""
    _clear_state()

    def _empty_2026(method, url):
        return _resp(method, url, {"columns": [], "data": []})

    _install_cpi_query(monkeypatch, _empty_2026)

    result = await psa_module.get_inflation_stats()

    assert not result.get("upstream_error")
    assert result["headline_inflation_pct"] == pytest.approx(3.5)
    assert result["reference_period"] == "2025 Jan"


@pytest.mark.asyncio
async def test_poverty_unknown_region_keeps_the_headline_key_and_is_invalid_request(monkeypatch):
    """The region-not-found path omitted `poverty_incidence_pct`, so a caller
    that indexed it got a KeyError. It also set no data_status."""
    meta = {
        "variables": [
            {
                "code": "Geolocation",
                "text": "Geolocation",
                "values": ["0"],
                "valueTexts": ["Philippines"],
            },
            {"code": "Incidence", "text": "Incidence", "values": ["1"], "valueTexts": ["x"]},
        ]
    }

    async def _fake_discover():
        return "https://example.test/poverty.px", meta

    async def _no_subsistence():
        raise RuntimeError("subsistence table not needed here")

    monkeypatch.setattr(psa_module, "_discover_poverty_table", _fake_discover)
    monkeypatch.setattr(psa_module, "_discover_subsistence_table", _no_subsistence)

    result = await psa_module.get_poverty_stats(region="Wakanda")

    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["data_status"] == "invalid_request"
    assert result["poverty_incidence_pct"] is None
    assert any("Wakanda" in c for c in result["caveats"])
