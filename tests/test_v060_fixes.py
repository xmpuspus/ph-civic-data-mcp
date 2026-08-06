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


def _fake_clock(monkeypatch, psa_mod):
    """Drive the limiter on a fake clock so no test pays a real sleep."""
    import asyncio

    clock = {"t": 1000.0}
    waits: list[float] = []

    class _Loop:
        def time(self):
            return clock["t"]

    async def _sleep(seconds):
        waits.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: _Loop())
    monkeypatch.setattr(asyncio, "sleep", _sleep)
    return waits


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
async def test_a_cold_burst_of_ten_never_waits(monkeypatch):
    """The bucket starts full so one composite's fan-out goes straight out."""
    from ph_civic_data_mcp.sources import psa as psa_mod

    psa_mod._reset_rate_limiter()
    waits = _fake_clock(monkeypatch, psa_mod)

    for _ in range(psa_mod.PSA_RATE_LIMIT_REQUESTS):
        await psa_mod._psa_rate_limit()
    assert waits == [], f"a burst of 10 must not wait, waited {waits}"


@pytest.mark.asyncio
async def test_the_eleventh_call_waits_one_second_not_a_whole_window(monkeypatch):
    """The regression this replaced stalled get_area_profile for 10 seconds."""
    from ph_civic_data_mcp.sources import psa as psa_mod

    psa_mod._reset_rate_limiter()
    waits = _fake_clock(monkeypatch, psa_mod)

    for _ in range(psa_mod.PSA_RATE_LIMIT_REQUESTS + 1):
        await psa_mod._psa_rate_limit()

    assert len(waits) == 1, f"only the 11th call waits, got {waits}"
    assert waits[0] == pytest.approx(1.0, abs=0.01), waits
    assert waits[0] < psa_mod.PSA_RATE_LIMIT_WINDOW_SECONDS / 2


@pytest.mark.asyncio
async def test_sustained_rate_matches_the_published_cap(monkeypatch):
    """30 back-to-back calls must not beat 10 per 10 seconds over the tail."""
    from ph_civic_data_mcp.sources import psa as psa_mod

    psa_mod._reset_rate_limiter()
    waits = _fake_clock(monkeypatch, psa_mod)

    total = 30
    for _ in range(total):
        await psa_mod._psa_rate_limit()

    # The bucket lets the first 10 through free; the other 20 pay 1s each.
    beyond_burst = total - psa_mod.PSA_RATE_LIMIT_REQUESTS
    assert sum(waits) == pytest.approx(beyond_burst / psa_mod.PSA_REFILL_PER_SECOND, abs=0.05)


@pytest.mark.asyncio
async def test_the_limiter_does_not_hold_its_lock_while_it_sleeps(monkeypatch):
    """Holding it would serialize every gathered PSA call behind one wait."""
    import asyncio

    from ph_civic_data_mcp.sources import psa as psa_mod

    psa_mod._reset_rate_limiter()
    locked_during_sleep = []

    async def _watch(seconds):
        locked_during_sleep.append(psa_mod._RATE_LOCK.locked())

    monkeypatch.setattr(asyncio, "sleep", _watch)
    for _ in range(psa_mod.PSA_RATE_LIMIT_REQUESTS + 2):
        await psa_mod._psa_rate_limit()

    assert locked_during_sleep, "expected at least one wait"
    assert not any(locked_during_sleep), "the lock must be free during the sleep"
    psa_mod._reset_rate_limiter()


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


@pytest.mark.asyncio
async def test_a_poverty_query_outage_says_upstream_error(monkeypatch):
    """A failed POST used to come back as a plain no-data domain answer."""
    _clear_psa_state()
    calls = {"n": 0}

    async def _fail_on_post(client, method, url, **kwargs):
        if method == "POST":
            calls["n"] += 1
            raise httpx.ConnectError("openstat dropped the query")
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
        return httpx.Response(404, text="no", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fail_on_post)

    result = await psa_module.get_poverty_stats()
    assert result["upstream_error"] is True
    assert any("ConnectError" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["psa_poverty"]) == 0


