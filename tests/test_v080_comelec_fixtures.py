"""Offline tests for the COMELEC 2025 election results source.

The archive is frozen (see CLAUDE.md), so these fixtures never go stale
against a live update. `comelec_er_28010001.json` is the real precinct
return the ledger probe saved from `/data/er/280/28010001.json`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from ph_civic_data_mcp.sources import comelec as comelec_module
from ph_civic_data_mcp.utils.cache import CACHES

FIXTURES = Path(__file__).parent / "fixtures"
ER_FIXTURE = json.loads((FIXTURES / "comelec_er_28010001.json").read_text())

REGIONS = {
    "regions": [
        {"categoryCode": "2", "masterCode": "0", "code": "R001000", "name": "REGION I"},
        {"categoryCode": "2", "masterCode": "0", "code": "R002000", "name": "REGION II"},
    ]
}
PRECINCTS = {
    "regions": [
        {"categoryCode": None, "masterCode": None, "code": "28010001", "name": "28010001"},
        {"categoryCode": None, "masterCode": None, "code": "28010002", "name": "28010002"},
    ]
}
LATEST_TIME = {"date": "16 May 2025", "time": "10:00:09 AM"}


def _route(
    monkeypatch, routes: dict[str, tuple[int, object]], *, exc=None, exc_on: set[str] | None = None
):
    """Fake fetch_with_retry that answers by exact URL.

    A URL not in `routes` gets a 403, matching an archive that answers
    "not found" for anything this test did not expect to be asked.
    """

    async def _fake(client, method, url, **kwargs):
        if exc is not None and (exc_on is None or url in exc_on):
            raise exc
        if url in routes:
            status, body = routes[url]
            return httpx.Response(status, json=body, request=httpx.Request(method, url))
        return httpx.Response(
            403, json={"Error": {"Code": "AccessDenied"}}, request=httpx.Request(method, url)
        )

    monkeypatch.setattr(comelec_module, "fetch_with_retry", _fake)


def _latest_time_url() -> str:
    return f"{comelec_module.COMELEC_BASE}/data/common/latestTime.json"


def _local_url(code: str) -> str:
    return f"{comelec_module.COMELEC_BASE}/data/regions/local/{code}.json"


def _precinct_url(code: str) -> str:
    return f"{comelec_module.COMELEC_BASE}/data/regions/precinct/{code[:2]}/{code}.json"


def _er_url(code: str) -> str:
    return f"{comelec_module.COMELEC_BASE}/data/er/{code[:3]}/{code}.json"


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["comelec_tree"].clear()
    CACHES["comelec_return"].clear()
    CACHES["comelec_meta"].clear()
    # A lock binds to the event loop that first contends it, and
    # pytest-asyncio gives each test a fresh loop. Several tests share a
    # cache key ("28010001"), so a stale lock from an earlier test would
    # bind to a closed loop.
    comelec_module._TREE_LOCKS.clear()
    comelec_module._RETURN_LOCKS.clear()
    yield
    # Clear again after the test. CACHES is a process-wide singleton, so
    # whichever test runs last in this file would otherwise leave a fixture
    # value (2 regions, not 20) sitting in comelec_tree for the live test
    # module that runs next in the same pytest session.
    CACHES["comelec_tree"].clear()
    CACHES["comelec_return"].clear()
    CACHES["comelec_meta"].clear()
    comelec_module._TREE_LOCKS.clear()
    comelec_module._RETURN_LOCKS.clear()


@pytest.mark.asyncio
async def test_tree_success_path_lists_regions(monkeypatch):
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _local_url("0"): (200, REGIONS),
        },
    )

    result = await comelec_module.browse_election_results()

    assert result["data_status"] == "success"
    assert result["level"] == "root"
    assert result["child_count"] == 2
    assert result["children"][0]["code"] == "R001000"
    assert result["data_frozen_at"] == "2025-05-16T10:00:09"


@pytest.mark.asyncio
async def test_403_on_the_local_tree_falls_back_to_precincts(monkeypatch):
    barangay_code = "2801001"
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _local_url(barangay_code): (403, {"Error": {"Code": "AccessDenied"}}),
            _precinct_url(barangay_code): (200, PRECINCTS),
        },
    )

    result = await comelec_module.browse_election_results(code=barangay_code)

    assert result["data_status"] == "success"
    assert result["level"] == "barangay"
    assert [c["code"] for c in result["children"]] == ["28010001", "28010002"]


@pytest.mark.asyncio
async def test_double_403_is_invalid_request_not_an_outage(monkeypatch):
    unknown_code = "9999999"
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _local_url(unknown_code): (403, {"Error": {"Code": "AccessDenied"}}),
            _precinct_url(unknown_code): (403, {"Error": {"Code": "AccessDenied"}}),
        },
    )

    result = await comelec_module.browse_election_results(code=unknown_code)

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False


@pytest.mark.asyncio
async def test_bad_code_shape_is_invalid_request_with_no_request_sent(monkeypatch):
    async def _must_not_fetch(client, method, url, **kwargs):
        raise AssertionError("a malformed code must never reach the network")

    monkeypatch.setattr(comelec_module, "fetch_with_retry", _must_not_fetch)

    result = await comelec_module.browse_election_results(code="not-a-code")

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True


@pytest.mark.parametrize(
    "region_code",
    ["R001000", "R04A000", "R04B000", "R00LAV0", "R00NIR0", "R0BARMM", "R0CAR00", "R0NCR00"],
)
def test_every_live_region_code_shape_is_accepted(region_code):
    """Live-checked 2026-09-04: 6 of the 20 root entries carry a letter in
    the tail (CALABARZON, MIMAROPA, BARMM, CAR, and NCR itself). A
    digits-only region regex rejected all six as invalid_request."""
    assert comelec_module._valid_browse_code(region_code)
    assert comelec_module._level_for_code(region_code) == "region"


@pytest.mark.asyncio
async def test_transport_failure_on_browse_is_unavailable(monkeypatch):
    _route(
        monkeypatch,
        {_latest_time_url(): (200, LATEST_TIME)},
        exc=httpx.ConnectError("no route"),
        exc_on={_local_url("0")},
    )

    result = await comelec_module.browse_election_results()

    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert "COMELEC results archive unavailable" in result["caveats"][0]


@pytest.mark.asyncio
async def test_latest_time_failure_degrades_to_a_caveat_not_a_tool_failure(monkeypatch):
    _route(
        monkeypatch,
        {_local_url("0"): (200, REGIONS)},
        exc=httpx.ConnectError("no route"),
        exc_on={_latest_time_url()},
    )

    result = await comelec_module.browse_election_results()

    assert result["data_status"] == "success"
    assert result["data_frozen_at"] is None
    assert any("freeze time unavailable" in c for c in result["caveats"])
    assert not CACHES["comelec_tree"], "a degraded data_frozen_at must not pin for the full TTL"


@pytest.mark.asyncio
async def test_election_return_success_path_has_candidates(monkeypatch):
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("28010001"): (200, ER_FIXTURE),
        },
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "success"
    assert result["precinct_code"] == "28010001"
    assert result["information"]["machine_id"] == "28010001"
    assert result["information"]["voting_center"] == "POBLACION 1, ADAMS, ILOCOS NORTE"
    assert result["total_er_received"] == 100.0

    senator = result["national_contests"][0]
    assert senator["contest_code"] == "00399000"
    assert senator["contest_name"] == "SENATOR of PHILIPPINES"
    assert senator["statistics"]["validVotes"] == 11916
    top_candidate = senator["candidates"][0]
    assert top_candidate["name"] == "1. ABALOS, BENHUR (PFP)"
    assert top_candidate["votes"] == 179
    assert top_candidate["percentage"] == 5.28

    mayor = [c for c in result["local_contests"] if c["contest_code"] == "00828010"][0]
    assert len(mayor["candidates"]) == 4


@pytest.mark.asyncio
async def test_unknown_precinct_is_invalid_request(monkeypatch):
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("99999999"): (403, {"Error": {"Code": "AccessDenied"}}),
        },
    )

    result = await comelec_module.get_election_return("99999999")

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False


@pytest.mark.asyncio
async def test_bad_precinct_code_shape_is_invalid_request_with_no_request_sent(monkeypatch):
    async def _must_not_fetch(client, method, url, **kwargs):
        raise AssertionError("a malformed precinct_code must never reach the network")

    monkeypatch.setattr(comelec_module, "fetch_with_retry", _must_not_fetch)

    result = await comelec_module.get_election_return("2801000")  # 7 digits, not 8

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True


@pytest.mark.asyncio
async def test_transport_failure_on_election_return_is_unavailable(monkeypatch):
    _route(
        monkeypatch,
        {_latest_time_url(): (200, LATEST_TIME)},
        exc=httpx.ConnectError("no route"),
        exc_on={_er_url("28010001")},
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True


@pytest.mark.asyncio
async def test_malformed_200_body_is_indeterminate_not_cached(monkeypatch):
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("28010001"): (200, {"totalErReceived": 1.0, "national": "not-a-list"}),
        },
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert not CACHES["comelec_return"], "a malformed body must never be cached"


@pytest.mark.asyncio
async def test_children_are_capped_at_500_with_a_truncated_flag(monkeypatch):
    big_list = {"regions": [{"code": f"280100{i:02d}"} for i in range(600)]}
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _local_url("2801000"): (200, big_list),
        },
    )

    result = await comelec_module.browse_election_results(code="2801000")

    assert result["data_status"] == "success"
    assert result["child_count"] == 500
    assert result["truncated"] is True


# --- Finding 1: a 403 is an unknown code only with an AccessDenied body ---


@pytest.mark.asyncio
async def test_403_with_access_denied_body_is_invalid_request(monkeypatch):
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("28010001"): (403, {"Error": {"Code": "AccessDenied"}}),
        },
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False


@pytest.mark.asyncio
async def test_403_with_html_body_is_unavailable_not_invalid_request(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        if url == _latest_time_url():
            return httpx.Response(200, json=LATEST_TIME, request=httpx.Request(method, url))
        if url == _er_url("28010001"):
            return httpx.Response(
                403,
                content=b"<html><body>Request blocked</body></html>",
                request=httpx.Request(method, url),
            )
        raise AssertionError(f"unexpected url {url}")

    monkeypatch.setattr(comelec_module, "fetch_with_retry", _fake)

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert "403" in result["caveats"][0]


# --- Finding 2: a malformed 'local' section, not just 'national' ---


@pytest.mark.asyncio
async def test_malformed_local_section_is_indeterminate_not_cached(monkeypatch):
    payload = {
        "totalErReceived": ER_FIXTURE["totalErReceived"],
        "information": ER_FIXTURE["information"],
        "national": ER_FIXTURE["national"],
        "local": "not-a-list",
    }
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("28010001"): (200, payload),
        },
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert not CACHES["comelec_return"], "a malformed local section must never be cached"


@pytest.mark.asyncio
async def test_missing_local_key_still_succeeds(monkeypatch):
    """A missing 'local' key is a genuine empty local ballot, not drift."""
    payload = {
        "totalErReceived": ER_FIXTURE["totalErReceived"],
        "information": ER_FIXTURE["information"],
        "national": ER_FIXTURE["national"],
    }
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("28010001"): (200, payload),
        },
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "success"
    assert result["local_contests"] == []


# --- Finding 3: a non-list nested 'candidates' value must not raise ---


@pytest.mark.asyncio
async def test_non_list_nested_candidates_does_not_raise(monkeypatch):
    payload = {
        "totalErReceived": 1.0,
        "information": ER_FIXTURE["information"],
        "national": [{"candidates": {"candidates": 1}}],
        "local": [],
    }
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("28010001"): (200, payload),
        },
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert not CACHES["comelec_return"], "a malformed contest must never be cached"


@pytest.mark.asyncio
async def test_non_list_nested_candidates_names_the_contest_code_in_caveats(monkeypatch):
    payload = {
        "totalErReceived": 1.0,
        "information": ER_FIXTURE["information"],
        "national": [{"contestCode": "00399000", "candidates": {"candidates": 1}}],
        "local": [],
    }
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("28010001"): (200, payload),
        },
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "indeterminate"
    assert "00399000" in result["caveats"][0]


# --- Finding 4: malformed tree rows must not cache as an empty success ---


@pytest.mark.asyncio
async def test_tree_rows_all_unparseable_is_indeterminate_not_cached(monkeypatch):
    bad = {"regions": [{"name": "REGION I"}]}
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _local_url("0"): (200, bad),
        },
    )

    result = await comelec_module.browse_election_results()

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert not CACHES["comelec_tree"], "an all-bad tree response must never be cached"


@pytest.mark.asyncio
async def test_tree_rows_partly_unparseable_returns_good_rows_with_a_caveat(monkeypatch):
    mixed = {
        "regions": [
            {"code": "R001000", "name": "REGION I"},
            {"name": "NO CODE HERE"},
        ]
    }
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _local_url("0"): (200, mixed),
        },
    )

    result = await comelec_module.browse_election_results()

    assert result["data_status"] == "success"
    assert result["child_count"] == 1
    assert result["children"][0]["code"] == "R001000"
    assert any("skipped" in c for c in result["caveats"])


# --- Finding 5: a bounded per-key lock closes the cache-write race ---


@pytest.mark.asyncio
async def test_twenty_concurrent_calls_reach_the_fake_once(monkeypatch):
    calls = 0

    async def _fake(client, method, url, **kwargs):
        nonlocal calls
        if url == _latest_time_url():
            await asyncio.sleep(0)
            return httpx.Response(200, json=LATEST_TIME, request=httpx.Request(method, url))
        calls += 1
        await asyncio.sleep(0)
        return httpx.Response(200, json=ER_FIXTURE, request=httpx.Request(method, url))

    monkeypatch.setattr(comelec_module, "fetch_with_retry", _fake)

    results = await asyncio.gather(
        *(comelec_module.get_election_return("28010001") for _ in range(20))
    )

    assert calls == 1
    assert all(r["data_status"] == "success" for r in results)


# --- 2026-09-04 review, finding 1: the tree fetch needs the same ---
# --- AccessDenied-body rule get_election_return already applies ---


@pytest.mark.asyncio
async def test_browse_403_with_access_denied_body_is_invalid_request(monkeypatch):
    code = "2801000"
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _local_url(code): (403, {"Error": {"Code": "AccessDenied"}}),
            _precinct_url(code): (403, {"Error": {"Code": "AccessDenied"}}),
        },
    )

    result = await comelec_module.browse_election_results(code=code)

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False


@pytest.mark.asyncio
async def test_browse_403_with_html_body_is_unavailable_not_invalid_request(monkeypatch):
    code = "2801000"

    async def _fake(client, method, url, **kwargs):
        if url == _latest_time_url():
            return httpx.Response(200, json=LATEST_TIME, request=httpx.Request(method, url))
        return httpx.Response(
            403,
            content=b"<html><body>Request blocked</body></html>",
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(comelec_module, "fetch_with_retry", _fake)

    result = await comelec_module.browse_election_results(code=code)

    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert "403" in result["caveats"][0]


# --- 2026-09-04 review, findings 2 and 3: a wrong-typed 'candidates' ---
# --- at either level is drift, not an empty ballot ---


@pytest.mark.asyncio
async def test_outer_candidates_not_a_dict_is_indeterminate_not_cached(monkeypatch):
    payload = {
        "totalErReceived": 1.0,
        "information": ER_FIXTURE["information"],
        "national": [{"contestCode": "00399000", "candidates": 5}],
        "local": [],
    }
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("28010001"): (200, payload),
        },
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert "00399000" in result["caveats"][0]
    assert not CACHES["comelec_return"], "an outer non-dict candidates field must never be cached"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_rows", [["oops"], [5, None], [["nested", "list"]]])
async def test_a_non_object_contest_row_is_indeterminate_not_an_empty_ballot(monkeypatch, bad_rows):
    # Last receipt pass on v0.8.0: non-dict contest rows were skipped, so a
    # ballot made only of them cached as a success with zero contests.
    payload = {
        "totalErReceived": 1.0,
        "information": ER_FIXTURE["information"],
        "national": bad_rows,
        "local": [],
    }
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("28010001"): (200, payload),
        },
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert "not objects" in result["caveats"][0]
    assert not CACHES["comelec_return"], "a malformed contest row must never cache"


@pytest.mark.asyncio
async def test_contest_with_no_candidates_key_still_parses(monkeypatch):
    """A contest missing 'candidates' entirely stays a genuine empty ballot."""
    payload = {
        "totalErReceived": 1.0,
        "information": ER_FIXTURE["information"],
        "national": [{"contestCode": "00399000", "contestName": "SENATOR"}],
        "local": [],
    }
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _er_url("28010001"): (200, payload),
        },
    )

    result = await comelec_module.get_election_return("28010001")

    assert result["data_status"] == "success"
    assert result["national_contests"][0]["candidates"] == []


# --- 2026-09-04 review, finding 4: an explicit empty code is a caller ---
# --- mistake, not a stand-in for the root default ---


@pytest.mark.asyncio
async def test_empty_string_code_is_invalid_request_not_the_root_default(monkeypatch):
    async def _must_not_fetch(client, method, url, **kwargs):
        raise AssertionError("an empty code must never reach the network")

    monkeypatch.setattr(comelec_module, "fetch_with_retry", _must_not_fetch)

    result = await comelec_module.browse_election_results(code="")

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True


@pytest.mark.asyncio
async def test_explicit_none_code_still_defaults_to_root(monkeypatch):
    _route(
        monkeypatch,
        {
            _latest_time_url(): (200, LATEST_TIME),
            _local_url("0"): (200, REGIONS),
        },
    )

    result = await comelec_module.browse_election_results(code=None)

    assert result["data_status"] == "success"
    assert result["code"] == "0"
