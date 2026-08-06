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

import re
from typing import Any

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.sources.psa import (
    PSA_API_BASE,
    PSA_LICENSE,
    PSAUpstreamError,
    _browse,
    _get_json_or_raise,
    _key_columns,
    _now,
    _post_json_or_raise,
    _to_float,
)
from ph_civic_data_mcp.utils.cache import CACHES, cache_key

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


async def _dataset_meta(path: str) -> dict:
    """Fetch and cache `.px` metadata. Successes only; errors stay retryable."""
    key = cache_key({"psa_meta": path})
    cache = CACHES["psa_browse"]
    if key in cache:
        return cache[key]
    meta = await _get_json_or_raise(_dataset_url(path))
    if not isinstance(meta, dict) or not meta.get("variables"):
        raise PSAUpstreamError(f"PSA dataset {path} returned no variable metadata")
    cache[key] = meta
    return meta


def _is_time_like(var: dict) -> bool:
    code = (var.get("code") or "").lower()
    text = (var.get("text") or "").lower()
    return bool(var.get("time")) or "year" in code or "period" in code or text == "year"


def _dimensions(meta: dict) -> list[dict]:
    dims: list[dict] = []
    for var in meta.get("variables", []):
        values = list(var.get("values", []))
        texts = list(var.get("valueTexts", []))
        listed = [
            {"code": v, "label": texts[i] if i < len(texts) else v}
            for i, v in enumerate(values[:MAX_VALUES_LISTED])
        ]
        dims.append(
            {
                "code": var.get("code") or var.get("text") or "",
                "label": var.get("text") or var.get("code") or "",
                "value_count": len(values),
                "values": listed,
                "values_truncated": len(values) > MAX_VALUES_LISTED,
                "is_time_like": _is_time_like(var),
            }
        )
    return dims


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
            "dimensions": dims,
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
        valid = {v["code"] for v in dim["values"]}
        chosen: list[str] = []
        for value in raw_values:
            value = str(value)
            if value.strip().lower() in ("all", "*"):
                raise CatalogPathError(
                    f"dimension {dim['code']!r}: 'all' and '*' are not allowed. "
                    "PSA rejects full-cube requests. Select explicit value codes."
                )
            if dim["values_truncated"]:
                # Cannot prove membership against a truncated list; accept and
                # let PXWeb reject an unknown code.
                chosen.append(value)
                continue
            if value not in valid:
                sample = [v["code"] for v in dim["values"][:10]]
                raise CatalogPathError(
                    f"dimension {dim['code']!r} has no value {value!r}. Valid codes start: {sample}"
                )
            chosen.append(value)
        if dim["code"] in resolved:
            raise CatalogPathError(f"dimension {dim['code']!r} selected twice")
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
    labels: list[str] = []
    for dim in dims:
        if not dim["is_time_like"]:
            continue
        by_code = {v["code"]: v["label"] for v in dim["values"]}
        labels.extend(by_code.get(c, c) for c in resolved.get(dim["code"], []))
    if not labels:
        return None
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} to {labels[-1]}"


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

    try:
        rows_cap = int(max_rows)
    except (TypeError, ValueError):
        return _validation_envelope(
            url, "max_rows must be an integer", dataset_path=path, rows=[], row_count=0
        )
    rows_cap = max(MIN_ROWS, min(rows_cap, MAX_ROWS_CEILING))

    try:
        meta = await _dataset_meta(path)
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

    label_lookup = {dim["code"]: {v["code"]: v["label"] for v in dim["values"]} for dim in dims}
    columns = _key_columns(payload)
    rows: list[dict] = []
    for record in payload.get("data", []):
        key = record.get("key", [])
        values = record.get("values", [])
        keys = {columns[i]: key[i] for i in range(min(len(columns), len(key)))}
        rows.append(
            {
                "keys": keys,
                "labels": {
                    code: label_lookup.get(code, {}).get(value, value)
                    for code, value in keys.items()
                },
                "value": _to_float(values[0] if values else None),
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
    out["caveats"] = caveats
    return out
