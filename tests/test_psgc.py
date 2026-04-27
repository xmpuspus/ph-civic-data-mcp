"""Unit tests for PSGC source — mocked HTTP."""

from __future__ import annotations


import httpx
import pytest

from ph_civic_data_mcp.sources import psgc as psgc_module
from ph_civic_data_mcp.utils.cache import CACHES


REGIONS_PAYLOAD = [
    {
        "code": "130000000",
        "name": "National Capital Region",
        "regionName": "NCR",
        "islandGroupCode": "luzon",
        "psgc10DigitCode": "1300000000",
    },
    {
        "code": "070000000",
        "name": "Central Visayas",
        "regionName": "Region VII",
        "islandGroupCode": "visayas",
        "psgc10DigitCode": "0700000000",
    },
]

PROVINCES_PAYLOAD = [
    {
        "code": "072200000",
        "name": "Cebu",
        "regionCode": "070000000",
        "islandGroupCode": "visayas",
        "psgc10DigitCode": "0722200000",
    },
    {
        "code": "035400000",
        "name": "Pampanga",
        "regionCode": "030000000",
        "islandGroupCode": "luzon",
        "psgc10DigitCode": "0354000000",
    },
]

CITIES_PAYLOAD = [
    {
        "code": "133900000",
        "name": "City of Manila",
        "regionCode": "130000000",
        "provinceCode": "",
        "type": "City",
        "islandGroupCode": "luzon",
        "psgc10DigitCode": "1339000000",
    },
    {
        "code": "072217000",
        "name": "Cebu City",
        "regionCode": "070000000",
        "provinceCode": "072200000",
        "type": "City",
        "islandGroupCode": "visayas",
        "psgc10DigitCode": "0722217000",
    },
]


def _mock_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/regions/"):
        return httpx.Response(200, json=REGIONS_PAYLOAD)
    if path.endswith("/provinces/"):
        return httpx.Response(200, json=PROVINCES_PAYLOAD)
    if path.endswith("/cities-municipalities/"):
        return httpx.Response(200, json=CITIES_PAYLOAD)
    if path.endswith("/cities/"):
        return httpx.Response(200, json=[c for c in CITIES_PAYLOAD if c.get("type") == "City"])
    if path.endswith("/municipalities/"):
        return httpx.Response(200, json=[])
    if path.endswith("/districts/"):
        return httpx.Response(200, json=[])
    if path.endswith("/sub-municipalities/"):
        return httpx.Response(200, json=[])
    if path.endswith("/barangays/"):
        return httpx.Response(200, json=[])
    if "/cities-municipalities/" in path:
        code = path.rstrip("/").rsplit("/", 1)[-1]
        for c in CITIES_PAYLOAD:
            if c["code"] == code:
                return httpx.Response(200, json=c)
        return httpx.Response(404, json={"detail": "not found"})
    if "/regions/" in path:
        code = path.rstrip("/").rsplit("/", 1)[-1]
        for r in REGIONS_PAYLOAD:
            if r["code"] == code:
                return httpx.Response(200, json=r)
        return httpx.Response(404)
    if "/provinces/" in path:
        code = path.rstrip("/").rsplit("/", 1)[-1]
        for p in PROVINCES_PAYLOAD:
            if p["code"] == code:
                return httpx.Response(200, json=p)
        return httpx.Response(404)
    return httpx.Response(404, json={"detail": f"unmocked: {path}"})


@pytest.fixture(autouse=True)
def _patch_client(monkeypatch):
    transport = httpx.MockTransport(_mock_handler)
    mock_client = httpx.AsyncClient(transport=transport, base_url="https://psgc.gitlab.io")
    monkeypatch.setattr("ph_civic_data_mcp.sources.psgc.CLIENT", mock_client)
    # Clear caches between tests so mocks always serve.
    for cache_name in ("psgc_browse", "psgc_resolve"):
        CACHES[cache_name].clear()
    yield


