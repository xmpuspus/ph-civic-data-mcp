"""Offline tests for the v0.6.1 population repair.

PSA moved the 2020 Census from `DB/1A/PO/` to `DB/1A/PO_2020/`, left one
projection table under `PO/`, and published the 2024 Census under
`DB/1A/PO_2024/` with PSGC-coded geography down to barangay level. The old
discovery pinned `DB/1A/PO/`, so `get_population_stats` returned a caveat with
no `upstream_error` for every input and `get_area_profile` hid it.

Every PSA HTTP call here is routed at an in-memory catalog shaped like the
live one on 2026-09-03, so nothing touches the network.
"""

from __future__ import annotations

import json

import httpx
import pytest
from cachetools import TTLCache

from ph_civic_data_mcp.sources import autostitch as autostitch_module
from ph_civic_data_mcp.sources import psa as psa_module
from ph_civic_data_mcp.sources.psa import get_population_stats
from ph_civic_data_mcp.utils import envelope
from ph_civic_data_mcp.utils.cache import CACHES

# ---------------------------------------------------------------------------
# An in-memory copy of the live 1A subject, 2026-09-03
# ---------------------------------------------------------------------------

SUBJECT_1A = [
    {"id": "PO_2010", "type": "l", "text": "2010 Census of Population and Housing"},
    {"id": "PO_2015", "type": "l", "text": "2015 Census of Population"},
    {"id": "PO_2020", "type": "l", "text": "2020 Census of Population and Housing"},
    {"id": "PO_2024", "type": "l", "text": "2024 Census of Population"},
    {"id": "PO", "type": "l", "text": "Population Projection"},
    {"id": "VS", "type": "l", "text": "Vital Statistics"},
]

BARANGAY_TITLE = (
    "Total Population, Household Population, and Number of Households by Province, "
    "City, Municipality, and Barangay as of 01 July 2024"
)

PO_2024_TABLES = [
    {"id": "0011A6DTPH0.px", "type": "t", "text": f"{BARANGAY_TITLE}: Ilocos Region"},
    {"id": "0081A6DTPH7.px", "type": "t", "text": f"{BARANGAY_TITLE}: Eastern Visayas"},
    # PSA's own typo, "Captial", is live. The acronym still has to match.
    {
        "id": "0171A6DTHP6.px",
        "type": "t",
        "text": f"{BARANGAY_TITLE} : National Captial Region (NCR)",
    },
    {
        "id": "0191A6DTHP8.px",
        "type": "t",
        "text": (
            "Total Population, Household Population, Number of Households and Average "
            "Household Size by Region, Province, and Highly Urbanized City: Philippines, 2024"
        ),
    },
    {
        "id": "0211A6DAPG0.px",
        "type": "t",
        "text": (
            "Population and Annual Population Growth Rate of the Philippines and its Regions, "
            "Provinces, and Highly Urbanized Cities Based on the 2010, 2015, 2020 and 2024 "
            "Population Census"
        ),
    },
    {
        "id": "0241A6DPUP1.px",
        "type": "t",
        "text": (
            "Total Population, Urban Population, and Percent Urban by Region, Province, "
            "Highly Urbanized City, and City/Municipality: Philippines, 2024"
        ),
    },
]

PO_2020_TABLES = [
    {
        "id": "0011A6DPHH0.px",
        "type": "t",
        "text": (
            "Total Population, Household Population, and Number of Households by Region "
            "and Province/Highly Urbanized City: Philippines, 2020"
        ),
    },
    {
        "id": "0031A6DPAG0.px",
        "type": "t",
        "text": "Population by Age Group, Sex, Region, and Province/Highly Urbanized City: Philippines , 2020",
    },
]

# `DB/1A/PO/` today: one projection table, no census.
PO_PROJECTION_TABLES = [
    {
        "id": "0021A3BPOP1.px",
        "type": "t",
        "text": (
            "Projected Population Based on 2020 CPH by Five-Year Age Group and by Sex "
            "and Single-Year Interval"
        ),
    },
]

META_2024_SUMMARY = {
    "title": PO_2024_TABLES[3]["text"],
    "variables": [
        {
            "code": "Geographic Location",
            "text": "Geographic Location",
            "values": ["0000000000", "1300000000", "1380600000", "0800000000", "0803700000"],
            "valueTexts": [
                "Philippines a",
                "..National Capital Region (NCR)",
                "....City of Manila",
                "..Region VIII (Eastern Visayas)",
                "....Leyte",
            ],
        },
        {
            "code": "Parameter",
            "text": "Parameter",
            "values": ["0", "1", "2", "3"],
            "valueTexts": [
                "Total Population",
                "Household Population",
                "Number of Households",
                "Average Household Size",
            ],
        },
    ],
}

