"""Offline tests for the v0.5.0 audit fixes.

Covers, with no live HTTP:
- failure envelopes from list tools (never a bare [] on upstream failure)
- errors are never written to TTL caches (no negative caching)
- PSGC nickname aliases + alternatives for ambiguous names
- list_admin_units offset pagination
- get_earthquake_bulletin host allowlist
- volcano alerts stitched into assess_area_risk
- cross-source unwrapping of failure envelopes
- real tool_count in get_data_freshness; MCP resources + prompts registered
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import cross_source as cs
from ph_civic_data_mcp.sources import philgeps as philgeps_module
from ph_civic_data_mcp.sources import phivolcs as phivolcs_module
from ph_civic_data_mcp.sources import psgc as psgc_module
from ph_civic_data_mcp.utils.cache import CACHES


# ---------------------------------------------------------------------------
# Failure envelopes + no negative caching
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_earthquakes_outage_returns_envelope_not_empty_list(monkeypatch):
    async def _boom(*args, **kwargs):
        raise httpx.ConnectError("phivolcs down")

    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", _boom)
    CACHES["phivolcs_earthquakes"].clear()

    result = await phivolcs_module.get_latest_earthquakes()
    assert isinstance(result, dict)
    assert result["upstream_error"] is True
    assert result["results"] == []
    assert any("not an absence of earthquakes" in c for c in result["caveats"])
    # The outage must not be cached as "no earthquakes".
    assert len(CACHES["phivolcs_earthquakes"]) == 0


@pytest.mark.asyncio
async def test_philgeps_outage_is_not_negative_cached(monkeypatch):
    calls = {"n": 0}

    listing_html = """
    <table>
      <tr><th>Ref</th><th>Title</th><th>Mode</th><th>Class</th><th>Agency</th>
          <th>Published</th><th>Closing</th></tr>
      <tr><td>R-1</td><td>Construction of flood control</td><td>Public Bidding</td>
          <td>Civil Works</td><td>DPWH</td><td>01/06/2026</td><td>15/06/2026</td></tr>
    </table>
    """

    async def _flaky(client, method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("philgeps blip")
        return httpx.Response(200, text=listing_html, request=httpx.Request(method, url))

    monkeypatch.setattr(philgeps_module, "fetch_with_retry", _flaky)
    CACHES["philgeps_data"].clear()

    first = await philgeps_module.search_procurement(keyword="flood")
    assert isinstance(first, dict)
    assert first["upstream_error"] is True

    # Second call recovers immediately — the blip was not cached for 6h.
    second = await philgeps_module.search_procurement(keyword="flood")
    assert isinstance(second, list)
    assert second and second[0]["title"] == "Construction of flood control"


@pytest.mark.asyncio
async def test_typhoon_outage_returns_envelope(monkeypatch):
    from ph_civic_data_mcp.sources import pagasa as pagasa_module

    async def _boom(*args, **kwargs):
        raise httpx.ReadTimeout("pagasa slow")

    monkeypatch.setattr(pagasa_module, "fetch_with_retry", _boom)
    CACHES["pagasa_typhoons"].clear()

    result = await pagasa_module.get_active_typhoons()
    assert isinstance(result, dict)
    assert result["upstream_error"] is True
    assert any("not an absence of active typhoons" in c for c in result["caveats"])
    assert len(CACHES["pagasa_typhoons"]) == 0


# ---------------------------------------------------------------------------
# PSGC aliases, alternatives, pagination, outage semantics
# ---------------------------------------------------------------------------

PSGC_REGIONS = [
    {"code": "130000000", "name": "National Capital Region", "regionName": "NCR"},
]
PSGC_CITIES = [
    {
        "code": "137404000",
        "name": "Quezon City",
        "regionCode": "130000000",
        "provinceCode": "",
        "type": "City",
    },
    {
        "code": "133900000",
        "name": "City of San Juan",
        "regionCode": "130000000",
        "provinceCode": "",
        "type": "City",
        "regionName": "NCR",
    },
    {
        "code": "041000000",
        "name": "San Juan",
        "regionCode": "040000000",
        "provinceCode": "041000000",
        "type": "Municipality",
        "regionName": "Region IV-A",
    },
    {
        "code": "012900000",
        "name": "San Juan",
        "regionCode": "010000000",
        "provinceCode": "012900000",
        "type": "Municipality",
        "regionName": "Region I",
    },
]


def _psgc_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/regions/"):
        return httpx.Response(200, json=PSGC_REGIONS)
    if path.endswith("/provinces/"):
        return httpx.Response(200, json=[])
    if path.endswith("/cities-municipalities/"):
        return httpx.Response(200, json=PSGC_CITIES)
    if path.endswith("/barangays/"):
        return httpx.Response(200, json=[])
    return httpx.Response(404, json={"detail": f"unmocked: {path}"})


@pytest.fixture()
def _psgc_mock(monkeypatch):
    transport = httpx.MockTransport(_psgc_handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://psgc.gitlab.io")
    monkeypatch.setattr("ph_civic_data_mcp.sources.psgc.CLIENT", client)
    for cache_name in ("psgc_browse", "psgc_resolve"):
        CACHES[cache_name].clear()
    yield


@pytest.mark.asyncio
async def test_alias_qc_resolves_to_quezon_city(_psgc_mock):
    result = await psgc_module.resolve_ph_location("QC")
    assert result["matched"] is True
    assert result["name"] == "Quezon City"


@pytest.mark.asyncio
async def test_x_city_resolves_to_city_of_x_form(_psgc_mock):
    # PSGC names it "City of San Juan"; people write "San Juan City".
    # Before v0.5.0 this scored unrelated cities highest (live: "Manila
    # City" -> Danao City).
    result = await psgc_module.resolve_ph_location("San Juan City")
    assert result["matched"] is True
    assert result["name"] == "City of San Juan"
    assert result["match_score"] == 1.0


@pytest.mark.asyncio
async def test_ambiguous_name_returns_alternatives(_psgc_mock):
    result = await psgc_module.resolve_ph_location("San Juan")
    assert result["matched"] is True
    alts = result["alternatives"]
    assert alts, "exact-match ties must surface runner-up candidates"
    assert all(a["psgc_code"] != result["psgc_code"] for a in alts)


@pytest.mark.asyncio
async def test_psgc_outage_not_cached_as_no_match(monkeypatch):
    async def _boom(*args, **kwargs):
        raise httpx.ConnectError("psgc down")

    monkeypatch.setattr(psgc_module, "fetch_with_retry", _boom)
    for cache_name in ("psgc_browse", "psgc_resolve"):
        CACHES[cache_name].clear()

    result = await psgc_module.resolve_ph_location("Cebu City")
    assert result["matched"] is False
    assert result["upstream_error"] is True
    # Neither the resolve nor the level lists may cache the outage.
    assert len(CACHES["psgc_resolve"]) == 0
    assert len(CACHES["psgc_browse"]) == 0


@pytest.mark.asyncio
async def test_list_admin_units_offset_pagination(_psgc_mock):
    page1 = await psgc_module.list_admin_units(level="city-municipality", limit=2)
    page2 = await psgc_module.list_admin_units(level="city-municipality", limit=2, offset=2)
    assert isinstance(page1, list) and isinstance(page2, list)
    assert len(page1) == 2 and len(page2) == 2
    assert {r["psgc_code"] for r in page1}.isdisjoint({r["psgc_code"] for r in page2})


# ---------------------------------------------------------------------------
# Bulletin URL allowlist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulletin_refuses_non_phivolcs_hosts(monkeypatch):
    async def _must_not_fetch(*args, **kwargs):
        raise AssertionError("fetch must not be attempted for disallowed hosts")

    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", _must_not_fetch)
    result = await phivolcs_module.get_earthquake_bulletin("https://evil.example.com/x.html")
    assert any("phivolcs.dost.gov.ph" in c for c in result["caveats"])


# ---------------------------------------------------------------------------
# Cross-source: volcano stitch + envelope unwrapping + honest rule
# ---------------------------------------------------------------------------


@pytest.fixture()
def _cross_mocks(monkeypatch):
    async def _quakes(**_):
        return []

    async def _typhoons():
        return []

    async def _alerts(region=None):
        return []

    async def _volcanoes(volcano_name=None):
        return [
            {
                "name": "Taal",
                "alert_level": 1,
                "status_description": "Low-level unrest",
                "bulletin_url": "https://wovodat.phivolcs.dost.gov.ph/x",
            },
            {
                "name": "Mayon",
                "alert_level": 0,
                "status_description": "Normal",
                "bulletin_url": "https://wovodat.phivolcs.dost.gov.ph/y",
            },
        ]

    monkeypatch.setattr(cs, "get_latest_earthquakes", _quakes)
    monkeypatch.setattr(cs, "get_active_typhoons", _typhoons)
    monkeypatch.setattr(cs, "get_weather_alerts", _alerts)
    monkeypatch.setattr(cs, "get_volcano_status", _volcanoes)
    yield


@pytest.mark.asyncio
async def test_assess_area_risk_includes_elevated_volcanoes(_cross_mocks):
    result = await cs.assess_area_risk("Batangas")
    names = [v["name"] for v in result["volcano_alerts"]]
    assert names == ["Taal"], "only alert_level >= 1 volcanoes are listed"
    assert result["volcano_alerts_scope"].startswith("national")


@pytest.mark.asyncio
async def test_assess_area_risk_unwraps_failure_envelopes(monkeypatch, _cross_mocks):
    async def _envelope(**_):
        return {
            "results": [],
            "upstream_error": True,
            "caveats": ["PHIVOLCS earthquake list unavailable (ConnectError: x)."],
        }

    monkeypatch.setattr(cs, "get_latest_earthquakes", _envelope)
    result = await cs.assess_area_risk("Manila")
    assert result["recent_earthquakes_30d"] == 0
    assert any("PHIVOLCS earthquake query failed" in c for c in result["caveats"])


@pytest.mark.asyncio
async def test_flag_evidence_is_honest_about_progress_data(monkeypatch, _cross_mocks):
    async def _projects(**_):
        return [
            {
                "project_id": "P-9",
                "title": "Mega bridge",
                "agency": "DPWH",
                "region": "NCR",
                "cost_php": 900_000_000,
                "progress_pct": None,
                "source_url": "https://www.philgeps.gov.ph/",
            }
        ]

    monkeypatch.setattr(cs, "search_infra_projects", _projects)
    result = await cs.flag_infra_anomalies()
    flags = [f for f in result["flagged"] if f["rule_fired"] == "high_cost_no_published_progress"]
    assert flags
    assert "publishes no progress data for any notice" in flags[0]["evidence"]


# ---------------------------------------------------------------------------
# Server self-description: real tool_count, resources, prompts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_data_freshness_reports_real_tool_count():
    from ph_civic_data_mcp.server import _register_tools, get_data_freshness, mcp

    _register_tools()
    tools = await mcp.list_tools()
    fresh = await get_data_freshness()
    assert fresh["tool_count"] == len(tools)
    assert fresh["tool_count"] >= 29


@pytest.mark.asyncio
async def test_resources_and_prompts_registered():
    from ph_civic_data_mcp.server import mcp

    resources = {str(r.uri) for r in await mcp.list_resources()}
    assert "data://ph-civic/source-catalog" in resources
    assert "data://ph-civic/civic-framing" in resources

    prompts = {p.name for p in await mcp.list_prompts()}
    assert {"area_briefing", "infra_accountability_scan"} <= prompts
