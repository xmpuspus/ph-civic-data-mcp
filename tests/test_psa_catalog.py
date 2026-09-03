"""Offline tests for the v0.6.0 PSA OpenSTAT catalog tools.

No live HTTP. Covers path validation, hierarchy browsing, metadata mapping,
selection validation, the cell ceiling, row normalization, missing values,
truncation, envelopes, caching, and the registered MCP surface.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import psa as psa_module
from ph_civic_data_mcp.sources import psa_catalog as cat
from ph_civic_data_mcp.utils.cache import CACHES
from ph_civic_data_mcp.utils.envelope import DATA_STATUS_INDETERMINATE

ROOT = [
    {"id": "1A", "type": "l", "text": "Population and Vital Statistics"},
    {"id": "1F", "type": "l", "text": "Poverty"},
]

SUBJECT = [{"id": "FY", "type": "l", "text": "Full Year Poverty Statistics"}]

LEAVES = [
    {
        "id": "0241F3DF013.px",
        "type": "t",
        "text": "Table 13. Poverty Incidence among Families and Population",
    }
]

META = {
    "title": "Table 13. Poverty Incidence among Families and Population",
    "variables": [
        {
            "code": "Major Island Group",
            "text": "Major Island Group",
            "values": ["0", "1", "2"],
            "valueTexts": ["PHILIPPINES", "..Luzon", "..Mindanao"],
        },
        {
            "code": "Among Families/Population",
            "text": "Among Families/Population",
            "values": ["0", "1"],
            "valueTexts": [
                "Poverty Incidence among Families (%)",
                "Poverty Incidence among Population (%)",
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

DATA = {
    "columns": [
        {"code": "Major Island Group", "type": "d"},
        {"code": "Among Families/Population", "type": "d"},
        {"code": "Year", "type": "d"},
        {"code": "Poverty Incidence", "type": "c"},
    ],
    "data": [
        {"key": ["0", "0", "2"], "values": ["10.9"]},
        {"key": ["2", "0", "2"], "values": [".."]},
    ],
}

DATASET = "1F/FY/0241F3DF013.px"


def _resp(method: str, url: str, payload: object) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request(method, url))


@pytest.fixture(autouse=True)
def _clean():
    CACHES["psa_browse"].clear()
    psa_module._DISCOVERY_CACHE.clear()
    yield
    CACHES["psa_browse"].clear()
    psa_module._DISCOVERY_CACHE.clear()


def _install(monkeypatch, seen: list[str] | None = None):
    async def _fake(client, method, url, **kwargs):
        if seen is not None:
            seen.append(f"{method} {url}")
        if method == "POST":
            return _resp(method, url, DATA)
        if url.endswith("/DB/"):
            return _resp(method, url, ROOT)
        if url.endswith("/DB/1F/"):
            return _resp(method, url, SUBJECT)
        if url.endswith("/DB/1F/FY/"):
            return _resp(method, url, LEAVES)
        if url.endswith("0241F3DF013.px"):
            return _resp(method, url, META)
        return httpx.Response(404, text="nope", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "https://evil.example/x",
        "//evil.example/x",
        "1F/../../etc/passwd",
        "1F/FY/..",
        "1F//FY",
        "1F/FY?token=1",
        "1F/FY#frag",
        "1F\\FY",
        "1F/FY%2e%2e",
        "1F/F Y",
        "file:///etc/passwd",
        "1F/FY\x00",
        "a/b/c/d/e/f/g/h/i/j",
        "-leading-dash",
    ],
)
def test_normalize_path_rejects_unsafe_input(bad):
    with pytest.raises(cat.CatalogPathError):
        cat._normalize_path(bad)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, ""),
        ("", ""),
        ("   ", ""),
        ("1F", "1F"),
        ("/1F/FY/", "1F/FY"),
        ("DB/1F/FY", "1F/FY"),
        ("db/1F/FY", "1F/FY"),
        ("2A/PPA/2025", "2A/PPA/2025"),
        ("1F/FY/0241F3DF013.px", "1F/FY/0241F3DF013.px"),
    ],
)
def test_normalize_path_accepts_relative_paths(raw, expected):
    assert cat._normalize_path(raw) == expected


def test_every_built_url_stays_under_the_official_base():
    for path in ("", "1F", "1F/FY"):
        assert cat._catalog_url(path).startswith(cat.CATALOG_ROOT_URL)
    assert cat._dataset_url(DATASET).startswith(cat.CATALOG_ROOT_URL)


def test_dataset_path_requires_a_px_leaf():
    with pytest.raises(cat.CatalogPathError):
        cat._dataset_path("1F/FY")
    with pytest.raises(cat.CatalogPathError):
        cat._dataset_path("")
    assert cat._dataset_path(DATASET) == DATASET


# ---------------------------------------------------------------------------
# browse_psa_catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_root_lists_subjects(monkeypatch):
    _install(monkeypatch)
    out = await cat.browse_psa_catalog()
    assert out["path"] == ""
    assert out["parent_path"] is None
    assert out["folder_count"] == 2
    assert out["dataset_count"] == 0
    assert {e["id"] for e in out["entries"]} == {"1A", "1F"}
    assert all(e["type"] == "folder" for e in out["entries"])
    assert out["source"] == "PSA OpenSTAT"
    assert out["source_url"] == cat.CATALOG_ROOT_URL
    assert out["data_retrieved_at"]
    assert not out.get("upstream_error")


@pytest.mark.asyncio
async def test_browse_walks_down_and_marks_datasets(monkeypatch):
    _install(monkeypatch)
    subject = await cat.browse_psa_catalog("1F")
    assert subject["entries"][0]["path"] == "1F/FY"
    assert subject["parent_path"] == ""

    leaves = await cat.browse_psa_catalog("1F/FY")
    assert leaves["dataset_count"] == 1
    assert leaves["entries"][0]["type"] == "dataset"
    assert leaves["entries"][0]["path"] == DATASET
    assert leaves["parent_path"] == "1F"


@pytest.mark.asyncio
async def test_browse_rejects_an_absolute_url_before_any_request(monkeypatch):
    seen: list[str] = []
    _install(monkeypatch, seen)
    out = await cat.browse_psa_catalog("https://evil.example/steal")
    assert out["validation_error"] is True
    assert out["entries"] == []
    assert not out.get("upstream_error")
    assert seen == [], "validation must happen before any network call"


@pytest.mark.asyncio
async def test_browse_returns_upstream_error_on_outage(monkeypatch):
    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("openstat down")

    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)
    out = await cat.browse_psa_catalog("1F")
    assert out["upstream_error"] is True
    assert out["entries"] == []
    assert any("ConnectError" in c for c in out["caveats"])
    assert len(CACHES["psa_browse"]) == 0


@pytest.mark.asyncio
async def test_browse_surfaces_a_429_as_upstream_error(monkeypatch):
    async def _throttled(client, method, url, **kwargs):
        return httpx.Response(429, text="slow down", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _throttled)
    out = await cat.browse_psa_catalog("1F")
    assert out["upstream_error"] is True
    assert any("429" in c for c in out["caveats"])
    assert len(CACHES["psa_browse"]) == 0


@pytest.mark.asyncio
async def test_browse_caches_a_success(monkeypatch):
    seen: list[str] = []
    _install(monkeypatch, seen)
    await cat.browse_psa_catalog("1F")
    calls = len(seen)
    await cat.browse_psa_catalog("1F")
    assert len(seen) == calls, "a cached browse must not re-request"


# ---------------------------------------------------------------------------
# describe_psa_dataset
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_describe_maps_dimensions_and_values(monkeypatch):
    _install(monkeypatch)
    out = await cat.describe_psa_dataset(DATASET)
    assert out["dataset_path"] == DATASET
    assert out["title"].startswith("Table 13.")
    codes = [d["code"] for d in out["dimensions"]]
    assert codes == ["Major Island Group", "Among Families/Population", "Year"]
    year = out["dimensions"][2]
    assert year["value_count"] == 3
    assert year["values"][2] == {"code": "2", "label": "2023"}
    assert year["is_time_like"] is True
    assert out["time_dimensions"] == ["Year"]
    assert out["total_cells"] == 18
    assert out["max_cells_per_query"] == cat.MAX_CELLS
    assert out["source_url"].endswith(DATASET)


@pytest.mark.asyncio
async def test_describe_rejects_a_folder_path(monkeypatch):
    seen: list[str] = []
    _install(monkeypatch, seen)
    out = await cat.describe_psa_dataset("1F/FY")
    assert out["validation_error"] is True
    assert out["dimensions"] == []
    assert seen == []


@pytest.mark.asyncio
async def test_describe_returns_upstream_error_on_outage(monkeypatch):
    async def _boom(client, method, url, **kwargs):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)
    out = await cat.describe_psa_dataset(DATASET)
    assert out["upstream_error"] is True
    assert out["dimensions"] == []
    assert len(CACHES["psa_browse"]) == 0


@pytest.mark.asyncio
async def test_describe_caches_metadata(monkeypatch):
    seen: list[str] = []
    _install(monkeypatch, seen)
    await cat.describe_psa_dataset(DATASET)
    calls = len(seen)
    await cat.describe_psa_dataset(DATASET)
    assert len(seen) == calls


# ---------------------------------------------------------------------------
# query_psa_dataset: selection validation
# ---------------------------------------------------------------------------


FULL = {
    "Major Island Group": ["0", "2"],
    "Among Families/Population": ["0"],
    "Year": ["2"],
}


@pytest.mark.asyncio
async def test_query_returns_normalized_rows(monkeypatch):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, FULL)
    assert not out.get("upstream_error")
    assert not out.get("validation_error")
    assert out["requested_cells"] == 2
    assert out["row_count"] == 2
    assert out["rows"][0]["keys"] == {
        "Major Island Group": "0",
        "Among Families/Population": "0",
        "Year": "2",
    }
    assert out["rows"][0]["labels"]["Major Island Group"] == "PHILIPPINES"
    assert out["rows"][0]["labels"]["Year"] == "2023"
    assert out["rows"][0]["value"] == pytest.approx(10.9)
    assert out["reference_period"] == "2023"
    assert out["disclaimer"]
    assert out["source_url"].endswith(DATASET)


@pytest.mark.asyncio
async def test_query_turns_the_psa_missing_sentinel_into_null(monkeypatch):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, FULL)
    assert out["rows"][1]["value"] is None, "'..' must be null, never 0"
    assert any("missing value" in c for c in out["caveats"])


@pytest.mark.asyncio
async def test_query_posts_only_explicit_item_filters(monkeypatch):
    captured: dict = {}

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            captured.update(kwargs.get("json") or {})
            return _resp(method, url, DATA)
        if url.endswith("0241F3DF013.px"):
            return _resp(method, url, META)
        return httpx.Response(404, text="nope", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    await cat.query_psa_dataset(DATASET, FULL)
    assert captured["response"] == {"format": "json"}
    assert len(captured["query"]) == 3
    for clause in captured["query"]:
        assert clause["selection"]["filter"] == "item"
        assert clause["selection"]["values"]


@pytest.mark.asyncio
async def test_query_rejects_an_unknown_dimension(monkeypatch):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, {**FULL, "Nope": ["0"]})
    assert out["validation_error"] is True
    assert "Nope" in out["caveats"][0]
    assert out["rows"] == []


@pytest.mark.asyncio
async def test_query_rejects_an_unknown_value_code(monkeypatch):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, {**FULL, "Year": ["99"]})
    assert out["validation_error"] is True
    assert "99" in out["caveats"][0]


@pytest.mark.asyncio
async def test_query_rejects_a_missing_dimension(monkeypatch):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, {"Year": ["2"]})
    assert out["validation_error"] is True
    assert "Major Island Group" in out["caveats"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("wildcard", ["all", "*", "ALL"])
async def test_query_rejects_wildcard_selections(monkeypatch, wildcard):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, {**FULL, "Year": [wildcard]})
    assert out["validation_error"] is True
    assert "full-cube" in out["caveats"][0]


@pytest.mark.asyncio
async def test_query_rejects_an_empty_selection_list(monkeypatch):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, {**FULL, "Year": []})
    assert out["validation_error"] is True


@pytest.mark.asyncio
async def test_query_rejects_non_dict_selections(monkeypatch):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, [])  # type: ignore[arg-type]
    assert out["validation_error"] is True


@pytest.mark.asyncio
async def test_query_accepts_a_bare_string_value(monkeypatch):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(
        DATASET,
        {"Major Island Group": "0", "Among Families/Population": "0", "Year": "2"},
    )
    assert not out.get("validation_error")
    assert out["requested_cells"] == 1


@pytest.mark.asyncio
async def test_query_enforces_the_cell_ceiling(monkeypatch):
    big_meta = {
        "title": "Big table",
        "variables": [
            {
                "code": "Area",
                "text": "Area",
                "values": [str(i) for i in range(200)],
                "valueTexts": [f"Area {i}" for i in range(200)],
            },
            {
                "code": "Year",
                "text": "Year",
                "values": [str(i) for i in range(20)],
                "valueTexts": [str(2000 + i) for i in range(20)],
            },
        ],
    }
    posted = {"n": 0}

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            posted["n"] += 1
            return _resp(method, url, DATA)
        return _resp(method, url, big_meta)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)

    out = await cat.query_psa_dataset(
        "1F/FY/big.px",
        {"Area": [str(i) for i in range(200)], "Year": [str(i) for i in range(20)]},
    )
    assert out["validation_error"] is True
    assert "4000 cells" in out["caveats"][0]
    assert posted["n"] == 0, "the ceiling must stop the POST, not trim it after"


@pytest.mark.asyncio
async def test_query_clamps_max_rows_and_reports_truncation(monkeypatch):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, FULL, max_rows=1)
    assert out["row_count"] == 1
    assert out["total_rows_available"] == 2
    assert out["truncated"] is True
    assert any("Returned 1 of 2 rows" in c for c in out["caveats"])

    zero = await cat.query_psa_dataset(DATASET, FULL, max_rows=0)
    assert zero["row_count"] == 1, "max_rows clamps up to the floor, never to 0"

    huge = await cat.query_psa_dataset(DATASET, FULL, max_rows=10**9)
    assert huge["truncated"] is False


@pytest.mark.asyncio
async def test_query_rejects_a_non_integer_max_rows(monkeypatch):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, FULL, max_rows="lots")  # type: ignore[arg-type]
    assert out["validation_error"] is True


@pytest.mark.asyncio
async def test_query_returns_upstream_error_when_the_post_fails(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return httpx.Response(403, text="WAF", request=httpx.Request(method, url))
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(DATASET, FULL)
    assert out["upstream_error"] is True
    assert out["rows"] == []
    assert any("403" in c for c in out["caveats"])


# ---------------------------------------------------------------------------
# The registered MCP surface
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_tools_are_registered_with_metadata():
    from ph_civic_data_mcp.server import mcp

    tools = {t.name: t for t in await mcp.list_tools()}
    for name in ("browse_psa_catalog", "describe_psa_dataset", "query_psa_dataset"):
        tool = tools[name]
        assert tool.title, f"{name} has no title"
        assert tool.tags, f"{name} has no tags"
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.open_world_hint is True
        schema = tool.output_schema or {}
        assert schema.get("type") == "object"
        assert "upstream_error" in schema.get("properties", {})
        assert "validation_error" in schema.get("properties", {})


@pytest.mark.asyncio
async def test_failure_envelopes_pass_the_declared_output_schema(monkeypatch):
    """A valid failure response must survive a call through the MCP layer."""
    from fastmcp import Client

    from ph_civic_data_mcp.server import mcp

    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("openstat down")

    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)

    async with Client(mcp) as client:
        outage = await client.call_tool("browse_psa_catalog", {"path": "1F"})
        assert outage.structured_content["upstream_error"] is True

        rejected = await client.call_tool(
            "describe_psa_dataset", {"dataset_path": "https://evil.example/x"}
        )
        assert rejected.structured_content["validation_error"] is True


@pytest.mark.asyncio
async def test_successful_responses_pass_the_declared_output_schema(monkeypatch):
    from fastmcp import Client

    from ph_civic_data_mcp.server import mcp

    _install(monkeypatch)
    async with Client(mcp) as client:
        listing = await client.call_tool("browse_psa_catalog", {})
        assert listing.structured_content["folder_count"] == 2

        described = await client.call_tool("describe_psa_dataset", {"dataset_path": DATASET})
        assert described.structured_content["total_cells"] == 18

        queried = await client.call_tool(
            "query_psa_dataset", {"dataset_path": DATASET, "selections": FULL}
        )
        assert queried.structured_content["row_count"] == 2
        assert queried.structured_content["rows"][1]["value"] is None


# ---------------------------------------------------------------------------
# Hardening found by the cross-model review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_huge_value_list_is_rejected_before_it_is_copied(monkeypatch):
    """The ceiling used to fire only after building a second list that size."""
    _install(monkeypatch)
    out = await cat.query_psa_dataset(
        DATASET,
        {
            "Major Island Group": ["0"] * (cat.MAX_CELLS + 1),
            "Among Families/Population": ["0"],
            "Year": ["2"],
        },
    )
    assert out["validation_error"] is True
    assert "1001 values" in out["caveats"][0]


@pytest.mark.asyncio
async def test_a_truncated_dimension_still_validates_every_code(monkeypatch):
    """Value lists are capped for display, never for validation."""
    wide = {
        "title": "Wide table",
        "variables": [
            {
                "code": "Area",
                "text": "Area",
                "values": [str(i) for i in range(cat.MAX_VALUES_LISTED + 50)],
                "valueTexts": [f"Area {i}" for i in range(cat.MAX_VALUES_LISTED + 50)],
            }
        ],
    }
    posted = {"n": 0}

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            posted["n"] += 1
            return _resp(method, url, DATA)
        return _resp(method, url, wide)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)

    described = await cat.describe_psa_dataset("1F/FY/wide.px")
    assert described["dimensions"][0]["values_truncated"] is True
    assert "_all_codes" not in described["dimensions"][0], "internal field leaked"

    # A code past the display cap is real, so it must be accepted.
    ok = await cat.query_psa_dataset("1F/FY/wide.px", {"Area": ["520"]})
    assert not ok.get("validation_error"), ok.get("caveats")

    # A code that does not exist is a caller mistake, never an upstream error.
    bad = await cat.query_psa_dataset("1F/FY/wide.px", {"Area": ["99999"]})
    assert bad["validation_error"] is True
    assert not bad.get("upstream_error")
    assert posted["n"] == 1, "the bad code must not reach PSA"


@pytest.mark.asyncio
async def test_concurrent_cold_calls_fetch_the_metadata_once(monkeypatch):
    """Twenty callers for one uncached table used to queue twenty GETs."""
    import asyncio

    fetches = {"n": 0}

    async def _slow(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, DATA)
        fetches["n"] += 1
        await asyncio.sleep(0.01)
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _slow)

    results = await asyncio.gather(*[cat.describe_psa_dataset(DATASET) for _ in range(20)])
    assert all(r["total_cells"] == 18 for r in results)
    assert fetches["n"] == 1, f"metadata fetched {fetches['n']} times, expected 1"


@pytest.mark.asyncio
async def test_two_time_dimensions_do_not_become_one_false_range(monkeypatch):
    two_time = {
        "title": "Two time dims",
        "variables": [
            {
                "code": "Year",
                "text": "Year",
                "values": ["0", "1"],
                "valueTexts": ["2018", "2023"],
            },
            {
                "code": "Period",
                "text": "Period",
                "values": ["0", "1"],
                "valueTexts": ["Q1", "Q4"],
            },
        ],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(
                method,
                url,
                {
                    "columns": [
                        {"code": "Year", "type": "d"},
                        {"code": "Period", "type": "d"},
                        {"code": "V", "type": "c"},
                    ],
                    "data": [{"key": ["0", "0"], "values": ["1.0"]}],
                },
            )
        return _resp(method, url, two_time)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset("1F/FY/two.px", {"Year": ["0", "1"], "Period": ["0", "1"]})
    assert out["reference_period"] == "2018 to 2023; Q1 to Q4", out["reference_period"]


@pytest.mark.asyncio
async def test_an_unreadable_cell_is_flagged_apart_from_a_psa_missing_marker(monkeypatch):
    payload = {
        "columns": [
            {"code": "Major Island Group", "type": "d"},
            {"code": "Among Families/Population", "type": "d"},
            {"code": "Year", "type": "d"},
            {"code": "V", "type": "c"},
        ],
        "data": [
            {"key": ["0", "0", "2"], "values": [".."]},
            {"key": ["1", "0", "2"], "values": ["not-a-number"]},
        ],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, payload)
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(
        DATASET,
        {"Major Island Group": ["0", "1"], "Among Families/Population": ["0"], "Year": ["2"]},
    )
    assert out["rows"][0]["value"] is None
    assert out["rows"][1]["value"] is None
    joined = " ".join(out["caveats"])
    assert "missing value" in joined, joined
    assert "could not read as a number" in joined, "a parse failure must not read as PSA's '..'"
    # v0.7.0: a parse failure is drift, so the response is indeterminate.
    assert out["data_status"] == DATA_STATUS_INDETERMINATE
    assert out["upstream_error"] is True


@pytest.mark.asyncio
async def test_a_psa_missing_marker_alone_stays_a_success_with_a_null_cell(monkeypatch):
    """PSA's own '..' is a legitimate missing value, never drift."""
    payload = {
        "columns": [
            {"code": "Major Island Group", "type": "d"},
            {"code": "Among Families/Population", "type": "d"},
            {"code": "Year", "type": "d"},
            {"code": "V", "type": "c"},
        ],
        "data": [{"key": ["0", "0", "2"], "values": [".."]}],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, payload)
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(
        DATASET,
        {"Major Island Group": ["0"], "Among Families/Population": ["0"], "Year": ["2"]},
    )
    assert out["rows"][0]["value"] is None
    assert out["data_status"] == "success"
    assert out["upstream_error"] is False


