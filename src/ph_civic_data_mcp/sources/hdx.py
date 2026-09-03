"""HDX (Humanitarian Data Exchange) CKAN API — Philippine dataset search.

HDX runs a standard CKAN catalog. `package_search` covers every dataset in
the Philippines country group (481 datasets as of the last live probe), each
with its own license (`license_id`, `license_title`, `license_url`). HDX has
no site-wide license: CC BY, ODC-BY, ODC-ODbL, and PDDL all appear on real PH
datasets, so a caller must read `license_id` before reuse instead of
assuming one blanket term.
https://data.humdata.org/api/3/action/help_show?name=package_search
"""

from __future__ import annotations

from datetime import datetime, timezone

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_EMPTY,
    DATA_STATUS_INDETERMINATE,
    DATA_STATUS_SUCCESS,
    failure_result,
)
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

SOURCE_NAME = "HDX"
HDX_URL = "https://data.humdata.org/api/3/action/package_search"
HDX_DATASET_BASE = "https://data.humdata.org/dataset"
HDX_LICENSE = "HDX (Humanitarian Data Exchange) CKAN API, per-dataset license"
PH_GROUP_FILTER = "groups:phl"

MIN_QUERY_LEN = 1
MAX_QUERY_LEN = 200
MIN_ROWS = 1
MAX_ROWS = 50
MAX_RESOURCES_PER_DATASET = 20

LICENSE_NOTE = "Each dataset carries its own license. Check license_id before you reuse it."


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_resource(res: object) -> dict | None:
    if not isinstance(res, dict):
        return None
    return {
        "name": res.get("name"),
        "format": res.get("format"),
        "url": res.get("url"),
        "size": res.get("size"),
        "last_modified": res.get("last_modified"),
    }


def _parse_dataset(item: object) -> dict | None:
    """One CKAN package into the tool's dataset shape, or None if unreadable.

    A dataset with no `name` cannot build an `hdx_url`, so it is dropped the
    same way a USGS feature with no usable magnitude is dropped: one bad
    entry skips itself, never the whole page.
    """
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name:
        return None
    org = item.get("organization")
    org_title = org.get("title") if isinstance(org, dict) else None
    raw_resources = item.get("resources")
    resources = []
    if isinstance(raw_resources, list):
        for res in raw_resources[:MAX_RESOURCES_PER_DATASET]:
            parsed = _parse_resource(res)
            if parsed is not None:
                resources.append(parsed)
    return {
        "name": name,
        "title": item.get("title") or name,
        "organization": org_title,
        "license_id": item.get("license_id"),
        "license_title": item.get("license_title"),
        "license_url": item.get("license_url"),
        "last_modified": item.get("last_modified"),
        "num_resources": item.get("num_resources", len(resources)),
        "resources": resources,
        "hdx_url": f"{HDX_DATASET_BASE}/{name}",
    }