META_2020_SUMMARY = {
    "title": PO_2020_TABLES[0]["text"],
    "variables": [
        {
            "code": "Geographic Location",
            "text": "Geographic Location",
            "values": ["0", "1", "2"],
            "valueTexts": [
                "PHILIPPINES /a",
                "..NATIONAL CAPITAL REGION (NCR)",
                "....City of Manila",
            ],
        },
        {
            "code": "Parameter",
            "text": "Parameter",
            "values": ["0", "1", "2"],
            "valueTexts": ["Total Population", "Household Population", "Number of Households"],
        },
    ],
}

# Live shape: the barangay tables declare the geography variable with no
# values at all. Only a code the caller already knows can reach a row.
META_BARANGAY = {
    "variables": [
        {"code": "Geographic Location", "text": "Geographic Location"},
        {
            "code": "Parameter",
            "text": "Parameter",
            "values": ["0", "1", "2"],
            "valueTexts": ["Total Population", "Household Population", "Number of Households"],
        },
    ],
}

# Live figures returned on 2026-09-03.
CELLS = {
    "0191A6DTHP8.px": {
        "0000000000": "112729484",
        "1300000000": "14001751",
        "1380600000": "1846513",
        "0800000000": "4625929",
        "0803700000": "1823458",
    },
    "0011A6DPHH0.px": {"0": "109033245", "1": "13484462", "2": "1846513"},
    "0171A6DTHP6.px": {"1380100001": "2356", "1380600000": "1846513", "1380601001": "1234"},
    "0081A6DTPH7.px": {"0831600000": "259353"},
}


def _json_response(method: str, url: str, payload: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload, request=httpx.Request(method, url))


def _install_fake_census(monkeypatch, *, seen: list[str] | None = None, meta_2024=None):
    """Route every PSA HTTP call at the in-memory 1A subject."""
    meta_2024 = META_2024_SUMMARY if meta_2024 is None else meta_2024

    async def _fake(client, method, url, **kwargs):
        if seen is not None:
            seen.append(f"{method} {url}")
        if method == "POST":
            table = url.rsplit("/", 1)[-1]
            body = kwargs["json"]
            geo_codes = body["query"][0]["selection"]["values"]
            param = body["query"][1]["selection"]["values"][0]
            rows = [
                {"key": [code, param], "values": [CELLS[table][code]]}
                for code in geo_codes
                if code in CELLS.get(table, {})
            ]
            return _json_response(method, url, {"columns": [], "data": rows})
        if url.endswith("/DB/1A/"):
            return _json_response(method, url, SUBJECT_1A)
        if url.endswith("/DB/1A/PO_2024/"):
            return _json_response(method, url, PO_2024_TABLES)
        if url.endswith("/DB/1A/PO_2020/"):
            return _json_response(method, url, PO_2020_TABLES)
        if url.endswith("/DB/1A/PO/"):
            return _json_response(method, url, PO_PROJECTION_TABLES)
        if url.endswith("0191A6DTHP8.px"):
            return _json_response(method, url, meta_2024)
        if url.endswith("0011A6DPHH0.px"):
            return _json_response(method, url, META_2020_SUMMARY)
        if url.endswith("0171A6DTHP6.px") or url.endswith("0081A6DTPH7.px"):
            return _json_response(method, url, META_BARANGAY)
        if url.endswith("0011A6DTPH0.px"):
            return _json_response(method, url, META_BARANGAY)
        return httpx.Response(404, text="not found", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)


PSGC_RECORDS = {
    "083747000": {
        "psgc_code": "083747000",
        "psgc_10digit_code": "0831600000",
        "name": "City of Tacloban",
        "level": "city",
        "region_code": "080000000",
    },
    "1380100001": {
        "psgc_code": "137501001",
        "psgc_10digit_code": "1380100001",
        "name": "Barangay 1",
        "level": "barangay",
        "region_code": "130000000",
    },
    "0803700000": {
        "psgc_code": "083700000",
        "psgc_10digit_code": "0803700000",
        "name": "Leyte",
        "level": "province",
        "region_code": "080000000",
    },
}


