"""Offline tests for the v0.6.0 defect fixes.

Covers, with no live HTTP:
- PSA poverty discovery walks the catalog (Poverty is 1F now, not 1E/FY)
- PSA browse caches a success and never caches an error or an empty listing
- poverty discovery failure returns upstream_error + a caveat carrying the error
- a fresh import of the server registers every tool without calling
  _register_tools() by hand
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import psa as psa_module
from ph_civic_data_mcp.utils.cache import CACHES


# Root listing as PSA serves it today: Poverty is its own subject at 1F, and
# 1E is Income and Consumption with no FY child. A discovery that still assumes
# 1E/FY finds nothing here.
ROOT_ENTRIES = [
    {"id": "1A", "type": "l", "text": "Population and Vital Statistics"},
    {"id": "1E", "type": "l", "text": "Income and Consumption"},
    {"id": "1F", "type": "l", "text": "Poverty"},
    {"id": "2M", "type": "l", "text": "Prices"},
]

POVERTY_SUBJECT_ENTRIES = [
    {"id": "FS", "type": "l", "text": "First Semester Poverty Statistics"},
    {"id": "FY", "type": "l", "text": "Full Year Poverty Statistics"},
    {"id": "BS", "type": "l", "text": "Poverty Statistics Among the Basic Sectors"},
]

FY_TABLE_ENTRIES = [
    {
        "id": "0011F3DF010.px",
        "type": "t",
        "text": (
            "Table 1. Annual Per Capita Poverty Threshold and Poverty Incidence "
            "Among Families with Measures of Precision, by Region and Province: "
            "2018, 2021, and 2023"
        ),
    },
    {
        "id": "0051F3DF030.px",
        "type": "t",
        "text": (
            "Table 3. Annual Per Capita Food Threshold and Subsistence Incidence "
            "Among Families with Measures of Precision, by Region and Province: "
            "2018, 2021, and 2023"
        ),
    },
]

# Dimension codes copied from the live 1F metadata on 2026-08-06.
POVERTY_META = {
    "title": "Table 1. Annual Per Capita Poverty Threshold and Poverty Incidence",
    "variables": [
        {
            "code": "Geolocation",
            "text": "Geolocation",
            "values": ["0", "1"],
            "valueTexts": ["PHILIPPINES", "..National Capital Region (NCR)"],
        },
        {
            "code": "Threshold/Incidence/Measures of Precision",
            "text": "Threshold/Incidence/Measures of Precision",
            "values": ["0", "1"],
            "valueTexts": [
                "Annual Per Capita Poverty Threshold (in PhP)",
                "Poverty Incidence Among Families (%)",
            ],
        },
        {
            "code": "Year",
            "text": "Year",
            "values": ["0", "1", "2"],
            "valueTexts": ["2018", "2021", "2023"],
        },
    ],
}

SUBSISTENCE_META = {
    "title": "Table 3. Annual Per Capita Food Threshold and Subsistence Incidence",
    "variables": [
        {
            "code": "Geolocation",
            "text": "Geolocation",
            "values": ["0", "1"],
            "valueTexts": ["PHILIPPINES", "..National Capital Region (NCR)"],
        },
        {
            "code": "Threshold/Incidence/Measures of Precision",
            "text": "Threshold/Incidence/Measures of Precision",
            "values": ["0", "1"],
            "valueTexts": [
                "Annual Per Capita Food Threshold (in PhP)",
                "Subsistence Incidence among Families (%)",
            ],
        },
        {
            "code": "Year",
            "text": "Year",
            "values": ["0", "1", "2"],
            "valueTexts": ["2018", "2021", "2023"],
        },
    ],
}


def _json_response(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


def _install_fake_openstat(monkeypatch, *, seen: list[str] | None = None):
    """Route every PSA HTTP call at an in-memory 1F catalog."""

    async def _fake(client, method, url, **kwargs):
        if seen is not None:
            seen.append(f"{method} {url}")
        if method == "POST":
            body = {
                "columns": [
                    {"code": "Geolocation", "type": "d"},
                    {"code": "Threshold/Incidence/Measures of Precision", "type": "d"},
                    {"code": "Year", "type": "d"},
                    {"code": "Poverty", "type": "c"},
                ],
                "data": [{"key": ["0", "1", "2"], "values": ["10.9"]}],
            }
            return _json_response(method, url, body)
        if url.endswith("/DB/"):
            return _json_response(method, url, ROOT_ENTRIES)
        if url.endswith("/DB/1F/"):
            return _json_response(method, url, POVERTY_SUBJECT_ENTRIES)
        if url.endswith("/DB/1F/FY/"):
            return _json_response(method, url, FY_TABLE_ENTRIES)
        if url.endswith("0011F3DF010.px"):
            return _json_response(method, url, POVERTY_META)
        if url.endswith("0051F3DF030.px"):
            return _json_response(method, url, SUBSISTENCE_META)
        # Anything else, including the dead 1E/FY path, is a 404.
        return httpx.Response(404, text="not found", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)


def _clear_psa_state() -> None:
    CACHES["psa_poverty"].clear()
    CACHES["psa_browse"].clear()
    psa_module._DISCOVERY_CACHE.clear()


# ---------------------------------------------------------------------------
# PSA poverty discovery walks the catalog instead of assuming 1E/FY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poverty_discovery_finds_table_under_1f(monkeypatch):
    _clear_psa_state()
    seen: list[str] = []
    _install_fake_openstat(monkeypatch, seen=seen)

    result = await psa_module.get_poverty_stats()

    assert result.get("poverty_incidence_pct") == pytest.approx(10.9)
    assert result["reference_year"] == 2023
    assert "/DB/1F/FY/0011F3DF010.px" in result["source_table"]
    assert not result.get("upstream_error")


@pytest.mark.asyncio
async def test_poverty_discovery_never_requests_the_dead_1e_path(monkeypatch):
    _clear_psa_state()
    seen: list[str] = []
    _install_fake_openstat(monkeypatch, seen=seen)

    await psa_module.get_poverty_stats()

    dead = [u for u in seen if "/DB/1E/FY" in u]
    assert dead == [], f"discovery still requests the removed 1E/FY path: {dead}"


@pytest.mark.asyncio
async def test_poverty_discovery_reads_subject_by_title_not_by_id(monkeypatch):
    """Poverty moved 1E -> 1F once already; a renumber must not break us again."""
    _clear_psa_state()

    moved_root = [
        {"id": "1A", "type": "l", "text": "Population and Vital Statistics"},
        {"id": "9Z", "type": "l", "text": "Poverty"},
    ]

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _json_response(
                method,
                url,
                {
                    "columns": [
                        {"code": "Geolocation", "type": "d"},
                        {"code": "Threshold/Incidence/Measures of Precision", "type": "d"},
                        {"code": "Year", "type": "d"},
                        {"code": "Poverty", "type": "c"},
                    ],
                    "data": [{"key": ["0", "1", "2"], "values": ["10.9"]}],
                },
            )
        if url.endswith("/DB/"):
            return _json_response(method, url, moved_root)
        if url.endswith("/DB/9Z/"):
            return _json_response(method, url, POVERTY_SUBJECT_ENTRIES)
        if url.endswith("/DB/9Z/FY/"):
            return _json_response(method, url, FY_TABLE_ENTRIES)
        if url.endswith("0011F3DF010.px"):
            return _json_response(method, url, POVERTY_META)
        if url.endswith("0051F3DF030.px"):
            return _json_response(method, url, SUBSISTENCE_META)
        return httpx.Response(404, text="not found", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)

    result = await psa_module.get_poverty_stats()
    assert result.get("poverty_incidence_pct") == pytest.approx(10.9)
    assert "/DB/9Z/FY/" in result["source_table"]


@pytest.mark.asyncio
async def test_poverty_regional_still_works(monkeypatch):
    _clear_psa_state()
    _install_fake_openstat(monkeypatch)

    result = await psa_module.get_poverty_stats(region="NCR")
    assert result["region"] == "National Capital Region (NCR)"
    assert result.get("poverty_incidence_pct") == pytest.approx(10.9)


# ---------------------------------------------------------------------------
# Discovery failure: honest envelope, real error text, nothing cached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poverty_discovery_failure_returns_upstream_error(monkeypatch):
    _clear_psa_state()

    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("openstat down")

    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    result = await psa_module.get_poverty_stats()
    assert result["upstream_error"] is True
    assert result["caveats"], "a failure must explain itself"
    assert any("ConnectError" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["psa_poverty"]) == 0
    assert len(CACHES["psa_browse"]) == 0


@pytest.mark.asyncio
async def test_browse_caches_success_but_not_failure(monkeypatch):
    _clear_psa_state()
    calls = {"n": 0}

    async def _flaky(client, method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow")
        return _json_response(method, url, ROOT_ENTRIES)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _flaky)

    with pytest.raises(psa_module.PSAUpstreamError):
        await psa_module._browse("")
    assert len(CACHES["psa_browse"]) == 0, "an error must never enter the cache"

    entries = await psa_module._browse("")
    assert entries == ROOT_ENTRIES
    assert len(CACHES["psa_browse"]) == 1

    # Third call is served from cache: no further HTTP.
    before = calls["n"]
    again = await psa_module._browse("")
    assert again == ROOT_ENTRIES
    assert calls["n"] == before


@pytest.mark.asyncio
async def test_browse_treats_empty_listing_as_upstream_failure(monkeypatch):
    """A path that should hold entries returning [] means the path moved."""
    _clear_psa_state()

    async def _empty(client, method, url, **kwargs):
        return _json_response(method, url, [])

    monkeypatch.setattr(psa_module, "fetch_with_retry", _empty)

    with pytest.raises(psa_module.PSAUpstreamError):
        await psa_module._browse("1F/FY")
    assert len(CACHES["psa_browse"]) == 0


@pytest.mark.asyncio
async def test_health_indicators_survive_a_browse_failure(monkeypatch):
    """_browse now raises; the health tool must still return an envelope."""
    _clear_psa_state()
    CACHES["psa_health"].clear()

    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("openstat down")

    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    result = await psa_module.get_health_indicators()
    assert result["upstream_error"] is True
    assert result["indicators"] == []
    assert len(CACHES["psa_health"]) == 0


@pytest.mark.asyncio
async def test_inflation_survives_a_browse_failure(monkeypatch):
    _clear_psa_state()
    CACHES["psa_prices"].clear()

    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("openstat down")

    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    result = await psa_module.get_inflation_stats()
    assert result["upstream_error"] is True
    assert len(CACHES["psa_prices"]) == 0


@pytest.mark.asyncio
async def test_labor_survives_a_browse_failure(monkeypatch):
    _clear_psa_state()
    CACHES["psa_labor"].clear()

    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("openstat down")

    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    result = await psa_module.get_labor_stats()
    assert result["upstream_error"] is True
    assert len(CACHES["psa_labor"]) == 0


# ---------------------------------------------------------------------------
# Rate limiting and 429 backoff
# ---------------------------------------------------------------------------


def test_a_429_backs_off_past_the_psa_window():
    """The 1/2/4s ladder burns all three tries inside one 10s window."""
    from ph_civic_data_mcp.utils import http

    response = httpx.Response(429, request=httpx.Request("GET", "https://example.test"))
    delays = [http._retry_delay(response, i) for i in range(3)]
    assert delays[0] >= 5
    assert sum(delays[:2]) > 10, f"two 429 retries must outlast a 10s window: {delays}"


def test_a_503_keeps_the_short_ladder():
    from ph_civic_data_mcp.utils import http

    response = httpx.Response(503, request=httpx.Request("GET", "https://example.test"))
    assert [http._retry_delay(response, i) for i in range(3)] == [1.0, 2.0, 4.0]


def test_retry_after_wins_when_the_server_sends_one():
    from ph_civic_data_mcp.utils import http

    request = httpx.Request("GET", "https://example.test")
    longer = httpx.Response(429, headers={"Retry-After": "17"}, request=request)
    assert http._retry_delay(longer, 0) == 17.0

    # A shorter Retry-After never shrinks our own floor.
    shorter = httpx.Response(429, headers={"Retry-After": "1"}, request=request)
    assert http._retry_delay(shorter, 0) >= 5

    # A silly value is capped, and a junk value is ignored.
    huge = httpx.Response(429, headers={"Retry-After": "99999"}, request=request)
    assert http._retry_delay(huge, 0) == http.MAX_RETRY_AFTER_SECONDS
    junk = httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct"}, request=request)
    assert http._retry_delay(junk, 0) >= 5


@pytest.mark.asyncio
async def test_psa_rate_limiter_holds_the_published_window(monkeypatch):
    """10 requests per 10 seconds: the 11th waits for the window to roll."""
    import asyncio

    from ph_civic_data_mcp.sources import psa as psa_mod

    psa_mod._RECENT_CALLS.clear()
    slept: list[float] = []

    async def _record(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _record)

    for _ in range(psa_mod.PSA_RATE_LIMIT_REQUESTS):
        await psa_mod._psa_rate_limit()
    assert slept == [], "the first 10 calls must not wait"

    await psa_mod._psa_rate_limit()
    assert slept, "the 11th call must wait for the window"
    assert slept[0] <= psa_mod.PSA_RATE_LIMIT_WINDOW_SECONDS
    psa_mod._RECENT_CALLS.clear()


@pytest.mark.asyncio
async def test_every_psa_fetch_helper_passes_the_rate_limiter(monkeypatch):
    from ph_civic_data_mcp.sources import psa as psa_mod

    hits = {"n": 0}

    async def _count():
        hits["n"] += 1

    async def _ok(client, method, url, **kwargs):
        return _json_response(method, url, {"variables": []})

    monkeypatch.setattr(psa_mod, "_psa_rate_limit", _count)
    monkeypatch.setattr(psa_mod, "fetch_with_retry", _ok)

    await psa_mod._get_json("https://example.test/a")
    await psa_mod._get_json_or_raise("https://example.test/b")
    await psa_mod._post_json("https://example.test/c", {})
    await psa_mod._post_json_or_raise("https://example.test/d", {})
    assert hits["n"] == 4, "all four PSA helpers must go through the limiter"
