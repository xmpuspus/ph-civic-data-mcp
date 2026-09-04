"""Offline tests for search_psic_codes (v0.8.0).

No live HTTP. Covers code-prefix and description whole-word matching, the
limit cut, a genuine zero-match empty result, the two drift shapes
(Cloudflare challenge, missing table), a transport failure, the two
invalid_request cases, and the single-flight fetch.

The real 20 KB probe slice of the PSIC search-results page (see
tmp/ulw-20260903/r3-fixtures/psa_psic_table.html) is a page-head-and-nav
slice cut before the psicdata table starts. It carries no <table
id="psicdata"> at all, so it is the real-world instance of the "missing
table" drift case, not a source of match rows. _NO_TABLE_HTML below mirrors
that same shape (a full page, no psicdata table) without committing the
20 KB file to the test suite.

A live probe on 2026-09-04 found that the code cell is never a bare code.
Every one of the 1360 rows on the real page reads "Subclass 01111", with
the level word spelled out ahead of the code. _ROWS mirrors that shape.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from ph_civic_data_mcp.sources import psic as psic_module
from ph_civic_data_mcp.utils.cache import CACHES
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_EMPTY,
    DATA_STATUS_INDETERMINATE,
    DATA_STATUS_INVALID_REQUEST,
    DATA_STATUS_SUCCESS,
    DATA_STATUS_UNAVAILABLE,
)

SOURCE_URL = psic_module.SOURCE_URL


def _row(description_html: str, code: str) -> str:
    return f"<tr><td>{description_html}</td><td>{code}</td></tr>"


def _table_page(rows: list[str]) -> str:
    return (
        "<html><body>"
        '<table id="psicdata" class="table table-striped table-bordered" data-striping="1">'
        "<thead><tr><th>PSIC/Description</th><th>Code</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table></body></html>"
    )


# Rows 5 and 6 are the two real rows the live probe log names (probed
# 2026-09-04): subclass 01111 "Growing of leguminous crops..." and subclass
# 01112 "Growing of groundnuts", both linked to their parent class page
# /classification/psic/class/0111 on the real site, never a /subclass/ page.
# The last row deliberately drops both the level word and the anchor, to
# exercise the code-length fallback a real row never needs.
_ROWS = [
    _row(
        '<a href="/classification/psic/section/A" class="psic">Agriculture, Forestry and Fishing</a>',
        "Section A",
    ),
    _row(
        '<a href="/classification/psic/division/01" class="psic">Crop and Animal Production</a>',
        "Division 01",
    ),
    _row(
        '<a href="/classification/psic/group/011" class="psic">Growing of Non-Perennial Crops</a>',
        "Group 011",
    ),
    _row(
        '<a href="/classification/psic/class/0111" class="psic">Growing of Cereals</a>',
        "Class 0111",
    ),
    _row(
        '<a href="/classification/psic/class/0111" class="psic">Growing of leguminous crops such '
        "as: mongo, string beans (sitao)</a>",
        "Subclass 01111",
    ),
    _row(
        '<a href="/classification/psic/class/0111" class="psic">Growing of groundnuts</a>',
        "Subclass 01112",
    ),
    _row("Growing of rice", "01113"),  # no level word, no anchor: fallback to code length
]

_TABLE_HTML = _table_page(_ROWS)

_CHALLENGE_HTML = (
    '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title></head>'
    "<body>Checking your browser before accessing psa.gov.ph.</body></html>"
)

_NO_TABLE_HTML = (
    "<!DOCTYPE html><html><head><title>Search PSIC database | Philippine Statistics "
    'Authority</title></head><body><div class="gva-body-wrapper">the page rendered but '
    "carries no psicdata table, the same shape as the real 20 KB probe slice</div>"
    "</body></html>"
)

_EMPTY_BODY_TABLE_HTML = (
    '<html><body><table id="psicdata"><thead><tr><th>PSIC/Description</th>'
    "<th>Code</th></tr></thead><tbody></tbody></table></body></html>"
)


def _page(html: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=html, request=httpx.Request("GET", SOURCE_URL))


@pytest.fixture(autouse=True)
def _clean():
    CACHES["psic_table"].clear()
    yield
    CACHES["psic_table"].clear()


def _install(monkeypatch, html: str, status: int = 200, seen: list[str] | None = None):
    async def _fake(client, method, url, **kwargs):
        if seen is not None:
            seen.append(url)
        return _page(html, status)

    monkeypatch.setattr(psic_module, "fetch_with_retry", _fake)


@pytest.mark.asyncio
async def test_description_token_match(monkeypatch):
    _install(monkeypatch, _TABLE_HTML)
    out = await psic_module.search_psic_codes("groundnuts")
    assert out["match_count"] == 1
    assert out["matches"][0] == {
        "code": "01112",
        "level": "subclass",
        "description": "Growing of groundnuts",
    }
    assert out["data_status"] == DATA_STATUS_SUCCESS
    assert out["source"] == psic_module.SOURCE_NAME
    assert out["total_codes"] == len(_ROWS)


@pytest.mark.asyncio
async def test_description_match_is_whole_word_not_a_substring(monkeypatch):
    _install(monkeypatch, _TABLE_HTML)
    out = await psic_module.search_psic_codes("crop")
    # "crop" is a whole word in "Crop and Animal Production" (code 01) but only
    # a fragment of "Crops" (code 011). A fragment match would wrongly pull in 011.
    codes = {m["code"] for m in out["matches"]}
    assert codes == {"01"}


@pytest.mark.asyncio
async def test_code_prefix_match(monkeypatch):
    _install(monkeypatch, _TABLE_HTML)
    out = await psic_module.search_psic_codes("0111")
    codes = {m["code"] for m in out["matches"]}
    assert codes == {"0111", "01111", "01112", "01113"}
    assert out["data_status"] == DATA_STATUS_SUCCESS


@pytest.mark.asyncio
async def test_level_falls_back_to_code_length_when_href_is_missing(monkeypatch):
    _install(monkeypatch, _TABLE_HTML)
    out = await psic_module.search_psic_codes("rice")
    assert out["matches"][0]["code"] == "01113"
    assert out["matches"][0]["level"] == "subclass"


@pytest.mark.asyncio
async def test_level_word_in_the_code_cell_wins_over_the_href(monkeypatch):
    """A real subclass row links to its parent class page, so the href alone
    would read "class" for a 5-digit subclass code. The level word PSA
    prints ahead of the code in the cell itself is read first and wins."""
    _install(monkeypatch, _TABLE_HTML)
    out = await psic_module.search_psic_codes("leguminous")
    assert out["matches"][0]["code"] == "01111"
    assert out["matches"][0]["level"] == "subclass"


@pytest.mark.asyncio
async def test_href_beats_code_length_when_the_level_word_is_absent(monkeypatch):
    """Defensive fallback: a row with no level word still reads level from
    the href segment before guessing from the bare code's length."""
    rows = [
        _row(
            '<a href="/classification/psic/class/0111" class="psic">Growing of vegetables</a>',
            "01199",
        )
    ]
    _install(monkeypatch, _table_page(rows))
    out = await psic_module.search_psic_codes("vegetables")
    assert out["matches"][0]["code"] == "01199"
    assert out["matches"][0]["level"] == "class"