def _install_fake_psgc(monkeypatch):
    async def _lookup(code: str):
        return PSGC_RECORDS.get(code)

    monkeypatch.setattr(psa_module, "lookup_psgc_code", _lookup)


@pytest.fixture(autouse=True)
def _clear_state(monkeypatch):
    CACHES["psa_population"].clear()
    CACHES["psa_browse"].clear()
    psa_module._DISCOVERY_CACHE.clear()
    psa_module._reset_rate_limiter()

    async def _no_wait():
        return None

    monkeypatch.setattr(psa_module, "_psa_rate_limit", _no_wait)
    yield
    CACHES["psa_population"].clear()
    CACHES["psa_browse"].clear()
    psa_module._DISCOVERY_CACHE.clear()


# ---------------------------------------------------------------------------
# Discovery by title, never by a pinned folder id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_picks_the_latest_census_by_title(monkeypatch):
    seen: list[str] = []
    _install_fake_census(monkeypatch, seen=seen)

    result = await get_population_stats()

    assert result["population"] == 112_729_484
    assert result["year"] == 2024
    assert result["source_table"].endswith("/DB/1A/PO_2024/0191A6DTHP8.px")
    # The projection folder is not a census. Nothing may be read from it.
    assert not any("/DB/1A/PO/" in call for call in seen), seen


@pytest.mark.asyncio
async def test_discovery_rejects_a_summary_table_without_a_national_row(monkeypatch):
    """A matching title is not enough. The table has to carry a Philippines row."""
    broken = json.loads(json.dumps(META_2024_SUMMARY))
    broken["variables"][0]["valueTexts"][0] = "Somewhere else"
    _install_fake_census(monkeypatch, meta_2024=broken)

    result = await get_population_stats()

    assert result["upstream_error"] is True
    assert result["data_status"] == "unavailable"
    assert result["population"] is None
    assert any("validation" in c.lower() or "passed" in c.lower() for c in result["caveats"])
    assert len(CACHES["psa_population"]) == 0
    assert "census_summary::2024" not in psa_module._DISCOVERY_CACHE


@pytest.mark.asyncio
async def test_discovery_transport_failure_is_upstream_error_and_caches_nothing(monkeypatch):
    async def _boom(*args, **kwargs):
        raise httpx.ConnectError("openstat down")

    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    result = await get_population_stats(region="NCR")

    assert result["upstream_error"] is True
    assert result["validation_error"] is False
    assert result["data_status"] == "unavailable"
    assert result["population"] is None
    assert result["region"] == "NCR"
    assert any("ConnectError" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["psa_population"]) == 0
    assert len(psa_module._DISCOVERY_CACHE) == 0


def test_discovery_cache_expires():
    """A process that ran for weeks kept a table PSA had moved. Now it forgets."""
    assert isinstance(psa_module._DISCOVERY_CACHE, TTLCache)
    assert psa_module._DISCOVERY_CACHE.ttl <= 86400


# ---------------------------------------------------------------------------
# Vintage choice
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_year_selects_that_census_vintage(monkeypatch):
    _install_fake_census(monkeypatch)

    result = await get_population_stats(year=2020)

    assert result["population"] == 109_033_245
    assert result["year"] == 2020
    assert result["census"] == "2020 Census of Population and Housing"
    assert result["reference_date"] == "2020-05-01"
    assert result["source_table"].endswith("/DB/1A/PO_2020/0011A6DPHH0.px")
    assert result["available_vintages"] == [2010, 2015, 2020, 2024]


@pytest.mark.asyncio
async def test_unknown_year_is_a_validation_error_not_an_outage(monkeypatch):
    seen: list[str] = []
    _install_fake_census(monkeypatch, seen=seen)

    result = await get_population_stats(year=2021)

    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["data_status"] == "invalid_request"
    assert result["available_vintages"] == [2010, 2015, 2020, 2024]
    assert result["population"] is None
    assert not any(call.startswith("POST") for call in seen)
    assert len(CACHES["psa_population"]) == 0