@pytest.mark.asyncio
async def test_a_string_values_field_is_malformed_not_a_psa_missing_marker(monkeypatch):
    """A `values` field of "10.9" (a string, not a list) is drift. It must
    not read as a confirmed PSA '..' gap."""
    payload = {
        "columns": [
            {"code": "Major Island Group", "type": "d"},
            {"code": "Among Families/Population", "type": "d"},
            {"code": "Year", "type": "d"},
            {"code": "V", "type": "c"},
        ],
        "data": [{"key": ["0", "0", "2"], "values": "10.9"}],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, payload)
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(
        DATASET,
        {"Major Island Group": ["0"], "Among Families/Population": ["0"], "Year": ["2"]},
    )
    assert out["rows"][0]["value"] is None
    assert out["data_status"] == DATA_STATUS_INDETERMINATE
    assert out["upstream_error"] is True
    assert all(".." not in c for c in out["caveats"]), out["caveats"]


@pytest.mark.asyncio
async def test_a_misaligned_row_is_reported_not_silently_truncated(monkeypatch):
    payload = {
        "columns": [
            {"code": "Major Island Group", "type": "d"},
            {"code": "Among Families/Population", "type": "d"},
            {"code": "Year", "type": "d"},
            {"code": "V", "type": "c"},
        ],
        "data": [{"key": ["0", "0"], "values": ["10.9"]}],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, payload)
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(DATASET, FULL)
    # A key of the wrong length cannot map to a place or period, so the whole
    # response fails instead of publishing a row with no geography attached.
    assert out["upstream_error"] is True
    assert out["data_status"] == DATA_STATUS_INDETERMINATE
    assert out["rows"] == []
    assert any("1 of 1" in c for c in out["caveats"]), out["caveats"]