@pytest.mark.asyncio
async def test_an_unpublished_poverty_cell_is_not_an_outage(monkeypatch):
    """PSA writes '..' for a cell it does not publish. That is data, not a fault."""
    _clear_psa_state()

    async def _missing_cell(client, method, url, **kwargs):
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
                    "data": [{"key": ["0", "1", "2"], "values": [".."]}],
                },
            )
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
        return httpx.Response(404, text="no", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _missing_cell)

    result = await psa_module.get_poverty_stats()
    assert result["poverty_incidence_pct"] is None
    assert not result.get("upstream_error"), "a published '..' is not an outage"
    assert any("'..'" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["psa_poverty"]) == 0


# ---------------------------------------------------------------------------
# Round-2 review findings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,expected",
    [
        ("I", "Region I (Ilocos Region)"),
        ("i", "Region I (Ilocos Region)"),
        ("Region I", "Region I (Ilocos Region)"),
        ("NCR", "National Capital Region (NCR)"),
        ("ncr", "National Capital Region (NCR)"),
        ("Ilocos", "Region I (Ilocos Region)"),
    ],
)
def test_a_short_region_code_does_not_match_philippines(query, expected):
    """A bare `in` let the region code "I" match "philippines"."""
    meta = {
        "variables": [
            {
                "code": "Geolocation",
                "values": ["0", "1", "2"],
                "valueTexts": [
                    "PHILIPPINES",
                    "..National Capital Region (NCR)",
                    "....Region I (Ilocos Region)",
                ],
            }
        ]
    }
    hit = psa_module._find_geo_value(meta, query, "Geolocation")
    assert hit is not None, f"{query!r} resolved to nothing"
    assert hit[1] == expected, f"{query!r} resolved to {hit[1]!r}"


def test_token_match_rejects_a_fragment():
    assert psa_module._token_match("i", "region i (ilocos)") is True
    assert psa_module._token_match("i", "philippines") is False
    assert psa_module._token_match("cor", "cordillera") is False, "a prefix is not a token"
    assert psa_module._token_match("cordillera", "cordillera administrative region") is True
    assert psa_module._token_match("", "anything") is False


def test_region_i_does_not_match_region_ii():
    """Whichever the catalog lists first, each must resolve to itself."""
    meta = {
        "variables": [
            {
                "code": "Geolocation",
                "values": ["0", "1", "2"],
                "valueTexts": [
                    "PHILIPPINES",
                    "..Region II (Cagayan Valley)",
                    "....Region I (Ilocos Region)",
                ],
            }
        ]
    }
    assert psa_module._find_geo_value(meta, "Region I", "Geolocation")[1].startswith("Region I ")
    assert psa_module._find_geo_value(meta, "Region II", "Geolocation")[1].startswith("Region II")
    assert psa_module._find_geo_value(meta, "I", "Geolocation")[1].startswith("Region I ")
    assert psa_module._find_geo_value(meta, "II", "Geolocation")[1].startswith("Region II")


@pytest.mark.asyncio
async def test_concurrent_cold_browses_hit_the_catalog_once(monkeypatch):
    import asyncio

    _clear_psa_state()
    fetches = {"n": 0}

    async def _slow(client, method, url, **kwargs):
        fetches["n"] += 1
        await asyncio.sleep(0.01)
        return _json_response(method, url, ROOT_ENTRIES)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _slow)
    results = await asyncio.gather(*[psa_module._browse("1F") for _ in range(20)])
    assert all(r == ROOT_ENTRIES for r in results)
    assert fetches["n"] == 1, f"browsed {fetches['n']} times, expected 1"


def test_the_path_lock_registry_is_bounded():
    _clear_psa_state()
    psa_module._PATH_LOCKS.clear()
    for i in range(psa_module._MAX_PATH_LOCKS + 20):
        psa_module._browse_lock(f"path-{i}")
    assert len(psa_module._PATH_LOCKS) <= psa_module._MAX_PATH_LOCKS
    psa_module._PATH_LOCKS.clear()