# ---------------------------------------------------------------------------
# Geography: labels, levels, footnotes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_national_result_carries_vintage_and_geography(monkeypatch):
    _install_fake_census(monkeypatch)

    result = await get_population_stats()

    assert result["geography"] == "Philippines"
    assert result["region"] == "Philippines"
    assert result["geography_level"] == "national"
    assert result["psgc_code"] == "0000000000"
    assert result["census"] == "2024 Census of Population"
    assert result["reference_date"] == "2024-07-01"
    assert result["data_status"] == "success"
    assert result["upstream_error"] is False
    assert result["source"] == "PSA"
    assert result["source_url"].endswith("/DB/1A/PO_2024/0191A6DTHP8.px")
    assert result["license"]
    assert "2024 Census of Population" in result["reference_note"]
    assert len(CACHES["psa_population"]) == 1


@pytest.mark.asyncio
async def test_region_label_is_stripped_of_dots_and_footnotes(monkeypatch):
    _install_fake_census(monkeypatch)

    result = await get_population_stats(region="NCR")

    assert result["population"] == 14_001_751
    assert result["geography"] == "National Capital Region (NCR)"
    assert result["geography_level"] == "region"
    assert result["psgc_code"] == "1300000000"


@pytest.mark.asyncio
async def test_a_highly_urbanized_city_row_is_labelled_as_one(monkeypatch):
    _install_fake_census(monkeypatch)

    result = await get_population_stats(region="City of Manila")

    assert result["population"] == 1_846_513
    assert result["geography"] == "City of Manila"
    assert result["geography_level"] == "highly_urbanized_city"


@pytest.mark.asyncio
async def test_2020_geography_codes_are_not_psgc(monkeypatch):
    _install_fake_census(monkeypatch)

    result = await get_population_stats(region="NCR", year=2020)

    assert result["population"] == 13_484_462
    assert result["geography"] == "NATIONAL CAPITAL REGION (NCR)"
    assert result["psgc_code"] is None


@pytest.mark.parametrize(
    "raw, cleaned",
    [
        ("Philippines a", "Philippines"),
        ("PHILIPPINES /a", "PHILIPPINES"),
        ("..Negros Island Region (NIR) 3/", "Negros Island Region (NIR)"),
        ("....City of Makati 1/", "City of Makati"),
        ("....Special Geographic Area 8**", "Special Geographic Area"),
        ("....Eight (8) Area Clusters ***", "Eight (8) Area Clusters"),
        ("..Region I (Ilocos Region)", "Region I (Ilocos Region)"),
    ],
)
def test_geo_label_cleaning(raw, cleaned):
    assert psa_module._clean_geo_label(raw) == cleaned


@pytest.mark.asyncio
async def test_unknown_region_is_a_validation_error_not_cached(monkeypatch):
    _install_fake_census(monkeypatch)

    result = await get_population_stats(region="Wakanda")

    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["population"] is None
    assert "Wakanda" in result["caveats"][0]
    assert len(CACHES["psa_population"]) == 0


@pytest.mark.asyncio
async def test_a_query_with_no_rows_is_indeterminate_not_zero(monkeypatch):
    _install_fake_census(monkeypatch)
    # Manila sits in the geography list but the fake cell store has no row for
    # it under the 2020 table, which is how a PXWeb gap presents.
    CELLS["0011A6DPHH0.px"].pop("2")
    try:
        result = await get_population_stats(region="City of Manila", year=2020)
    finally:
        CELLS["0011A6DPHH0.px"]["2"] = "1846513"

    assert result["upstream_error"] is True
    assert result["population"] is None
    assert len(CACHES["psa_population"]) == 0


# ---------------------------------------------------------------------------
# psgc_code: the 2024 Census down to barangay level
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_barangay_code_reads_its_regional_table(monkeypatch):
    seen: list[str] = []
    _install_fake_census(monkeypatch, seen=seen)
    _install_fake_psgc(monkeypatch)

    result = await get_population_stats(psgc_code="1380100001")

    assert result["population"] == 2356
    assert result["geography"] == "Barangay 1"
    assert result["geography_level"] == "barangay"
    assert result["psgc_code"] == "1380100001"
    assert result["region"] == "National Capital Region (NCR)"
    assert result["year"] == 2024
    assert result["source_table"].endswith("/DB/1A/PO_2024/0171A6DTHP6.px")
    posts = [c for c in seen if c.startswith("POST")]
    assert len(posts) == 1 and posts[0].endswith("0171A6DTHP6.px"), posts