@pytest.mark.asyncio
async def test_max_rows_ceiling_is_actually_enforced(monkeypatch):
    """MAX_ROWS_CEILING was never exercised: the fixture only had 2 rows."""
    big = {
        "columns": [
            {"code": "Major Island Group", "type": "d"},
            {"code": "Among Families/Population", "type": "d"},
            {"code": "Year", "type": "d"},
            {"code": "V", "type": "c"},
        ],
        "data": [{"key": ["0", "0", "2"], "values": ["1.0"]} for _ in range(6000)],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, big)
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(DATASET, FULL, max_rows=10**6)
    assert out["row_count"] == cat.MAX_ROWS_CEILING
    assert out["total_rows_available"] == 6000
    assert out["truncated"] is True


@pytest.mark.asyncio
async def test_a_partial_row_count_is_indeterminate_not_a_quiet_success(monkeypatch):
    """A 4-cell selection that returns 1 row must not report plain success."""
    four_cell_meta = {
        "title": "Four cells",
        "variables": [
            {
                "code": "Year",
                "text": "Year",
                "values": ["0", "1", "2", "3"],
                "valueTexts": ["2020", "2021", "2022", "2023"],
            }
        ],
    }
    one_row = {
        "columns": [{"code": "Year", "type": "d"}, {"code": "V", "type": "c"}],
        "data": [{"key": ["0"], "values": ["1.0"]}],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, one_row)
        return _resp(method, url, four_cell_meta)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset("1F/FY/four.px", {"Year": ["0", "1", "2", "3"]})
    assert out["data_status"] == DATA_STATUS_INDETERMINATE
    assert out["upstream_error"] is True
    assert out["row_count"] == 1
    assert out["requested_cells"] == 4
    assert any("1 row(s) for a 4-cell selection" in c for c in out["caveats"]), out["caveats"]


