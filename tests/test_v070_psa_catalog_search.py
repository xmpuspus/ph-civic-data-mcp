"""Offline tests for search_psa_catalog (v0.7.0).

No live HTTP. Covers the recursive catalog walk, the flattened index cache,
keyword matching by title and path, limit enforcement, and the failure
envelope on an upstream outage.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import psa as psa_module
from ph_civic_data_mcp.sources import psa_catalog as cat
from ph_civic_data_mcp.utils.cache import CACHES
from ph_civic_data_mcp.utils.envelope import DATA_STATUS_INVALID_REQUEST, DATA_STATUS_UNAVAILABLE

# A small two-subject tree: one dataset directly under a subject, one
# dataset one folder deeper. Mirrors the real shape (folder depth varies).
ROOT = [
    {"id": "1A", "type": "l", "text": "Population and Vital Statistics"},
    {"id": "1F", "type": "l", "text": "Poverty"},
]

SUBJECT_1A = [
    {"id": "0011A.px", "type": "t", "text": "Total Population by Region"},
]

SUBJECT_1F = [
    {"id": "FY", "type": "l", "text": "Full Year Poverty Statistics"},
]

FY_LEAVES = [
    {"id": "0011F.px", "type": "t", "text": "Poverty Incidence Among Families"},
]


def _resp(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


@pytest.fixture(autouse=True)
def _clean():
    CACHES["psa_browse"].clear()
    CACHES["psa_catalog_index"].clear()
    yield
    CACHES["psa_browse"].clear()
    CACHES["psa_catalog_index"].clear()


def _install_tree(monkeypatch, seen: list[str] | None = None):
    async def _fake(client, method, url, **kwargs):
        if seen is not None:
            seen.append(f"{method} {url}")
        if url.endswith("/DB/"):
            return _resp(method, url, ROOT)
        if url.endswith("/DB/1A/"):
            return _resp(method, url, SUBJECT_1A)
        if url.endswith("/DB/1F/"):
            return _resp(method, url, SUBJECT_1F)
        if url.endswith("/DB/1F/FY/"):
            return _resp(method, url, FY_LEAVES)
        return httpx.Response(404, text="nope", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)


@pytest.mark.asyncio
async def test_search_finds_a_match_by_title(monkeypatch):
    _install_tree(monkeypatch)
    out = await cat.search_psa_catalog("poverty")
    assert out["match_count"] == 1
    assert out["matches"][0] == {
        "path": "1F/FY/0011F.px",
        "title": "Poverty Incidence Among Families",
    }
    assert not out.get("upstream_error")
    assert not out.get("validation_error")
    assert out["data_status"] == "success"
    assert out["source"] == cat.SOURCE_NAME


@pytest.mark.asyncio
async def test_search_matches_case_insensitively_on_path(monkeypatch):
    _install_tree(monkeypatch)
    out = await cat.search_psa_catalog("0011A")
    assert out["match_count"] == 1
    assert out["matches"][0]["path"] == "1A/0011A.px"


@pytest.mark.asyncio
async def test_search_returns_no_hits_gracefully(monkeypatch):
    _install_tree(monkeypatch)
    out = await cat.search_psa_catalog("zzz_not_a_table")
    assert out["match_count"] == 0
    assert out["total_available"] == 0
    assert out["matches"] == []
    assert not out.get("upstream_error")
    assert not out.get("validation_error")


@pytest.mark.asyncio
async def test_search_enforces_the_requested_limit(monkeypatch):
    _install_tree(monkeypatch)
    out = await cat.search_psa_catalog("0011", limit=1)
    assert out["match_count"] == 1
    assert out["total_available"] == 2
    assert out["limit"] == 1
    assert any("Raise limit" in c for c in out["caveats"])


@pytest.mark.asyncio
async def test_search_caps_the_limit_at_100(monkeypatch):
    _install_tree(monkeypatch)
    out = await cat.search_psa_catalog("0011", limit=100000)
    assert out["limit"] == cat.MAX_SEARCH_LIMIT


@pytest.mark.asyncio
async def test_search_rejects_an_empty_keyword(monkeypatch):
    seen: list[str] = []
    _install_tree(monkeypatch, seen)
    out = await cat.search_psa_catalog("   ")
    assert out["validation_error"] is True
    assert out["data_status"] == DATA_STATUS_INVALID_REQUEST
    assert out["matches"] == []
    assert seen == [], "validation must happen before any network call"


@pytest.mark.asyncio
async def test_search_rejects_a_non_integer_limit(monkeypatch):
    _install_tree(monkeypatch)
    out = await cat.search_psa_catalog("poverty", limit="20")  # type: ignore[arg-type]
    assert out["validation_error"] is True


@pytest.mark.asyncio
async def test_search_walks_the_catalog_once_then_answers_from_cache(monkeypatch):
    seen: list[str] = []
    _install_tree(monkeypatch, seen)
    await cat.search_psa_catalog("poverty")
    calls_after_first = len(seen)
    assert calls_after_first > 0

    await cat.search_psa_catalog("population")
    assert len(seen) == calls_after_first, "a second search must not re-walk the catalog"


@pytest.mark.asyncio
async def test_search_returns_upstream_error_on_a_catalog_outage(monkeypatch):
    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("openstat down")

    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    out = await cat.search_psa_catalog("poverty")
    assert out["upstream_error"] is True
    assert out["data_status"] == DATA_STATUS_UNAVAILABLE
    assert out["matches"] == []
    assert any("ConnectError" in c for c in out["caveats"])
    assert len(CACHES["psa_catalog_index"]) == 0, "a failed walk must never be cached"