@pytest.mark.asyncio
async def test_a_nine_digit_code_is_widened_through_psgc(monkeypatch):
    _install_fake_census(monkeypatch)
    _install_fake_psgc(monkeypatch)

    result = await get_population_stats(psgc_code="083747000")

    assert result["population"] == 259_353
    assert result["geography"] == "City of Tacloban"
    assert result["geography_level"] == "city"
    assert result["psgc_code"] == "0831600000"
    assert result["region"] == "Region VIII (Eastern Visayas)"
    assert result["source_table"].endswith("/DB/1A/PO_2024/0081A6DTPH7.px")


@pytest.mark.asyncio
async def test_a_province_code_reads_the_summary_table(monkeypatch):
    seen: list[str] = []
    _install_fake_census(monkeypatch, seen=seen)
    _install_fake_psgc(monkeypatch)

    result = await get_population_stats(psgc_code="0803700000")

    assert result["population"] == 1_823_458
    assert result["geography"] == "Leyte"
    assert result["geography_level"] == "province"
    assert result["source_table"].endswith("0191A6DTHP8.px")


@pytest.mark.parametrize("bad", ["12ab", "12345678901", "../x", "１２３４５６７８９０", ""])
@pytest.mark.asyncio
async def test_a_malformed_psgc_code_never_reaches_the_wire(monkeypatch, bad):
    seen: list[str] = []
    _install_fake_census(monkeypatch, seen=seen)
    _install_fake_psgc(monkeypatch)

    result = await get_population_stats(psgc_code=bad)

    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert seen == []


@pytest.mark.asyncio
async def test_region_and_psgc_code_together_is_a_validation_error(monkeypatch):
    seen: list[str] = []
    _install_fake_census(monkeypatch, seen=seen)

    result = await get_population_stats(region="NCR", psgc_code="1300000000")

    assert result["validation_error"] is True
    assert seen == []


@pytest.mark.asyncio
async def test_a_code_absent_from_every_table_is_empty_not_cached(monkeypatch):
    _install_fake_census(monkeypatch)

    async def _unknown(code: str):
        return {
            "psgc_code": "999999999",
            "psgc_10digit_code": "0899999999",
            "name": "Nowhere",
            "level": "barangay",
            "region_code": "080000000",
        }

    monkeypatch.setattr(psa_module, "lookup_psgc_code", _unknown)

    result = await get_population_stats(psgc_code="0899999999")

    assert result["population"] is None
    assert result["data_status"] == "empty"
    assert result["upstream_error"] is False
    assert len(CACHES["psa_population"]) == 0


@pytest.mark.asyncio
async def test_sub_provincial_lookups_need_the_2024_vintage(monkeypatch):
    seen: list[str] = []
    _install_fake_census(monkeypatch, seen=seen)
    _install_fake_psgc(monkeypatch)

    result = await get_population_stats(psgc_code="1380100001", year=2020)

    assert result["validation_error"] is True
    assert "2024" in result["caveats"][0]
    assert not any(c.startswith("POST") for c in seen)


@pytest.mark.asyncio
async def test_psgc_lookup_outage_still_returns_the_figure_with_a_caveat(monkeypatch):
    """PSA can answer a 10-digit code even when the PSGC mirror is down."""
    _install_fake_census(monkeypatch)

    async def _down(code: str):
        raise httpx.ConnectError("psgc mirror down")

    monkeypatch.setattr(psa_module, "lookup_psgc_code", _down)

    result = await get_population_stats(psgc_code="1380100001")

    assert result["population"] == 2356
    assert result["geography"] is None
    assert result["geography_level"] == "barangay"
    assert any("PSGC" in c for c in result["caveats"])
    # A partial answer never enters the 24h cache.
    assert len(CACHES["psa_population"]) == 0


# ---------------------------------------------------------------------------
# The shared failure contract
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# lookup_psgc_code: a 429 or 403 is an outage, never "the code is unknown"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lookup_psgc_code_raises_on_rate_limiting_not_unknown(monkeypatch):
    """Codex cross-model finding: only >=500 raised, so a 429 read as a miss."""
    from ph_civic_data_mcp.sources import psgc as psgc_module

    async def _rate_limited(client, method, url, **kwargs):
        return httpx.Response(429, text="slow down", request=httpx.Request(method, url))

    monkeypatch.setattr(psgc_module, "fetch_with_retry", _rate_limited)

    with pytest.raises(httpx.HTTPStatusError):
        await psgc_module.lookup_psgc_code("083747000")