@mcp.tool(
    title="Search HDX for Philippine humanitarian datasets",
    tags={"hdx", "open-data", "humanitarian", "philippines", "catalog"},
    annotations={
        "title": "Search HDX for Philippine humanitarian datasets",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def search_hdx_datasets(query: str, rows: int = 10) -> dict:
    """Search HDX for Philippine humanitarian datasets by keyword.

    Calls the CKAN `package_search` action filtered to the Philippines
    country group and returns matching datasets, most recently modified
    first, each with its own license and up to 20 resources. Examples:

      search_hdx_datasets("flood")                    datasets matching "flood"
      search_hdx_datasets("food prices", rows=5)       5 most recently modified matches

    On failure: an empty query, a query over 200 characters, a query with a
    control character, or a rows value outside 1 to 50 gives
    validation_error true and data_status "invalid_request". An unreachable
    HDX API gives upstream_error true and data_status "unavailable". A
    response whose success field is not true, or whose result.results field
    is not a list, gives upstream_error true and data_status
    "indeterminate". Zero datasets on a clean query gives data_status
    "empty", never a failure, and still caches. Every dataset carries its
    own license_id: read it before you reuse a resource.

    Args:
        query: Free-text search term, 1 to 200 printable characters, for
               example "flood", "food security", "displacement".
        rows: Number of datasets to return, 1 to 50 (default 10).
    """
    if not isinstance(query, str) or not query.strip():
        return failure_result(
            SOURCE_NAME,
            HDX_URL,
            "query must be a non-empty string.",
            license=HDX_LICENSE,
            validation_error=True,
            query=query,
            total_count=0,
            datasets=[],
        )
    query = query.strip()
    if len(query) > MAX_QUERY_LEN:
        return failure_result(
            SOURCE_NAME,
            HDX_URL,
            f"query must be at most {MAX_QUERY_LEN} characters, got {len(query)}.",
            license=HDX_LICENSE,
            validation_error=True,
            query=query,
            total_count=0,
            datasets=[],
        )
    if not query.isprintable():
        return failure_result(
            SOURCE_NAME,
            HDX_URL,
            "query must not contain a control character.",
            license=HDX_LICENSE,
            validation_error=True,
            query=query,
            total_count=0,
            datasets=[],
        )
    if isinstance(rows, bool) or not isinstance(rows, int):
        return failure_result(
            SOURCE_NAME,
            HDX_URL,
            f"rows must be a whole number, got {type(rows).__name__}.",
            license=HDX_LICENSE,
            validation_error=True,
            query=query,
            total_count=0,
            datasets=[],
        )
    if not (MIN_ROWS <= rows <= MAX_ROWS):
        return failure_result(
            SOURCE_NAME,
            HDX_URL,
            f"rows must be between {MIN_ROWS} and {MAX_ROWS}, got {rows}.",
            license=HDX_LICENSE,
            validation_error=True,
            query=query,
            total_count=0,
            datasets=[],
        )

    ckey = cache_key({"tool": "hdx", "query": query, "rows": rows})
    cache = CACHES["hdx_search"]
    if ckey in cache:
        return cache[ckey]

    params = {
        "q": query,
        "fq": PH_GROUP_FILTER,
        "rows": rows,
        "sort": "metadata_modified desc",
    }

    try:
        response = await fetch_with_retry(CLIENT, "GET", HDX_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        log_stderr(f"HDX error: {exc}")
        return failure_result(
            SOURCE_NAME,
            HDX_URL,
            f"HDX package_search unavailable ({type(exc).__name__}: {exc}).",
            license=HDX_LICENSE,
            query=query,
            total_count=0,
            datasets=[],
        )

    if not isinstance(payload, dict) or payload.get("success") is not True:
        log_stderr(f"HDX returned success={isinstance(payload, dict) and payload.get('success')}")
        return failure_result(
            SOURCE_NAME,
            HDX_URL,
            "HDX package_search returned a response with success not true.",
            license=HDX_LICENSE,
            data_status=DATA_STATUS_INDETERMINATE,
            query=query,
            total_count=0,
            datasets=[],
        )

    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("results"), list):
        log_stderr("HDX package_search sent a success body with no readable result.results")
        return failure_result(
            SOURCE_NAME,
            HDX_URL,
            "HDX package_search sent a success body with no readable result.results list.",
            license=HDX_LICENSE,
            data_status=DATA_STATUS_INDETERMINATE,
            query=query,
            total_count=0,
            datasets=[],
        )

    raw_results = result["results"]
    count_field = result.get("count")

    # `count` above 0 with an empty `results` is the same drift shape
    # world_bank._fetch_observations guards with `total != 0`: a page that
    # always has datasets sent none this time, which is not a real zero.
    if not raw_results and isinstance(count_field, int) and count_field > 0:
        return failure_result(
            SOURCE_NAME,
            HDX_URL,
            f"HDX reported count={count_field} but sent 0 results; not a real zero.",
            license=HDX_LICENSE,
            data_status=DATA_STATUS_INDETERMINATE,
            query=query,
            total_count=0,
            datasets=[],
        )

    datasets = [d for d in (_parse_dataset(item) for item in raw_results) if d is not None]

    # A nonempty results list where every entry failed to parse is drift, not
    # a real zero. The same shape as USGS's all-fail-parse guard: a genuine
    # zero-dataset answer and a page that always has datasets look the same
    # on the surface, and only the raw count tells them apart.
    if raw_results and not datasets:
        return failure_result(
            SOURCE_NAME,
            HDX_URL,
            f"HDX sent {len(raw_results)} result(s) but none parsed (missing a dataset name).",
            license=HDX_LICENSE,
            data_status=DATA_STATUS_INDETERMINATE,
            query=query,
            total_count=0,
            datasets=[],
        )

    total_count = result.get("count")
    if not isinstance(total_count, int):
        total_count = len(datasets)

    out = {
        "source": SOURCE_NAME,
        "source_url": HDX_URL,
        "license": HDX_LICENSE,
        "data_status": DATA_STATUS_SUCCESS if datasets else DATA_STATUS_EMPTY,
        "upstream_error": False,
        "validation_error": False,
        "query": query,
        "total_count": total_count,
        "datasets": datasets,
        "note": LICENSE_NOTE,
        "caveats": [],
        "data_retrieved_at": _now().isoformat(),
    }
    cache[ckey] = out
    return out