@pytest.mark.asyncio
async def test_a_response_with_no_data_array_is_an_upstream_error(monkeypatch):
    """A 200 with a malformed body is drift, not an empty result set."""

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, {"columns": []})
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(DATASET, FULL)
    assert out["upstream_error"] is True
    assert out["rows"] == []
    assert any("no `data` array" in c for c in out["caveats"]), out["caveats"]


@pytest.mark.asyncio
async def test_a_nonzero_selection_with_zero_rows_is_indeterminate(monkeypatch):
    """PSA writes a missing cell as '..' inside a row, never as zero rows."""

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, {"columns": [], "data": []})
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    one_cell = {"Major Island Group": ["0"], "Among Families/Population": ["0"], "Year": ["2"]}
    out = await cat.query_psa_dataset(DATASET, one_cell)
    assert out["data_status"] == DATA_STATUS_INDETERMINATE
    assert out["upstream_error"] is True
    assert out["rows"] == []
    assert any("1-cell" in c for c in out["caveats"]), out["caveats"]


@pytest.mark.asyncio
async def test_a_label_survives_past_the_display_cap(monkeypatch):
    """Validation reads every code, so labels must reach that far too."""
    n = cat.MAX_VALUES_LISTED + 50
    wide = {
        "title": "Wide table",
        "variables": [
            {
                "code": "Year",
                "text": "Year",
                "values": [str(i) for i in range(n)],
                "valueTexts": [str(1500 + i) for i in range(n)],
            }
        ],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(
                method,
                url,
                {
                    "columns": [{"code": "Year", "type": "d"}, {"code": "V", "type": "c"}],
                    "data": [{"key": ["520"], "values": ["7.5"]}],
                },
            )
        return _resp(method, url, wide)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset("1F/FY/wide.px", {"Year": ["520"]})
    assert out["rows"][0]["labels"]["Year"] == "2020", out["rows"][0]["labels"]
    assert out["reference_period"] == "2020", out["reference_period"]


@pytest.mark.asyncio
async def test_a_wrong_typed_values_field_is_not_indexed_as_a_list(monkeypatch):
    """values as a string would hand back its first character as data."""

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(
                method,
                url,
                {
                    "columns": [
                        {"code": "Major Island Group", "type": "d"},
                        {"code": "Among Families/Population", "type": "d"},
                        {"code": "Year", "type": "d"},
                        {"code": "V", "type": "c"},
                    ],
                    "data": [{"key": ["0", "0", "2"], "values": "10.9"}],
                },
            )
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(DATASET, FULL)
    assert out["rows"][0]["value"] is None, "must not read '1' out of the string '10.9'"
    assert any("key count" in c for c in out["caveats"]), out["caveats"]


def test_the_metadata_lock_registry_is_bounded():
    cat._META_LOCKS.clear()
    for i in range(cat._MAX_META_LOCKS + 20):
        cat._meta_lock(f"1F/FY/{i}.px")
    assert len(cat._META_LOCKS) <= cat._MAX_META_LOCKS
    cat._META_LOCKS.clear()


@pytest.mark.asyncio
async def test_the_reference_period_reads_chronologically(monkeypatch):
    """Caller order must not reverse the reported period."""

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, DATA)
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    backwards = await cat.query_psa_dataset(
        DATASET,
        {
            "Major Island Group": ["0"],
            "Among Families/Population": ["0"],
            "Year": ["2", "0"],
        },
    )
    assert backwards["reference_period"] == "2018 to 2023", backwards["reference_period"]