@pytest.mark.asyncio
async def test_lookup_psgc_code_treats_404_as_try_the_next_level(monkeypatch):
    from ph_civic_data_mcp.sources import psgc as psgc_module

    calls: list[str] = []

    async def _fake(client, method, url, **kwargs):
        calls.append(url)
        if url.endswith("/cities-municipalities/083747000/"):
            return httpx.Response(
                200,
                json={
                    "code": "083747000",
                    "name": "City of Tacloban",
                    "psgc10DigitCode": "0831600000",
                },
                request=httpx.Request(method, url),
            )
        return httpx.Response(404, request=httpx.Request(method, url))

    monkeypatch.setattr(psgc_module, "fetch_with_retry", _fake)

    record = await psgc_module.lookup_psgc_code("083747000")

    assert record is not None
    assert record["psgc_10digit_code"] == "0831600000"
    assert len(calls) == 3  # regions, provinces, then cities-municipalities


@pytest.mark.asyncio
async def test_a_psgc_rate_limit_becomes_an_upstream_error_not_a_bad_code(monkeypatch):
    """End to end: get_population_stats must not call a rate-limited code invalid."""
    _install_fake_census(monkeypatch)

    async def _rate_limited(code: str):
        import httpx as httpx_mod

        raise httpx_mod.HTTPStatusError(
            "429", request=httpx_mod.Request("GET", "https://x"), response=httpx_mod.Response(429)
        )

    monkeypatch.setattr(psa_module, "lookup_psgc_code", _rate_limited)

    result = await get_population_stats(psgc_code="083747000")

    assert result["upstream_error"] is True
    assert result["validation_error"] is False
    assert result["data_status"] == "unavailable"


def test_failure_result_shape():
    out = envelope.failure_result("PSA", "https://x", "boom", population=None, region="NCR")
    assert out["upstream_error"] is True
    assert out["validation_error"] is False
    assert out["data_status"] == "unavailable"
    assert out["caveats"] == ["boom"]
    assert out["population"] is None and out["region"] == "NCR"
    assert out["source"] == "PSA" and out["source_url"] == "https://x"
    assert out["data_retrieved_at"]


def test_failure_result_validation_shape():
    out = envelope.failure_result("PSA", "https://x", ["bad year"], validation_error=True)
    assert out["validation_error"] is True
    assert out["upstream_error"] is False
    assert out["data_status"] == "invalid_request"


def test_failure_envelope_keeps_the_list_contract():
    out = envelope.failure_envelope("PHIVOLCS", "https://x", "down", license="Public")
    assert out["results"] == []
    assert out["upstream_error"] is True
    assert out["license"] == "Public"
    assert envelope.is_failure(out)
    assert not envelope.is_failure({"population": 1})
    assert not envelope.is_failure([])


# ---------------------------------------------------------------------------
# get_area_profile folds a sibling's failure envelope into its caveats
# ---------------------------------------------------------------------------


@pytest.fixture()
def _profile_mocks(monkeypatch):
    async def _resolve(location):
        return {
            "matched": True,
            "psgc_code": "130000000",
            "name": "National Capital Region",
            "level": "region",
            "alternatives": [],
        }

    async def _hierarchy(code):
        return {"chain": [{"level": "region", "name": "National Capital Region"}]}

    async def _ok_dict(*args, **kwargs):
        return {"reference_period": "2026-07", "headline_inflation_pct": 1.2}

    async def _ok_list(*args, **kwargs):
        return []

    monkeypatch.setattr(autostitch_module, "resolve_ph_location", _resolve)
    monkeypatch.setattr(autostitch_module, "get_location_hierarchy", _hierarchy)
    monkeypatch.setattr(autostitch_module, "get_poverty_stats", _ok_dict)
    monkeypatch.setattr(autostitch_module, "get_inflation_stats", _ok_dict)
    monkeypatch.setattr(autostitch_module, "get_labor_stats", _ok_dict)
    monkeypatch.setattr(autostitch_module, "assess_area_risk", _ok_dict)
    monkeypatch.setattr(autostitch_module, "get_weather_forecast", _ok_dict)
    monkeypatch.setattr(autostitch_module, "search_infra_projects", _ok_list)
    yield


