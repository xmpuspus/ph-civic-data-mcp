"""Bounded, agent-safe access to the whole PSA OpenSTAT statistical catalog.

The curated PSA tools (population, poverty, inflation, labor, health) each pin
one subject and one table predicate. This module opens the rest of the
catalog: browse the hierarchy, describe a dataset, then run one explicit,
bounded query.

Three limits keep that safe:

- Every path is relative and is rebuilt under `PSA_API_BASE`. A scheme, a host,
  a query string, a fragment, `..`, or an odd character is rejected before any
  request goes out.
- Every dimension needs an explicit list of value codes. PXWeb expands an
  unnamed dimension to all its values, which is how a small-looking query turns
  into a full-cube request. PSA answers those with an HTTP 403 from its WAF.
- The cell product is computed before the POST and capped at MAX_CELLS.

A caller mistake returns `validation_error: true`. Only a real upstream failure
returns `upstream_error: true`. Neither is ever cached.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.sources.psa import (
    _MISSING as PSA_MISSING_MARKERS,
    PSA_API_BASE,
    PSA_LICENSE,
    PSANotFoundError,
    PSAUpstreamError,
    _browse,
    _get_json_or_raise,
    _key_columns,
    _now,
    _post_json_or_raise,
    _to_float,
)
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_SUCCESS,
    DATA_STATUS_VALUES,
    failure_result,
)

SOURCE_NAME = "PSA OpenSTAT"
CATALOG_ROOT_URL = f"{PSA_API_BASE}/DB/"

# Hard ceiling on one query. PSA answers full-cube requests with a WAF 403, and
# a large cube is also unreadable to an agent. 1000 cells is roughly a
# 100-area by 10-year table.
MAX_CELLS = 1000
MAX_ROWS_CEILING = 5000
MIN_ROWS = 1

# Guards on a caller-supplied path.
MAX_PATH_SEGMENTS = 8
MAX_SEGMENT_LENGTH = 64
MAX_VALUES_LISTED = 500
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

DISCLAIMER = (
    "Statistical indicators derived from public data. Patterns may have legitimate explanations."
)

VINTAGE_NOTE = (
    "Read the reference period from the table's own time or Year dimension. "
    "The PXWeb `updated` field is server wall-clock, not data vintage."
)


class CatalogPathError(ValueError):
    """A caller-supplied catalog path or selection failed validation."""


# ---------------------------------------------------------------------------
# Output schemas
#
# Every shape a tool can return, including the two failure shapes, is described
# here. A failure envelope is a valid response, so it must never fail schema
# validation. Only the fields present on all three shapes are required.
# ---------------------------------------------------------------------------

_ENVELOPE_FIELDS: dict[str, Any] = {
    "source": {"type": "string", "description": "Upstream data source name."},
    "source_url": {"type": "string", "description": "Canonical OpenSTAT URL used."},
    "license": {"type": "string"},
    "data_retrieved_at": {"type": "string", "format": "date-time"},
    "caveats": {"type": "array", "items": {"type": "string"}},
    "upstream_error": {
        "type": "boolean",
        "description": "True when OpenSTAT was unreachable. Not an empty result.",
    },
    "validation_error": {
        "type": "boolean",
        "description": "True when the caller's arguments were rejected before any request.",
    },
    "note": {"type": "string"},
}

_REQUIRED = ["source", "source_url", "data_retrieved_at", "caveats"]

BROWSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "One level of the PSA OpenSTAT catalog, or a failure envelope.",
    "properties": {
        **_ENVELOPE_FIELDS,
        "path": {"type": ["string", "null"], "description": "Relative path browsed."},
        "parent_path": {"type": ["string", "null"]},
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "type": {"type": "string", "enum": ["folder", "dataset"]},
                    "path": {
                        "type": "string",
                        "description": "Pass back to browse or describe.",
                    },
                },
                "required": ["id", "title", "type", "path"],
            },
        },
        "folder_count": {"type": "integer"},
        "dataset_count": {"type": "integer"},
    },
    "required": [*_REQUIRED, "entries"],
}

_DIMENSION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "code": {"type": "string", "description": "Use this key in `selections`."},
        "label": {"type": "string"},
        "value_count": {"type": "integer"},
        "values": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"code": {"type": "string"}, "label": {"type": "string"}},
                "required": ["code", "label"],
            },
        },
        "values_truncated": {"type": "boolean"},
        "is_time_like": {"type": "boolean"},
    },
    "required": ["code", "label", "value_count", "values"],
}

DESCRIBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Dimensions and valid value codes of one dataset, or a failure envelope.",
    "properties": {
        **_ENVELOPE_FIELDS,
        "dataset_path": {"type": "string"},
        "title": {"type": "string"},
        "dimensions": {"type": "array", "items": _DIMENSION_SCHEMA},
        "time_dimensions": {"type": "array", "items": {"type": "string"}},
        "total_cells": {"type": "integer", "description": "Size of the full cube."},
        "max_cells_per_query": {"type": "integer"},
    },
    "required": [*_REQUIRED, "dataset_path", "dimensions"],
}

QUERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Normalized rows from one bounded query, or a failure envelope.",
    "properties": {
        **_ENVELOPE_FIELDS,
        "dataset_path": {"type": "string"},
        "title": {"type": "string"},
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "keys": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Dimension code -> selected value code.",
                    },
                    "labels": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                        "description": "Dimension code -> human-readable value label.",
                    },
                    "value": {
                        "type": ["number", "null"],
                        "description": "Null means PSA published '..' here, not zero.",
                    },
                },
                "required": ["keys", "labels", "value"],
            },
        },
        "row_count": {"type": "integer"},
        "total_rows_available": {"type": "integer"},
        "requested_cells": {"type": "integer"},
        "truncated": {"type": "boolean"},
        "reference_period": {
            "type": ["string", "null"],
            "description": "Data vintage read from the table's own time dimension.",
        },
        "disclaimer": {"type": "string"},
    },
    "required": [*_REQUIRED, "dataset_path", "rows", "row_count"],
}

_MATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Pass to describe_psa_dataset or query_psa_dataset.",
        },
        "title": {"type": "string"},
    },
    "required": ["path", "title"],
}

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": "Dataset titles and paths matching a keyword, or a failure envelope.",
    "properties": {
        **_ENVELOPE_FIELDS,
        "data_status": {"type": "string", "enum": sorted(DATA_STATUS_VALUES)},
        "keyword": {"type": "string"},
        "matches": {"type": "array", "items": _MATCH_SCHEMA},
        "match_count": {"type": "integer"},
        "total_available": {"type": "integer"},
        "limit": {"type": "integer"},
    },
    "required": [*_REQUIRED, "keyword", "matches", "match_count"],
}


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def _normalize_path(raw: str | None) -> str:
    """Turn caller input into a safe relative path under `DB/`, or raise.

    Returns "" for the catalog root.
    """
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise CatalogPathError("path must be a string")
    path = raw.strip()
    if not path:
        return ""
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in path):
        raise CatalogPathError("path contains a control character")
    if "://" in path or path.startswith("//"):
        raise CatalogPathError("path must be relative to the OpenSTAT catalog root, not a URL")
    for bad in ("?", "#", "\\", " ", "%", "@", ":"):
        if bad in path:
            raise CatalogPathError(f"path must not contain {bad!r}")
    segments = [s for s in path.strip("/").split("/")]
    # Tolerate a leading "DB" because the API URL carries one and agents copy it.
    if segments and segments[0].upper() == "DB":
        segments = segments[1:]
    if not segments:
        return ""
    if len(segments) > MAX_PATH_SEGMENTS:
        raise CatalogPathError(f"path is deeper than {MAX_PATH_SEGMENTS} segments")
    for segment in segments:
        if not segment:
            raise CatalogPathError("path contains an empty segment")
        if segment in (".", ".."):
            raise CatalogPathError("path must not contain '.' or '..'")
        if len(segment) > MAX_SEGMENT_LENGTH:
            raise CatalogPathError("path segment is too long")
        if not _SEGMENT_RE.match(segment):
            raise CatalogPathError(f"path segment {segment!r} has unexpected characters")
    return "/".join(segments)


def _dataset_path(raw: str) -> str:
    path = _normalize_path(raw)
    if not path:
        raise CatalogPathError("dataset_path is required")
    if not path.lower().endswith(".px"):
        raise CatalogPathError(
            "dataset_path must point at a .px dataset. Use browse_psa_catalog to find one."
        )
    return path


def _catalog_url(path: str) -> str:
    return f"{CATALOG_ROOT_URL}{path}/" if path else CATALOG_ROOT_URL


def _dataset_url(path: str) -> str:
    return f"{CATALOG_ROOT_URL}{path}"


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------


def _base_envelope(source_url: str) -> dict:
    return {
        "source": SOURCE_NAME,
        "source_url": source_url,
        "license": PSA_LICENSE,
        "data_retrieved_at": _now().isoformat(),
        "caveats": [],
    }


def _validation_envelope(source_url: str, message: str, **extra: Any) -> dict:
    out = _base_envelope(source_url)
    out.update(extra)
    out["validation_error"] = True
    out["caveats"] = [message]
    return out


def _upstream_envelope(source_url: str, message: str, **extra: Any) -> dict:
    out = _base_envelope(source_url)
    out.update(extra)
    out["upstream_error"] = True
    out["caveats"] = [message]
    return out


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


# One lock per dataset path. Without it, N concurrent cold calls for the same
# table all miss the cache and queue N identical GETs behind the rate limiter,
# so the later ones blow their own timeout while the first result sits unused.
_MAX_META_LOCKS = 256
_META_LOCKS: dict[str, asyncio.Lock] = {}


def _meta_lock(path: str) -> asyncio.Lock:
    lock = _META_LOCKS.get(path)
    if lock is not None:
        return lock
    if len(_META_LOCKS) >= _MAX_META_LOCKS:
        # Only drop entries nobody is holding. Clearing the whole registry
        # would hand a second caller a fresh lock for a path someone is
        # already inside, which silently un-does single-flight.
        for key in [k for k, held in _META_LOCKS.items() if not held.locked()]:
            del _META_LOCKS[key]
        if len(_META_LOCKS) >= _MAX_META_LOCKS:
            return asyncio.Lock()
    return _META_LOCKS.setdefault(path, asyncio.Lock())


async def _dataset_meta(path: str) -> dict:
    """Fetch and cache `.px` metadata. Successes only; errors stay retryable."""
    key = cache_key({"psa_meta": path})
    cache = CACHES["psa_browse"]
    if key in cache:
        return cache[key]

    async with _meta_lock(path):
        # Re-check: the holder before us may have filled the cache already.
        if key in cache:
            return cache[key]
        meta = await _get_json_or_raise(_dataset_url(path))
        if not isinstance(meta, dict):
            raise PSAUpstreamError(f"PSA dataset {path} returned a non-object body")
        variables = meta.get("variables")
        if not isinstance(variables, list) or not variables:
            # A truthy check alone let a string or a dict through, and every
            # reader downstream then treated it as a list of variables.
            raise PSAUpstreamError(f"PSA dataset {path} returned no variable metadata")
        cache[key] = meta
        return meta


def _is_time_like(var: dict) -> bool:
    # str() on purpose: a numeric code from PSA raised AttributeError on
    # .lower() and took the whole tool down.
    code = str(var.get("code") or "").lower()
    text = str(var.get("text") or "").lower()
    return bool(var.get("time")) or "year" in code or "period" in code or text == "year"


def _dimensions(meta: dict) -> list[dict]:
    dims: list[dict] = []
    for var in meta.get("variables", []):
        if not isinstance(var, dict):
            continue
        raw_values = var.get("values")
        raw_texts = var.get("valueTexts")
        # list("abc") is ["a","b","c"], which would publish three fake codes.
        values = list(raw_values) if isinstance(raw_values, (list, tuple)) else []
        texts = list(raw_texts) if isinstance(raw_texts, (list, tuple)) else []
        listed = [
            {"code": str(v), "label": str(texts[i] if i < len(texts) else v)}
            for i, v in enumerate(values[:MAX_VALUES_LISTED])
        ]
        dims.append(
            {
                "code": str(var.get("code") or var.get("text") or ""),
                "label": str(var.get("text") or var.get("code") or ""),
                "value_count": len(values),
                "values": listed,
                "values_truncated": len(values) > MAX_VALUES_LISTED,
                "is_time_like": _is_time_like(var),
                # The reported list is capped for readability, but validation
                # checks against every code PSA declares. Validating against the
                # truncated list would wave a bad code through to PXWeb and turn
                # a caller mistake into a reported upstream error.
                "_all_codes": frozenset(str(v) for v in values),
                "_all_labels": {
                    str(v): str(texts[i] if i < len(texts) else v) for i, v in enumerate(values)
                },
            }
        )
    return dims


def _public_dimensions(dims: list[dict]) -> list[dict]:
    """Drop the internal validation set before a dimension leaves the server."""
    return [{k: v for k, v in dim.items() if not k.startswith("_")} for dim in dims]


def _total_cells(dims: list[dict]) -> int:
    total = 1
    for dim in dims:
        total *= max(dim["value_count"], 1)
    return total


# ---------------------------------------------------------------------------
# browse_psa_catalog
# ---------------------------------------------------------------------------


@mcp.tool(
    title="Browse the PSA OpenSTAT catalog",
    tags={"psa", "openstat", "statistics", "catalog", "philippines"},
    annotations={
        "title": "Browse the PSA OpenSTAT catalog",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
    output_schema=BROWSE_SCHEMA,
    timeout=60.0,
)
async def browse_psa_catalog(path: str | None = None) -> dict:
    """List one level of the PSA OpenSTAT statistical catalog.

    OpenSTAT publishes roughly 2,900 tables across 27 subjects. This walks that
    tree one level at a time so an agent can find a dataset without guessing a
    table id.

    Args:
        path: Relative catalog path such as "1F" or "1F/FY". None or ""
              returns the 27 top-level subjects. Use the `path` field of an
              entry from a previous call to go one level deeper.

    Returns: path, parent_path, entries (each with id, title, type
    "folder"/"dataset", and the `path` to pass back), folder_count,
    dataset_count, source, source_url, license, data_retrieved_at, caveats.

    A `dataset` entry is a `.px` table. Pass its `path` to
    describe_psa_dataset before calling query_psa_dataset. Folder depth varies
    by subject, so keep browsing until entries come back as datasets.

    On upstream failure this returns upstream_error: true with an empty
    entries list. That means the catalog was unreachable, never that the
    folder is empty.
    """
    try:
        safe = _normalize_path(path)
    except CatalogPathError as exc:
        return _validation_envelope(CATALOG_ROOT_URL, str(exc), path=path, entries=[])

    url = _catalog_url(safe)
    try:
        raw_entries = await _browse(safe)
    except PSANotFoundError as exc:
        # A wrong path is a caller mistake. Calling it an outage would tell the
        # agent to retry something that can never work.
        return _validation_envelope(url, str(exc), path=safe, entries=[])
    except PSAUpstreamError as exc:
        return _upstream_envelope(url, f"PSA OpenSTAT browse failed: {exc}", path=safe, entries=[])

    entries: list[dict] = []
    for entry in raw_entries:
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            continue
        is_dataset = entry.get("type") == "t" or entry_id.lower().endswith(".px")
        entries.append(
            {
                "id": entry_id,
                "title": entry.get("text") or entry_id,
                "type": "dataset" if is_dataset else "folder",
                "path": f"{safe}/{entry_id}" if safe else entry_id,
            }
        )

    parent = safe.rsplit("/", 1)[0] if "/" in safe else ("" if safe else None)
    out = _base_envelope(url)
    out.update(
        {
            "path": safe,
            "parent_path": parent,
            "entries": entries,
            "folder_count": sum(1 for e in entries if e["type"] == "folder"),
            "dataset_count": sum(1 for e in entries if e["type"] == "dataset"),
            "note": VINTAGE_NOTE,
        }
    )
    return out


# ---------------------------------------------------------------------------
# describe_psa_dataset
# ---------------------------------------------------------------------------


@mcp.tool(
    title="Describe a PSA OpenSTAT dataset",
    tags={"psa", "openstat", "statistics", "metadata", "philippines"},
    annotations={
        "title": "Describe a PSA OpenSTAT dataset",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
    output_schema=DESCRIBE_SCHEMA,
    timeout=60.0,
)
async def describe_psa_dataset(dataset_path: str) -> dict:
    """Read the dimensions and valid value codes of one PSA OpenSTAT dataset.

    Call this before query_psa_dataset. The query tool needs an explicit value
    code for every dimension, and those codes live here.

    Args:
        dataset_path: Relative path to a `.px` dataset, e.g.
                      "1F/FY/0011F3DF010.px". Take it from the `path` field of
                      a browse_psa_catalog dataset entry.

    Returns: dataset_path, title, dimensions (each with code, label,
    value_count, values [{code, label}], values_truncated, is_time_like),
    total_cells, max_cells_per_query, time_dimensions, source, source_url,
    license, data_retrieved_at, caveats.

    total_cells is the size of the full cube. A query must select down to
    max_cells_per_query or fewer, so pick explicit codes per dimension.
    """
    try:
        path = _dataset_path(dataset_path)
    except CatalogPathError as exc:
        return _validation_envelope(
            CATALOG_ROOT_URL, str(exc), dataset_path=dataset_path, dimensions=[]
        )

    url = _dataset_url(path)
    try:
        meta = await _dataset_meta(path)
    except PSANotFoundError as exc:
        return _validation_envelope(url, str(exc), dataset_path=path, dimensions=[])
    except PSAUpstreamError as exc:
        return _upstream_envelope(
            url,
            f"PSA OpenSTAT metadata fetch failed: {exc}",
            dataset_path=path,
            dimensions=[],
        )

    dims = _dimensions(meta)
    out = _base_envelope(url)
    out.update(
        {
            "dataset_path": path,
            "title": meta.get("title") or path,
            "dimensions": _public_dimensions(dims),
            "time_dimensions": [d["code"] for d in dims if d["is_time_like"]],
            "total_cells": _total_cells(dims),
            "max_cells_per_query": MAX_CELLS,
            "note": VINTAGE_NOTE,
        }
    )
    if any(d["values_truncated"] for d in dims):
        out["caveats"] = [f"Value lists are capped at {MAX_VALUES_LISTED} entries per dimension."]
    return out


# ---------------------------------------------------------------------------
# query_psa_dataset
# ---------------------------------------------------------------------------


def _resolve_dimension(dims: list[dict], wanted: str) -> dict | None:
    for dim in dims:
        if dim["code"] == wanted:
            return dim
    lowered = wanted.strip().lower()
    for dim in dims:
        if dim["code"].lower() == lowered or dim["label"].lower() == lowered:
            return dim
    return None


def _validate_selections(dims: list[dict], selections: Any) -> tuple[dict[str, list[str]], int]:
    """Return (code -> value codes, cell count) or raise CatalogPathError."""
    if not isinstance(selections, dict) or not selections:
        raise CatalogPathError(
            "selections must be a non-empty object mapping every dimension code "
            "to a list of value codes. Call describe_psa_dataset for the codes."
        )

    resolved: dict[str, list[str]] = {}
    for raw_code, raw_values in selections.items():
        dim = _resolve_dimension(dims, str(raw_code))
        if dim is None:
            known = [d["code"] for d in dims]
            raise CatalogPathError(f"unknown dimension {raw_code!r}. This dataset has: {known}")
        if isinstance(raw_values, str):
            raw_values = [raw_values]
        if not isinstance(raw_values, list) or not raw_values:
            raise CatalogPathError(
                f"dimension {dim['code']!r} needs a non-empty list of value codes"
            )
        # Reject on length BEFORE converting and copying. A caller-supplied list
        # of a hundred million codes would otherwise build a second list that
        # size before the cell check below ever runs.
        if len(raw_values) > MAX_CELLS:
            raise CatalogPathError(
                f"dimension {dim['code']!r} lists {len(raw_values)} values; one query "
                f"is capped at {MAX_CELLS} cells. Narrow the selection."
            )
        if dim["code"] in resolved:
            raise CatalogPathError(f"dimension {dim['code']!r} selected twice")

        valid = dim["_all_codes"]
        chosen: list[str] = []
        for value in raw_values:
            value = str(value)
            if value.strip().lower() in ("all", "*"):
                raise CatalogPathError(
                    f"dimension {dim['code']!r}: 'all' and '*' are not allowed. "
                    "PSA rejects full-cube requests. Select explicit value codes."
                )
            if value not in valid:
                sample = [v["code"] for v in dim["values"][:10]]
                raise CatalogPathError(
                    f"dimension {dim['code']!r} has no value {value!r}. Valid codes start: {sample}"
                )
            chosen.append(value)
        resolved[dim["code"]] = chosen

    missing = [d["code"] for d in dims if d["code"] not in resolved]
    if missing:
        raise CatalogPathError(
            f"every dimension needs an explicit selection; missing: {missing}. "
            "PXWeb expands an unselected dimension to all of its values."
        )

    cells = 1
    for values in resolved.values():
        cells *= len(values)
    if cells > MAX_CELLS:
        raise CatalogPathError(
            f"this selection asks for {cells} cells; the limit is {MAX_CELLS}. "
            "Narrow one dimension and retry."
        )
    return resolved, cells


def _reference_period(dims: list[dict], resolved: dict[str, list[str]]) -> str | None:
    """Human-readable vintage, read from the table's own time dimensions.

    A range only spans ONE dimension. Joining two different time dimensions
    into "first to last" would invent a period neither of them covers.
    """
    parts: list[str] = []
    for dim in dims:
        if not dim["is_time_like"]:
            continue
        by_code = dim["_all_labels"]
        chosen = resolved.get(dim["code"], [])
        if not chosen:
            continue
        # Order by the table's own declared sequence, not by the order the
        # caller happened to list the codes. Otherwise ["2","0"] reports
        # "2023 to 2018", which is backwards.
        order = {code: i for i, code in enumerate(by_code)}
        labels = [by_code.get(c, c) for c in sorted(chosen, key=lambda c: order.get(c, 0))]
        parts.append(labels[0] if len(labels) == 1 else f"{labels[0]} to {labels[-1]}")
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return "; ".join(parts)


@mcp.tool(
    title="Query a PSA OpenSTAT dataset",
    tags={"psa", "openstat", "statistics", "query", "philippines"},
    annotations={
        "title": "Query a PSA OpenSTAT dataset",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
    output_schema=QUERY_SCHEMA,
    timeout=90.0,
)
async def query_psa_dataset(
    dataset_path: str,
    selections: dict[str, list[str]],
    max_rows: int = 500,
) -> dict:
    """Run one bounded query against a PSA OpenSTAT dataset.

    Every dimension needs an explicit list of value codes from
    describe_psa_dataset. That is a hard requirement, not a convention: PXWeb
    expands an unselected dimension to all of its values, and PSA answers the
    resulting full-cube request with an HTTP 403.

    Args:
        dataset_path: Relative `.px` path, e.g. "1F/FY/0241F3DF013.px".
        selections: Dimension code -> list of value codes, covering every
                    dimension the dataset declares. "all" and "*" are rejected.
                    Example: {"Year": ["2"], "Major Island Group": ["0", "2"],
                    "Among Families/Population": ["0"]}.
        max_rows: Cap on returned rows (1-5000, default 500).

    Returns: dataset_path, title, rows (each with keys {dimension: value_code},
    labels {dimension: value_label}, and a numeric or null value),
    row_count, requested_cells, truncated, reference_period, source,
    source_url, license, data_retrieved_at, caveats, disclaimer.

    PSA writes a missing cell as "..", and those come back as null, never zero.
    A selection error returns validation_error: true; an OpenSTAT outage
    returns upstream_error: true.
    """
    try:
        path = _dataset_path(dataset_path)
    except CatalogPathError as exc:
        return _validation_envelope(
            CATALOG_ROOT_URL, str(exc), dataset_path=dataset_path, rows=[], row_count=0
        )

    url = _dataset_url(path)

    if isinstance(max_rows, bool) or not isinstance(max_rows, int):
        return _validation_envelope(
            url,
            f"max_rows must be a whole number, got {type(max_rows).__name__}",
            dataset_path=path,
            rows=[],
            row_count=0,
        )
    rows_cap = max_rows
    rows_cap = max(MIN_ROWS, min(rows_cap, MAX_ROWS_CEILING))

    try:
        meta = await _dataset_meta(path)
    except PSANotFoundError as exc:
        return _validation_envelope(url, str(exc), dataset_path=path, rows=[], row_count=0)
    except PSAUpstreamError as exc:
        return _upstream_envelope(
            url,
            f"PSA OpenSTAT metadata fetch failed: {exc}",
            dataset_path=path,
            rows=[],
            row_count=0,
        )

    dims = _dimensions(meta)
    try:
        resolved, cells = _validate_selections(dims, selections)
    except CatalogPathError as exc:
        return _validation_envelope(url, str(exc), dataset_path=path, rows=[], row_count=0)

    query = {
        "query": [
            {"code": code, "selection": {"filter": "item", "values": values}}
            for code, values in resolved.items()
        ],
        "response": {"format": "json"},
    }
    try:
        payload = await _post_json_or_raise(url, query)
    except PSAUpstreamError as exc:
        return _upstream_envelope(
            url,
            f"PSA OpenSTAT query failed: {exc}",
            dataset_path=path,
            rows=[],
            row_count=0,
        )

    if "data" not in payload or not isinstance(payload.get("data"), list):
        return _upstream_envelope(
            url,
            "PSA returned a response with no `data` array. That is a malformed "
            "reply, not an empty result.",
            dataset_path=path,
            rows=[],
            row_count=0,
        )

    # C. Labels come from every declared value, not from the display-capped
    # list, so a code past MAX_VALUES_LISTED still resolves to its label and
    # still contributes its reference period.
    label_lookup = {dim["code"]: dim["_all_labels"] for dim in dims}
    columns = _key_columns(payload)
    rows: list[dict] = []
    misaligned = 0
    unparseable = 0
    for record in payload.get("data", []):
        if not isinstance(record, dict):
            misaligned += 1
            continue
        key = record.get("key") or []
        if not isinstance(key, (list, tuple)):
            misaligned += 1
            key = []
        values = record.get("values")
        if len(key) != len(columns):
            misaligned += 1
        keys = {columns[i]: key[i] for i in range(min(len(columns), len(key)))}
        if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
            # PXWeb always sends a list here. Anything else is drift, and
            # indexing a string would hand back its first character as data.
            misaligned += 1
            raw = None
        elif not values:
            # An empty array is a row with no cell at all, which is drift, not
            # the '..' PSA writes for a value it does not publish.
            misaligned += 1
            raw = None
        else:
            raw = values[0]
        parsed = _to_float(raw)
        if parsed is None and raw is not None and str(raw).strip() not in PSA_MISSING_MARKERS:
            unparseable += 1
        rows.append(
            {
                "keys": keys,
                "labels": {
                    code: label_lookup.get(code, {}).get(value, value)
                    for code, value in keys.items()
                },
                "value": parsed,
            }
        )

    total_rows = len(rows)
    truncated = total_rows > rows_cap
    out = _base_envelope(url)
    out.update(
        {
            "dataset_path": path,
            "title": meta.get("title") or path,
            "rows": rows[:rows_cap],
            "row_count": min(total_rows, rows_cap),
            "total_rows_available": total_rows,
            "requested_cells": cells,
            "truncated": truncated,
            "reference_period": _reference_period(dims, resolved),
            "note": VINTAGE_NOTE,
            "disclaimer": DISCLAIMER,
        }
    )
    caveats: list[str] = []
    if truncated:
        caveats.append(
            f"Returned {rows_cap} of {total_rows} rows. Raise max_rows or narrow the selection."
        )
    if any(row["value"] is None for row in out["rows"]):
        caveats.append("Some cells are null: PSA publishes '..' for a missing value.")
    if unparseable:
        caveats.append(
            f"{unparseable} cell(s) held a value this server could not read as a "
            "number and reported as null. That is a parse failure, not a PSA "
            "missing-value marker."
        )
    if misaligned:
        caveats.append(
            f"{misaligned} row(s) carried a key count that does not match the "
            f"{len(columns)} dimension columns; those rows are partially mapped."
        )
    out["caveats"] = caveats
    return out


# ---------------------------------------------------------------------------
# search_psa_catalog
# ---------------------------------------------------------------------------

MAX_SEARCH_LIMIT = 100
MIN_SEARCH_LIMIT = 1

# One walk of the whole tree at a time. Without it, two concurrent cold
# searches both miss the index cache and each pace their own few hundred
# browse calls behind the rate limiter, for the same result.
_INDEX_LOCK = asyncio.Lock()
_INDEX_CACHE_KEY = "index"


async def _walk_catalog(path: str) -> list[dict]:
    """Recursively collect every {path, title} dataset entry under `path`.

    Reuses _browse, the same per-level listing browse_psa_catalog calls, and
    the same dataset-or-folder test it applies to each entry. Sibling folders
    walk concurrently, the fan-out pattern get_area_profile already uses, so
    the rate limiter still paces every request but the round trips overlap.

    Raises PSAUpstreamError if any subtree fails to load. A partial index
    would let a real table quietly look like a search miss.
    """
    entries = await _browse(path)
    matches: list[dict] = []
    subfolders: list[str] = []
    for entry in entries:
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            continue
        title = str(entry.get("text") or entry_id)
        child_path = f"{path}/{entry_id}" if path else entry_id
        is_dataset = entry.get("type") == "t" or entry_id.lower().endswith(".px")
        if is_dataset:
            matches.append({"path": child_path, "title": title})
        else:
            subfolders.append(child_path)
    if subfolders:
        for sub_matches in await asyncio.gather(*(_walk_catalog(p) for p in subfolders)):
            matches.extend(sub_matches)
    return matches


async def _catalog_index() -> list[dict]:
    """The flattened {path, title} list for every dataset in the PSA catalog.

    Walking the whole ~2,900-table tree paces one request at a time behind the
    rate limiter, so a cold call can take minutes. The flattened list caches
    24h, the same TTL as psa_discovery, so a later search answers from memory.
    """
    cache = CACHES["psa_catalog_index"]
    if _INDEX_CACHE_KEY in cache:
        return cache[_INDEX_CACHE_KEY]
    async with _INDEX_LOCK:
        if _INDEX_CACHE_KEY in cache:
            return cache[_INDEX_CACHE_KEY]
        index = await _walk_catalog("")
        cache[_INDEX_CACHE_KEY] = index
        return index


@mcp.tool(
    title="Search the PSA OpenSTAT catalog",
    tags={"psa", "openstat", "statistics", "search", "catalog", "philippines"},
    annotations={
        "title": "Search the PSA OpenSTAT catalog",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
    output_schema=SEARCH_SCHEMA,
    timeout=300.0,
)
async def search_psa_catalog(keyword: str, limit: int = 20) -> dict:
    """Find a PSA OpenSTAT dataset by keyword, without browsing level by level.

    Matches a case-insensitive substring against every dataset title and path
    in the catalog. The first call after a cold start walks the whole
    ~2,900-table tree and can take a few minutes; the flattened index then
    caches for 24 hours, so a later search answers from memory.

    Args:
        keyword: Case-insensitive substring to match against a dataset title
                 or path, e.g. "fertility", "poverty incidence", "CPI".
        limit: Maximum number of matches to return (1-100, default 20).

    Returns: keyword, matches (each with path and title), match_count,
    total_available, limit, data_status, source, source_url, license,
    data_retrieved_at, caveats.

    A caller mistake (an empty keyword) returns validation_error: true. An
    OpenSTAT outage during the catalog walk returns upstream_error: true.
    Neither is cached.
    """
    url = CATALOG_ROOT_URL

    if not isinstance(keyword, str) or not keyword.strip():
        return failure_result(
            SOURCE_NAME,
            url,
            "keyword must be a non-empty string.",
            license=PSA_LICENSE,
            validation_error=True,
            keyword=keyword,
            matches=[],
            match_count=0,
        )
    if isinstance(limit, bool) or not isinstance(limit, int):
        return failure_result(
            SOURCE_NAME,
            url,
            f"limit must be a whole number, got {type(limit).__name__}",
            license=PSA_LICENSE,
            validation_error=True,
            keyword=keyword,
            matches=[],
            match_count=0,
        )
    bounded_limit = max(MIN_SEARCH_LIMIT, min(limit, MAX_SEARCH_LIMIT))

    try:
        index = await _catalog_index()
    except PSAUpstreamError as exc:
        return failure_result(
            SOURCE_NAME,
            url,
            f"PSA OpenSTAT catalog walk failed: {exc}",
            license=PSA_LICENSE,
            keyword=keyword,
            matches=[],
            match_count=0,
        )

    want = keyword.strip().lower()
    hits = [e for e in index if want in e["title"].lower() or want in e["path"].lower()]
    matches = hits[:bounded_limit]

    caveats: list[str] = []
    if len(hits) > bounded_limit:
        caveats.append(
            f"Returned {bounded_limit} of {len(hits)} matches. Raise limit or narrow the keyword."
        )

    return {
        "keyword": keyword,
        "matches": matches,
        "match_count": len(matches),
        "total_available": len(hits),
        "limit": bounded_limit,
        "data_status": DATA_STATUS_SUCCESS,
        "upstream_error": False,
        "validation_error": False,
        "caveats": caveats,
        "source": SOURCE_NAME,
        "source_url": url,
        "license": PSA_LICENSE,
        "data_retrieved_at": _now().isoformat(),
    }