@pytest.mark.asyncio
async def test_a_health_table_that_fails_to_load_is_reported(monkeypatch):
    """Skipping it silently made a fetch failure look like an unpublished set."""
    _clear_psa_state()
    CACHES["psa_health"].clear()

    listing = [
        {"id": "a.px", "type": "t", "text": "Maternal Mortality Ratio"},
        {"id": "b.px", "type": "t", "text": "Total Fertility Rate"},
    ]

    async def _one_table_down(client, method, url, **kwargs):
        if url.endswith("/DB/1D/"):
            return _json_response(method, url, listing)
        if url.endswith("a.px"):
            raise httpx.ConnectError("that table is down")
        if url.endswith("b.px"):
            return _json_response(
                method,
                url,
                {"title": "Total Fertility Rate", "variables": [], "data": []},
            )
        return httpx.Response(404, text="no", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _one_table_down)

    result = await psa_module.get_health_indicators()
    assert result["upstream_error"] is True
    assert any("did not load" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["psa_health"]) == 0, "a partial answer must not cache"


# ---------------------------------------------------------------------------
# Round-4 review findings
# ---------------------------------------------------------------------------


def test_year_from_label_refuses_to_invent_a_year():
    """The old fallback published a hardcoded 2023 nobody measured."""
    assert psa_module._year_from_label("2023") == 2023
    assert psa_module._year_from_label("FY 2021") == 2021
    assert psa_module._year_from_label("latest") is None
    assert psa_module._year_from_label("") is None
    assert psa_module._year_from_label("99") is None
    assert psa_module._year_from_label("3199") is None


@pytest.mark.asyncio
async def test_a_poverty_table_with_no_incidence_dimension_does_not_crash(monkeypatch):
    _clear_psa_state()
    broken_meta = {
        "title": "Table 1. Something else entirely",
        "variables": [
            {
                "code": "Geolocation",
                "values": ["0"],
                "valueTexts": ["PHILIPPINES"],
            }
        ],
    }

    async def _fake(client, method, url, **kwargs):
        if url.endswith("/DB/"):
            return _json_response(method, url, ROOT_ENTRIES)
        if url.endswith("/DB/1F/"):
            return _json_response(method, url, POVERTY_SUBJECT_ENTRIES)
        if url.endswith("/DB/1F/FY/"):
            return _json_response(method, url, FY_TABLE_ENTRIES)
        if url.endswith(".px"):
            return _json_response(method, url, broken_meta)
        return httpx.Response(404, text="no", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)

    result = await psa_module.get_poverty_stats()
    assert result["upstream_error"] is True
    assert any("Incidence" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["psa_poverty"]) == 0


@pytest.mark.asyncio
async def test_a_subsistence_query_outage_is_reported_and_not_cached(monkeypatch):
    """The poverty figure is still right; the null subsistence is an outage."""
    _clear_psa_state()

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            if "0051F3DF030" in url:
                raise httpx.ConnectError("subsistence table dropped")
            return _json_response(
                method,
                url,
                {
                    "columns": [
                        {"code": "Geolocation", "type": "d"},
                        {"code": "Threshold/Incidence/Measures of Precision", "type": "d"},
                        {"code": "Year", "type": "d"},
                        {"code": "P", "type": "c"},
                    ],
                    "data": [{"key": ["0", "1", "2"], "values": ["10.9"]}],
                },
            )
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
        return httpx.Response(404, text="no", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)

    result = await psa_module.get_poverty_stats()
    assert result["poverty_incidence_pct"] == pytest.approx(10.9)
    assert result["subsistence_incidence_pct"] is None
    assert result["upstream_error"] is True
    assert any("Subsistence query failed" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["psa_poverty"]) == 0, "a partial answer must not cache"