@pytest.mark.asyncio
async def test_a_wrong_path_is_a_caller_mistake_not_an_outage(monkeypatch):
    """A 404 told the agent 'unreachable', so it retried a path that cannot work."""

    async def _not_found(client, method, url, **kwargs):
        return httpx.Response(404, text="nope", request=httpx.Request(method, url))

    monkeypatch.setattr(psa_module, "fetch_with_retry", _not_found)

    listing = await cat.browse_psa_catalog("ZZ")
    assert listing["validation_error"] is True
    assert not listing.get("upstream_error")

    described = await cat.describe_psa_dataset("1F/FY/nosuch.px")
    assert described["validation_error"] is True
    assert not described.get("upstream_error")

    queried = await cat.query_psa_dataset("1F/FY/nosuch.px", {"Year": ["0"]})
    assert queried["validation_error"] is True
    assert not queried.get("upstream_error")


@pytest.mark.asyncio
async def test_browsing_a_dataset_path_says_so(monkeypatch):
    """A .px path answers with metadata, not a listing. That is a caller error."""

    async def _meta_body(client, method, url, **kwargs):
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _meta_body)
    out = await cat.browse_psa_catalog("1F/FY/0241F3DF013.px")
    assert out["validation_error"] is True
    assert "describe_psa_dataset" in out["caveats"][0]


