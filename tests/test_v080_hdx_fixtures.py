"""Offline tests for search_hdx_datasets: the CKAN package_search shape,
success / empty / upstream-failure / schema-drift coverage, and the two
caller-mistake cases (empty query, rows out of range).

Payload shapes below are trimmed from a real package_search response,
recorded 2026-09-03 in tmp/ulw-20260903/r3-fixtures/hdx.json.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ph_civic_data_mcp.sources import hdx as hdx_module
from ph_civic_data_mcp.utils.cache import CACHES


def _dataset(name: str = "cod-ab-phl", license_id: str = "cc-by-igo") -> dict:
    return {
        "name": name,
        "title": "Philippines - Subnational Administrative Boundaries",
        "organization": {
            "id": "b3a25ac4-ac05-4991-923c-d25f47bef1ec",
            "name": "ocha-fiss",
            "title": "OCHA Field Information Services Section (FISS)",
        },
        "license_id": license_id,
        "license_title": "Creative Commons Attribution for Intergovernmental Organisations (CC BY-IGO)",
        "license_url": "http://creativecommons.org/licenses/by/3.0/igo/legalcode",
        "last_modified": "2026-05-28T12:27:34.210982",
        "num_resources": 1,
        "resources": [
            {
                "name": "phl_admin_boundaries.gdb.zip",
                "format": "Geodatabase",
                "url": "https://data.humdata.org/dataset/caf116df/resource/3fa0dbcf/download/phl_admin_boundaries.gdb.zip",
                "size": 360586879,
                "last_modified": "2026-05-28T12:13:03.332477",
            }
        ],
    }


def _payload(results: list[dict], count: int | None = None) -> dict:
    return {
        "help": "https://data.humdata.org/api/3/action/help_show?name=package_search",
        "success": True,
        "result": {
            "count": count if count is not None else len(results),
            "results": results,
        },
    }


def _install_fake_fetch(monkeypatch, payload: dict | None = None, *, status: int = 200, exc=None):
    async def _fake(client, method, url, **kwargs):
        if exc is not None:
            raise exc
        return httpx.Response(status, json=payload or {}, request=httpx.Request(method, url))

    monkeypatch.setattr(hdx_module, "fetch_with_retry", _fake)


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["hdx_search"].clear()
    yield
    CACHES["hdx_search"].clear()


@pytest.mark.asyncio
async def test_success_returns_datasets_with_license_passed_through(monkeypatch):
    _install_fake_fetch(monkeypatch, _payload([_dataset()], count=481))

    result = await hdx_module.search_hdx_datasets("philippines", rows=10)

    assert result["data_status"] == "success"
    assert result["upstream_error"] is False
    assert result["total_count"] == 481
    assert len(result["datasets"]) == 1
    ds = result["datasets"][0]
    assert ds["name"] == "cod-ab-phl"
    assert ds["license_id"] == "cc-by-igo"
    assert ds["license_title"].startswith("Creative Commons")
    assert ds["organization"] == "OCHA Field Information Services Section (FISS)"
    assert ds["hdx_url"] == "https://data.humdata.org/dataset/cod-ab-phl"
    assert ds["resources"][0]["format"] == "Geodatabase"
    assert "note" in result
    assert len(CACHES["hdx_search"]) == 1


@pytest.mark.asyncio
async def test_a_second_call_with_the_same_args_is_served_from_cache(monkeypatch):
    calls = {"n": 0}

    async def _fake(client, method, url, **kwargs):
        calls["n"] += 1
        return httpx.Response(200, json=_payload([_dataset()]), request=httpx.Request(method, url))

    monkeypatch.setattr(hdx_module, "fetch_with_retry", _fake)

    first = await hdx_module.search_hdx_datasets("philippines")
    again = await hdx_module.search_hdx_datasets("philippines")

    assert again == first
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_zero_datasets_on_a_clean_query_is_empty_not_a_failure(monkeypatch):
    _install_fake_fetch(monkeypatch, _payload([], count=0))

    result = await hdx_module.search_hdx_datasets("no-such-keyword-xyz")

    assert result["data_status"] == "empty"
    assert result["upstream_error"] is False
    assert result["datasets"] == []
    assert len(CACHES["hdx_search"]) == 1, "a genuine empty answer still caches"


@pytest.mark.asyncio
async def test_resources_are_capped_at_twenty(monkeypatch):
    ds = _dataset()
    ds["resources"] = [
        {
            "name": f"r{i}",
            "format": "CSV",
            "url": f"https://example.test/{i}",
            "size": 1,
            "last_modified": None,
        }
        for i in range(30)
    ]
    ds["num_resources"] = 30
    _install_fake_fetch(monkeypatch, _payload([ds]))

    result = await hdx_module.search_hdx_datasets("philippines")

    assert len(result["datasets"][0]["resources"]) == 20


@pytest.mark.asyncio
async def test_transport_failure_is_unavailable_and_never_cached(monkeypatch):
    _install_fake_fetch(monkeypatch, exc=httpx.ConnectError("no route"))

    result = await hdx_module.search_hdx_datasets("philippines")

    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert result["datasets"] == []
    assert "HDX package_search unavailable" in result["caveats"][0]
    assert not CACHES["hdx_search"]


@pytest.mark.asyncio
async def test_a_success_false_body_is_indeterminate_and_never_cached(monkeypatch):
    _install_fake_fetch(monkeypatch, {"success": False, "error": {"message": "not authorized"}})

    result = await hdx_module.search_hdx_datasets("philippines")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert not CACHES["hdx_search"]


@pytest.mark.asyncio
async def test_a_non_list_results_field_is_indeterminate_and_never_cached(monkeypatch):
    _install_fake_fetch(
        monkeypatch, {"success": True, "result": {"count": 5, "results": "not-a-list"}}
    )

    result = await hdx_module.search_hdx_datasets("philippines")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert not CACHES["hdx_search"]


@pytest.mark.asyncio
async def test_a_missing_result_field_is_indeterminate_and_never_cached(monkeypatch):
    _install_fake_fetch(monkeypatch, {"success": True})

    result = await hdx_module.search_hdx_datasets("philippines")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert not CACHES["hdx_search"]


@pytest.mark.asyncio
async def test_a_positive_count_with_empty_results_is_indeterminate_not_a_real_zero(monkeypatch):
    """count says 481 datasets exist; results sent none. Not a real zero,
    the same drift shape world_bank guards with its own `total` check."""
    _install_fake_fetch(monkeypatch, _payload([], count=481))

    result = await hdx_module.search_hdx_datasets("philippines")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert not CACHES["hdx_search"]


@pytest.mark.asyncio
async def test_all_results_failing_to_parse_is_indeterminate_not_cached(monkeypatch):
    """A nonempty results list where every entry has no usable name must not
    read as a real absence of datasets."""
    _install_fake_fetch(monkeypatch, _payload([{"title": "no name field"}]))

    result = await hdx_module.search_hdx_datasets("philippines")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert not CACHES["hdx_search"]


@pytest.mark.asyncio
async def test_a_bad_dataset_entry_is_skipped_not_the_whole_page(monkeypatch):
    good = _dataset("cod-ab-phl")
    bad = {"title": "missing a name"}
    _install_fake_fetch(monkeypatch, _payload([bad, good]))

    result = await hdx_module.search_hdx_datasets("philippines")

    assert result["data_status"] == "success"
    assert len(result["datasets"]) == 1
    assert result["datasets"][0]["name"] == "cod-ab-phl"


@pytest.mark.asyncio
async def test_empty_query_is_a_validation_error_not_a_fetch(monkeypatch):
    async def _must_not_fetch(client, method, url, **kwargs):
        raise AssertionError("fetch must not be attempted for an empty query")

    monkeypatch.setattr(hdx_module, "fetch_with_retry", _must_not_fetch)

    result = await hdx_module.search_hdx_datasets("   ")

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert not CACHES["hdx_search"]


@pytest.mark.asyncio
async def test_a_query_over_two_hundred_characters_is_a_validation_error(monkeypatch):
    async def _must_not_fetch(client, method, url, **kwargs):
        raise AssertionError("fetch must not be attempted for an over-length query")

    monkeypatch.setattr(hdx_module, "fetch_with_retry", _must_not_fetch)

    result = await hdx_module.search_hdx_datasets("x" * 201)

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True


@pytest.mark.asyncio
async def test_a_query_with_a_control_character_is_a_validation_error(monkeypatch):
    async def _must_not_fetch(client, method, url, **kwargs):
        raise AssertionError("fetch must not be attempted for a control character")

    monkeypatch.setattr(hdx_module, "fetch_with_retry", _must_not_fetch)

    result = await hdx_module.search_hdx_datasets("flood\x00relief")

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [0, -1, 51, 1000])
async def test_rows_out_of_range_is_a_validation_error_not_a_fetch(monkeypatch, rows):
    """rows outside 1 to 50 is rejected, never silently clamped."""

    async def _must_not_fetch(client, method, url, **kwargs):
        raise AssertionError("fetch must not be attempted for an out-of-range rows")

    monkeypatch.setattr(hdx_module, "fetch_with_retry", _must_not_fetch)

    result = await hdx_module.search_hdx_datasets("philippines", rows=rows)

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False


@pytest.mark.asyncio
async def test_a_non_integer_rows_is_a_validation_error(monkeypatch):
    async def _must_not_fetch(client, method, url, **kwargs):
        raise AssertionError("fetch must not be attempted for a non-integer rows")

    monkeypatch.setattr(hdx_module, "fetch_with_retry", _must_not_fetch)

    result = await hdx_module.search_hdx_datasets("philippines", rows="ten")

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True


@pytest.mark.asyncio
async def test_query_is_passed_as_a_param_never_concatenated_into_the_url(monkeypatch):
    seen = {}

    async def _fake(client, method, url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs.get("params")
        return httpx.Response(200, json=_payload([_dataset()]), request=httpx.Request(method, url))

    monkeypatch.setattr(hdx_module, "fetch_with_retry", _fake)

    await hdx_module.search_hdx_datasets("../../etc/passwd", rows=5)

    assert seen["url"] == hdx_module.HDX_URL
    assert "../../etc/passwd" not in seen["url"]
    assert seen["params"]["q"] == "../../etc/passwd"
    assert seen["params"]["fq"] == "groups:phl"
    assert seen["params"]["rows"] == 5
    assert seen["params"]["sort"] == "metadata_modified desc"


@pytest.mark.asyncio
async def test_results_with_a_string_count_return_the_datasets_with_a_caveat_and_no_cache(
    monkeypatch,
):
    # Codex pass on v0.8.0: a bad count was only treated as drift beside
    # empty results. A non-empty results list with count "12" is drift too.
    payload = _payload([_dataset()], count=None)
    payload["result"]["count"] = "12"
    _install_fake_fetch(monkeypatch, payload)

    result = await hdx_module.search_hdx_datasets("philippines")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert len(result["datasets"]) == 1
    assert result["datasets"][0]["name"] == "cod-ab-phl"
    assert result["total_count"] == 1
    assert "12" in result["caveats"][0]
    assert not CACHES["hdx_search"]


@pytest.mark.asyncio
async def test_results_with_a_real_integer_count_still_cache(monkeypatch):
    _install_fake_fetch(monkeypatch, _payload([_dataset()], count=1))

    result = await hdx_module.search_hdx_datasets("philippines")

    assert result["data_status"] == "success"
    assert result["upstream_error"] is False
    assert result["total_count"] == 1
    assert len(CACHES["hdx_search"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("count_value", [None, "12", True, 3.0])
async def test_empty_results_without_an_integer_count_are_indeterminate(monkeypatch, count_value):
    # Codex pass on v0.8.0: CKAN always sends an integer count, so empty
    # results beside a missing or wrong-typed count is drift, not a real zero.
    payload = {"success": True, "result": {"results": []}}
    if count_value is not None:
        payload["result"]["count"] = count_value
    _install_fake_fetch(monkeypatch, payload)

    result = await hdx_module.search_hdx_datasets("flood")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert len(CACHES["hdx_search"]) == 0


@pytest.mark.asyncio
async def test_twenty_five_concurrent_cold_calls_fetch_once(monkeypatch):
    calls = {"n": 0}

    async def _fake(client, method, url, **kwargs):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, json=_payload([_dataset()]), request=httpx.Request(method, url))

    monkeypatch.setattr(hdx_module, "fetch_with_retry", _fake)

    results = await asyncio.gather(*(hdx_module.search_hdx_datasets("same") for _ in range(25)))

    assert calls["n"] == 1
    assert all(r["data_status"] == "success" for r in results)
    assert len(CACHES["hdx_search"]) == 1