@pytest.mark.parametrize(
    "query,expected_fragment",
    [
        ("Metro Manila", "National Capital Region"),
        ("Region IV-B", "MIMAROPA"),
        ("IV-B", "MIMAROPA"),
        ("Region IV-A", "CALABARZON"),
        ("CAR", "Cordillera"),
        ("BARMM", "Bangsamoro"),
    ],
)
def test_region_aliases_the_live_probe_found_missing(query, expected_fragment):
    """A live probe of the 108-entry list found these four with no match."""
    meta = {
        "variables": [
            {
                "code": "Geolocation",
                "values": ["0", "1", "2", "3", "4", "5"],
                "valueTexts": [
                    "PHILIPPINES",
                    "..National Capital Region (NCR)",
                    "..MIMAROPA Region",
                    "..Region IV-A (CALABARZON)",
                    "..Cordillera Administrative Region (CAR)",
                    "..Bangsamoro Autonomous Region in Muslim Mindanao (BARMM)",
                ],
            }
        ]
    }
    hit = psa_module._find_geo_value(meta, query, "Geolocation")
    assert hit is not None, f"{query!r} resolved to nothing"
    assert expected_fragment in hit[1], f"{query!r} resolved to {hit[1]!r}"


def test_a_null_region_on_an_empty_dimension_does_not_crash():
    """region.strip() on None used to raise AttributeError here."""
    meta = {"variables": [{"code": "Geolocation", "values": [], "valueTexts": []}]}
    assert psa_module._find_geo_value(meta, None, "Geolocation") is None


def test_first_cell_refuses_a_wrong_typed_values_field():
    """values[0] on the string '10.9' yields '1' and publishes it as data."""
    good = {"data": [{"key": ["0"], "values": ["10.9"]}]}
    assert psa_module._first_cell(good) == pytest.approx(10.9)

    for bad in (
        {"data": [{"key": ["0"], "values": "10.9"}]},
        {"data": [{"key": ["0"], "values": 10.9}]},
        {"data": ["not-a-row"]},
        {"data": []},
        {"data": "nope"},
        {},
    ):
        assert psa_module._first_cell(bad) is None, bad