@pytest.mark.asyncio
async def test_area_profile_surfaces_a_population_envelope(_profile_mocks, monkeypatch):
    async def _pop_envelope(**kwargs):
        return envelope.failure_result(
            "PSA", "https://openstat", "PSA fetch error: ConnectError: down", population=None
        )

    monkeypatch.setattr(autostitch_module, "get_population_stats", _pop_envelope)

    profile = await autostitch_module.get_area_profile("NCR")

    assert profile["demographics"]["population"] is None
    assert profile["blocks"]["population"] == "unavailable"
    assert any("PSA population" in c and "ConnectError" in c for c in profile["caveats"])
    assert profile["upstream_error"] is True
    assert profile["correlations"]["infra_notices_per_100k_population"] is None


@pytest.mark.asyncio
async def test_area_profile_names_the_exception_it_caught(_profile_mocks, monkeypatch):
    async def _pop_raise(**kwargs):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(autostitch_module, "get_population_stats", _pop_raise)

    profile = await autostitch_module.get_area_profile("NCR")

    assert profile["blocks"]["population"] == "unavailable"
    assert any("ReadTimeout" in c and "slow" in c for c in profile["caveats"])


@pytest.mark.asyncio
async def test_area_profile_marks_every_block_when_all_succeed(_profile_mocks, monkeypatch):
    async def _pop_ok(**kwargs):
        return {"population": 14_001_751, "year": 2024, "reference_note": "2024 Census"}

    monkeypatch.setattr(autostitch_module, "get_population_stats", _pop_ok)

    profile = await autostitch_module.get_area_profile("NCR")

    assert profile["demographics"]["population"] == 14_001_751
    assert profile["demographics"]["population_year"] == 2024
    assert profile["blocks"]["population"] == "success"
    assert profile["upstream_error"] is False
    assert profile["caveats"] == []


@pytest.mark.asyncio
async def test_area_profile_never_reads_a_resolver_outage_as_an_unknown_place(
    _profile_mocks, monkeypatch
):
    """Codex cross-model finding: a PSGC outage looked identical to "no match"."""

    async def _resolve_down(location):
        return {
            "query": location,
            "matched": False,
            "upstream_error": True,
            "caveats": ["PSGC API unavailable (ConnectError: down)."],
            "source": "PSGC",
        }

    async def _pop_ok(**kwargs):
        return {"population": 1, "year": 2024}

    monkeypatch.setattr(autostitch_module, "resolve_ph_location", _resolve_down)
    monkeypatch.setattr(autostitch_module, "get_population_stats", _pop_ok)

    profile = await autostitch_module.get_area_profile("Leyte")

    assert profile["blocks"]["resolve"] == "unavailable"
    assert profile["upstream_error"] is True
    assert any("PSGC resolve" in c and "ConnectError" in c for c in profile["caveats"])
    assert not any("did not resolve to a PSGC record" in c for c in profile["caveats"])
    assert profile["resolved"]["matched"] is False


@pytest.mark.asyncio
async def test_area_profile_folds_a_hierarchy_outage(_profile_mocks, monkeypatch):
    async def _hierarchy_down(code):
        return {
            "psgc_code": code,
            "chain": [],
            "upstream_error": True,
            "caveats": ["PSGC API unavailable while walking hierarchy (ConnectError)."],
        }

    async def _pop_ok(**kwargs):
        return {"population": 1, "year": 2024}

    monkeypatch.setattr(autostitch_module, "get_location_hierarchy", _hierarchy_down)
    monkeypatch.setattr(autostitch_module, "get_population_stats", _pop_ok)

    profile = await autostitch_module.get_area_profile("NCR")

    assert profile["blocks"]["hierarchy"] == "unavailable"
    assert profile["upstream_error"] is True
    assert any("PSGC hierarchy" in c for c in profile["caveats"])


@pytest.mark.asyncio
async def test_area_profile_folds_an_infra_envelope_the_same_way(_profile_mocks, monkeypatch):
    async def _pop_ok(**kwargs):
        return {"population": 14_001_751, "year": 2024}

    async def _infra_envelope(**kwargs):
        return envelope.failure_envelope("PhilGEPS", "https://philgeps", "listing unavailable")

    monkeypatch.setattr(autostitch_module, "get_population_stats", _pop_ok)
    monkeypatch.setattr(autostitch_module, "search_infra_projects", _infra_envelope)

    profile = await autostitch_module.get_area_profile("NCR")

    assert profile["blocks"]["infra"] == "unavailable"
    assert profile["procurement"]["infra_notice_count"] is None
    assert profile["correlations"]["infra_notices_per_100k_population"] is None
    assert any("PhilGEPS infra search" in c for c in profile["caveats"])