@pytest.mark.asyncio
async def test_limit_cuts_and_sets_truncated(monkeypatch):
    _install(monkeypatch, _TABLE_HTML)
    out = await psic_module.search_psic_codes("0111", limit=2)
    assert out["match_count"] == 2
    assert out["truncated"] is True
    assert any("Raise limit" in c for c in out["caveats"])


@pytest.mark.asyncio
async def test_zero_matches_on_a_parsed_table_is_empty_and_cacheable(monkeypatch):
    _install(monkeypatch, _TABLE_HTML)
    out = await psic_module.search_psic_codes("zzz-no-such-industry")
    assert out["match_count"] == 0
    assert out["matches"] == []
    assert out["data_status"] == DATA_STATUS_EMPTY
    assert out["upstream_error"] is False
    assert out["validation_error"] is False
    assert len(CACHES["psic_table"]) == 1, (
        "a genuine zero-match search still caches the parsed table"
    )


@pytest.mark.asyncio
async def test_cloudflare_challenge_is_unavailable(monkeypatch):
    _install(monkeypatch, _CHALLENGE_HTML)
    out = await psic_module.search_psic_codes("rice")
    assert out["data_status"] == DATA_STATUS_UNAVAILABLE
    assert out["upstream_error"] is True
    assert out["matches"] == []
    assert any("Cloudflare" in c for c in out["caveats"])
    assert len(CACHES["psic_table"]) == 0, "a challenge page must never be cached"