@pytest.mark.asyncio
async def test_a_wrong_typed_poverty_cell_is_not_its_first_character(monkeypatch):
    _clear_psa_state()

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _json_response(
                method,
                url,
                {
                    "columns": [{"code": "Geolocation", "type": "d"}],
                    "data": [{"key": ["0"], "values": "10.9"}],
                },
            )
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
        return httpx.Response(404, text="no", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    result = await psa_module.get_poverty_stats()
    assert result.get("poverty_incidence_pct") != 1.0, "read '1' out of the string '10.9'"
    assert result.get("poverty_incidence_pct") is None
    assert len(CACHES["psa_poverty"]) == 0


def test_release_smoke_never_interpolates_the_dispatch_input_into_shell():
    """GitHub substitutes ${{ }} into the script body before bash runs it."""
    import pathlib

    workflow = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/release-smoke.yml"
    text = workflow.read_text()
    in_run = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:"):
            in_run = True
            continue
        if stripped.startswith(("- name:", "env:", "with:", "uses:")):
            in_run = False
        if in_run and "github.event.inputs" in line:
            raise AssertionError(f"dispatch input reaches the shell body: {line.strip()}")
    assert "INPUT_VERSION: ${{ github.event.inputs.version }}" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("sub_year_labels", [["2018", "2021"], ["2018", "unknown"]])
async def test_a_mismatched_subsistence_year_withholds_rather_than_crashes(
    monkeypatch, sub_year_labels
):
    """Setting sub_geo = None mid-block used to reach sub_geo[0] and crash."""
    _clear_psa_state()
    sub_meta = {
        **SUBSISTENCE_META,
        "variables": [
            SUBSISTENCE_META["variables"][0],
            SUBSISTENCE_META["variables"][1],
            {
                "code": "Year",
                "text": "Year",
                "values": ["0", "1"],
                "valueTexts": sub_year_labels,
            },
        ],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _json_response(
                method,
                url,
                {
                    "columns": [{"code": "Geolocation", "type": "d"}],
                    "data": [{"key": ["0"], "values": ["10.9"]}],
                },
            )
        if url.endswith("/DB/"):
            return _json_response(method, url, ROOT_ENTRIES)
        if url.endswith("/DB/1F/"):
            return _json_response(method, url, POVERTY_SUBJECT_ENTRIES)
        if url.endswith("/DB/1F/FY/"):
            return _json_response(method, url, FY_TABLE_ENTRIES)
        if url.endswith("0011F3DF010.px"):
            return _json_response(method, url, POVERTY_META)
        if url.endswith("0051F3DF030.px"):
            return _json_response(method, url, sub_meta)
        return httpx.Response(404, text="no", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)

    result = await psa_module.get_poverty_stats()
    assert result["poverty_incidence_pct"] == pytest.approx(10.9)
    assert result["reference_year"] == 2023
    assert result["subsistence_incidence_pct"] is None
    assert result["upstream_error"] is True
    assert any("withheld" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["psa_poverty"]) == 0


@pytest.mark.asyncio
async def test_a_matching_subsistence_year_is_still_reported(monkeypatch):
    """The withholding guard must not suppress the normal case."""
    _clear_psa_state()
    _install_fake_openstat(monkeypatch)

    result = await psa_module.get_poverty_stats()
    assert result["poverty_incidence_pct"] == pytest.approx(10.9)
    assert result["subsistence_incidence_pct"] == pytest.approx(10.9)
    assert not result.get("upstream_error")


def test_retry_after_accepts_an_http_date():
    """RFC 9110 allows delta-seconds OR an HTTP-date."""
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    from ph_civic_data_mcp.utils import http

    when = datetime.now(timezone.utc) + timedelta(seconds=14)
    assert http._retry_after_seconds(format_datetime(when)) == pytest.approx(14, abs=2)
    assert http._retry_after_seconds("17") == pytest.approx(17.0)
    assert http._retry_after_seconds("  17  ") == pytest.approx(17.0)
    assert http._retry_after_seconds("nonsense") is None
    past = datetime.now(timezone.utc) - timedelta(seconds=60)
    assert http._retry_after_seconds(format_datetime(past)) == pytest.approx(0.0, abs=2)


def test_a_short_value_texts_list_does_not_raise():
    """The empty-values guard covered values but the fallback indexed texts."""
    meta = {"variables": [{"code": "Geolocation", "values": ["0", "1"], "valueTexts": []}]}
    assert psa_module._find_geo_value(meta, None, "Geolocation") is None
    partial = {"variables": [{"code": "Geolocation", "values": ["0", "1"], "valueTexts": ["A"]}]}
    assert psa_module._find_geo_value(partial, None, "Geolocation") == ("0", "A")


@pytest.mark.asyncio
async def test_a_malformed_population_cell_is_never_a_population_of_zero(monkeypatch):
    """The old fallback reported the Philippines as having no people."""
    _clear_psa_state()
    CACHES["psa_population"].clear()

    pop_listing = [{"id": "p.px", "type": "t", "text": "Total population by region and household"}]
    pop_meta = {
        "title": "Total population",
        "variables": [
            {
                "code": "Geographic Location",
                "values": ["0"],
                "valueTexts": ["PHILIPPINES"],
            },
            {"code": "Parameter", "values": ["0"], "valueTexts": ["Total Population"]},
        ],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _json_response(
                method,
                url,
                {
                    "columns": [{"code": "Geographic Location", "type": "d"}],
                    "data": [{"key": ["0"], "values": ["not-a-number"]}],
                },
            )
        if url.endswith("/DB/1A/PO/"):
            return _json_response(method, url, pop_listing)
        if url.endswith("p.px"):
            return _json_response(method, url, pop_meta)
        return httpx.Response(404, text="no", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)

    result = await psa_module.get_population_stats()
    assert result.get("population") != 0, "a parse failure became a population of zero"
    assert result.get("population") is None
    assert result["upstream_error"] is True
    assert len(CACHES["psa_population"]) == 0


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity"])
def test_to_float_refuses_a_non_finite_value(raw):
    """float('nan') succeeds and would JSON-encode as an out-of-spec literal."""
    assert psa_module._to_float(raw) is None


def test_to_float_still_reads_a_real_number():
    assert psa_module._to_float("10.9") == pytest.approx(10.9)
    assert psa_module._to_float("-3") == pytest.approx(-3.0)
    assert psa_module._to_float("..") is None