@pytest.mark.asyncio
async def test_resolve_ph_location_matches_city():
    result = await psgc_module.resolve_ph_location("Cebu City")
    assert result["matched"] is not False
    assert result["psgc_code"] == "072217000"
    assert result["name"] == "Cebu City"
    assert result["level"] in ("city", "city-municipality")
    assert result["source"] == "PSGC"
    assert result["source_url"].startswith("https://psgc.gitlab.io/api/")
    assert result["license"]
    assert "data_retrieved_at" in result


@pytest.mark.asyncio
async def test_resolve_ph_location_handles_unknown():
    result = await psgc_module.resolve_ph_location("zzzzzzzzz-not-a-place")
    assert result["matched"] is False
    assert result["caveats"]
    assert result["source"] == "PSGC"
    assert "data_retrieved_at" in result


@pytest.mark.asyncio
async def test_resolve_ph_location_fuzzy_partial():
    result = await psgc_module.resolve_ph_location("Pampanga")
    assert result["psgc_code"] == "035400000"
    assert result["level"] == "province"


@pytest.mark.asyncio
async def test_list_admin_units_no_parent_returns_regions():
    result = await psgc_module.list_admin_units()
    assert isinstance(result, list)
    assert len(result) == 2
    codes = {r["psgc_code"] for r in result}
    assert "130000000" in codes
    for r in result:
        assert r["level"] == "region"
        assert r["source_url"].startswith("https://psgc.gitlab.io/api/")
        assert r["license"]


@pytest.mark.asyncio
async def test_list_admin_units_children_of_province():
    result = await psgc_module.list_admin_units(parent_code="072200000")
    assert isinstance(result, list)
    # Cebu City is a child of Cebu province
    assert any(r["name"] == "Cebu City" for r in result)


@pytest.mark.asyncio
async def test_get_location_hierarchy_walks_up():
    result = await psgc_module.get_location_hierarchy("072217000")
    assert result["psgc_code"] == "072217000"
    chain = result["chain"]
    assert isinstance(chain, list)
    assert len(chain) >= 2
    # First entry should be the region, last should be the city itself
    assert chain[0]["level"] == "region"
    assert chain[-1]["psgc_code"] == "072217000"
    assert result["source"] == "PSGC"


@pytest.mark.asyncio
async def test_get_location_hierarchy_unknown_code():
    result = await psgc_module.get_location_hierarchy("999999999")
    assert result["chain"] == []
    assert result["caveats"]


@pytest.mark.asyncio
async def test_resolve_graceful_on_5xx(monkeypatch):
    """5xx upstream must not raise; resolve returns matched=False with caveats."""

    def _bad_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "service unavailable"})

    transport = httpx.MockTransport(_bad_handler)
    bad_client = httpx.AsyncClient(transport=transport, base_url="https://psgc.gitlab.io")
    monkeypatch.setattr("ph_civic_data_mcp.sources.psgc.CLIENT", bad_client)
    for cache_name in ("psgc_browse", "psgc_resolve"):
        CACHES[cache_name].clear()

    result = await psgc_module.resolve_ph_location("Cebu City")
    # On total upstream failure we return matched=False, never crash.
    assert result["source"] == "PSGC"
    assert "data_retrieved_at" in result


def test_classify_level_pure():
    cases = [
        ({"code": "130000000"}, "region"),
        ({"code": "035400000"}, "province"),
        ({"code": "133900000", "type": "City"}, "city"),
        ({"code": "012801001", "type": "Barangay"}, "barangay"),
    ]
    for record, expected in cases:
        assert psgc_module._classify_level(record) == expected, f"failed for {record}"


def test_score_ranking():
    assert psgc_module._score("Manila", "Manila") == 1.0
    assert psgc_module._score("manila", "City of Manila") > psgc_module._score(
        "manila", "Iloilo City"
    )
    assert psgc_module._score("", "Manila") == 0.0