@pytest.mark.asyncio
async def test_page_with_no_table_is_indeterminate(monkeypatch):
    _install(monkeypatch, _NO_TABLE_HTML)
    out = await psic_module.search_psic_codes("rice")
    assert out["data_status"] == DATA_STATUS_INDETERMINATE
    assert out["upstream_error"] is True
    assert len(CACHES["psic_table"]) == 0


@pytest.mark.asyncio
async def test_table_with_zero_body_rows_is_indeterminate(monkeypatch):
    _install(monkeypatch, _EMPTY_BODY_TABLE_HTML)
    out = await psic_module.search_psic_codes("rice")
    assert out["data_status"] == DATA_STATUS_INDETERMINATE
    assert len(CACHES["psic_table"]) == 0


@pytest.mark.asyncio
async def test_transport_failure_is_unavailable_and_never_cached(monkeypatch):
    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("psa.gov.ph down")

    monkeypatch.setattr(psic_module, "fetch_with_retry", _boom)
    out = await psic_module.search_psic_codes("rice")
    assert out["data_status"] == DATA_STATUS_UNAVAILABLE
    assert out["upstream_error"] is True
    assert any("ConnectError" in c for c in out["caveats"])
    assert len(CACHES["psic_table"]) == 0


@pytest.mark.asyncio
async def test_whitespace_query_is_invalid_request_before_any_fetch(monkeypatch):
    seen: list[str] = []
    _install(monkeypatch, _TABLE_HTML, seen=seen)
    out = await psic_module.search_psic_codes("   ")
    assert out["data_status"] == DATA_STATUS_INVALID_REQUEST
    assert out["validation_error"] is True
    assert out["matches"] == []
    assert seen == [], "validation must happen before any network call"


@pytest.mark.asyncio
async def test_limit_out_of_range_is_invalid_request(monkeypatch):
    seen: list[str] = []
    _install(monkeypatch, _TABLE_HTML, seen=seen)
    out = await psic_module.search_psic_codes("rice", limit=0)
    assert out["data_status"] == DATA_STATUS_INVALID_REQUEST
    assert out["validation_error"] is True

    out = await psic_module.search_psic_codes("rice", limit=101)
    assert out["data_status"] == DATA_STATUS_INVALID_REQUEST
    assert out["validation_error"] is True
    assert seen == [], "validation must happen before any network call"


@pytest.mark.asyncio
async def test_twenty_concurrent_cold_calls_fetch_the_page_once(monkeypatch):
    calls = 0

    async def _fake(client, method, url, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)  # yield so the other 19 callers queue behind the lock
        return _page(_TABLE_HTML)

    monkeypatch.setattr(psic_module, "fetch_with_retry", _fake)
    results = await asyncio.gather(*(psic_module.search_psic_codes("rice") for _ in range(20)))
    assert calls == 1
    assert all(r["data_status"] in (DATA_STATUS_SUCCESS, DATA_STATUS_EMPTY) for r in results)


@pytest.mark.asyncio
async def test_every_description_carries_the_current_data_status(monkeypatch):
    """Sweep every case above against the closed set, the way the neighbour tests do."""
    from ph_civic_data_mcp.utils.envelope import DATA_STATUS_VALUES

    _install(monkeypatch, _TABLE_HTML)
    out = await psic_module.search_psic_codes("rice")
    assert out["data_status"] in DATA_STATUS_VALUES


@pytest.mark.asyncio
async def test_tool_description_carries_examples_and_on_failure_not_args():
    """FastMCP cuts the client-facing description at the first standalone
    Google-style header, so Args: must never reach an agent."""
    from ph_civic_data_mcp.server import mcp

    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == "search_psic_codes")
    assert "Examples:" in tool.description
    assert "On failure" in tool.description
    assert "Args:" not in tool.description
    assert tool.annotations.open_world_hint is True