@pytest.mark.asyncio
async def test_a_real_outage_is_still_an_outage(monkeypatch):
    """The 404 mapping must not swallow a genuine transport failure."""

    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("openstat down")

    monkeypatch.setattr(psa_module, "fetch_with_retry", _boom)
    out = await cat.browse_psa_catalog("1F")
    assert out["upstream_error"] is True
    assert not out.get("validation_error")


@pytest.mark.asyncio
async def test_wrong_typed_metadata_is_not_split_into_characters(monkeypatch):
    """list('abc') is ['a','b','c'], which would publish three fake codes."""
    broken = {
        "title": "Broken",
        "variables": [
            {"code": "Year", "text": "Year", "values": "012", "valueTexts": "abc"},
            "not-a-variable",
        ],
    }

    async def _fake(client, method, url, **kwargs):
        return _resp(method, url, broken)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.describe_psa_dataset("1F/FY/broken.px")
    dims = out["dimensions"]
    assert len(dims) == 1
    assert dims[0]["values"] == [], f"characters leaked as codes: {dims[0]['values']}"
    assert dims[0]["value_count"] == 0


@pytest.mark.asyncio
async def test_a_malformed_row_does_not_crash_the_tool(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(
                method,
                url,
                {
                    "columns": [
                        {"code": "Major Island Group", "type": "d"},
                        {"code": "Among Families/Population", "type": "d"},
                        {"code": "Year", "type": "d"},
                        {"code": "V", "type": "c"},
                    ],
                    "data": ["not-a-row", {"key": "abc", "values": ["1.0"]}],
                },
            )
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(DATASET, FULL)
    # Neither row has a usable key, so the tool must not crash and must not
    # publish a value with no geography or period.
    assert out["upstream_error"] is True
    assert out["rows"] == []
    assert any("2 of 2" in c for c in out["caveats"]), out["caveats"]


@pytest.mark.asyncio
async def test_a_non_list_key_does_not_publish_a_bare_value(monkeypatch):
    """A key that is not a list must not turn into keys: {}, value: 1.0."""
    payload = {
        "columns": [
            {"code": "Major Island Group", "type": "d"},
            {"code": "Among Families/Population", "type": "d"},
            {"code": "Year", "type": "d"},
            {"code": "V", "type": "c"},
        ],
        "data": [{"key": "abc", "values": ["1.0"]}],
    }

    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(method, url, payload)
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(DATASET, FULL)
    assert out["data_status"] == DATA_STATUS_INDETERMINATE
    assert out["upstream_error"] is True
    assert out["rows"] == []
    assert any("1 of 1" in c for c in out["caveats"]), out["caveats"]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [True, False, 1.5, "500", None])
async def test_max_rows_rejects_a_non_integer(monkeypatch, bad):
    _install(monkeypatch)
    out = await cat.query_psa_dataset(DATASET, FULL, max_rows=bad)
    assert out["validation_error"] is True, f"{bad!r} was accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("variables", ["abc", {"a": 1}, [], 7, None])
async def test_non_list_variables_is_an_upstream_error(monkeypatch, variables):
    """A truthy check let a string through, and readers treated it as a list."""

    async def _fake(client, method, url, **kwargs):
        return _resp(method, url, {"title": "T", "variables": variables})

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.describe_psa_dataset("1F/FY/broken.px")
    assert out["upstream_error"] is True, f"{variables!r} was accepted"
    assert out["dimensions"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variable",
    [
        {"code": 123, "values": ["0"], "valueTexts": ["x"]},
        {"code": "A", "text": 7, "values": ["0"], "valueTexts": [None]},
        {"code": "A", "values": [1, 2], "valueTexts": [3, 4]},
        {"code": None, "text": None, "values": ["0"], "valueTexts": ["x"]},
    ],
)
async def test_non_string_metadata_does_not_crash_describe(monkeypatch, variable):
    """A numeric code raised AttributeError on .lower() and killed the tool."""

    async def _fake(client, method, url, **kwargs):
        return _resp(method, url, {"title": "t", "variables": [variable]})

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.describe_psa_dataset("1F/FY/x.px")
    assert len(out["dimensions"]) == 1
    dim = out["dimensions"][0]
    assert isinstance(dim["code"], str)
    assert isinstance(dim["label"], str)
    for value in dim["values"]:
        assert isinstance(value["code"], str)
        assert isinstance(value["label"], str)


@pytest.mark.asyncio
async def test_an_empty_values_array_is_drift_not_a_psa_missing_marker(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        if method == "POST":
            return _resp(
                method,
                url,
                {
                    "columns": [
                        {"code": "Major Island Group", "type": "d"},
                        {"code": "Among Families/Population", "type": "d"},
                        {"code": "Year", "type": "d"},
                        {"code": "V", "type": "c"},
                    ],
                    "data": [{"key": ["0", "0", "2"], "values": []}],
                },
            )
        return _resp(method, url, META)

    monkeypatch.setattr(psa_module, "fetch_with_retry", _fake)
    out = await cat.query_psa_dataset(DATASET, FULL)
    assert out["rows"][0]["value"] is None
    assert any("key count" in c for c in out["caveats"]), out["caveats"]
