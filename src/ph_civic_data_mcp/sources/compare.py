"""Side-by-side comparison of civic indicators across two to five places.

`compare_areas` calls `get_area_profile` for every place, lines up the
requested metrics into one row per place, and flags when the rows are not
truly comparable (different data vintages, different admin levels). It
builds on the same fan-out pattern `autostitch.py` uses, and reuses that
module's disclaimer text so the two tools never drift apart.
"""

from __future__ import annotations

import asyncio
import csv
import io
from datetime import datetime, timezone

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.sources.autostitch import PROFILE_DISCLAIMER, get_area_profile
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_INDETERMINATE,
    DATA_STATUS_INVALID_REQUEST,
    DATA_STATUS_SUCCESS,
    DATA_STATUS_UNAVAILABLE,
    failure_result,
)

# The metric allowlist. A request must pick a subset of these; anything else
# is rejected before any network call. 5 locations x 8 metrics is the hard
# ceiling this tool can ever return, so no extra truncation logic is needed.
COMPARE_METRICS: tuple[str, ...] = (
    "population",
    "population_year",
    "poverty_incidence_pct",
    "poverty_year",
    "headline_inflation_pct",
    "employment_rate_pct",
    "infra_notice_count",
    "earthquake_risk_level",
)

MIN_LOCATIONS = 2
MAX_LOCATIONS = 5

SOURCE = "PSA, PSGC, PhilGEPS, PHIVOLCS via get_area_profile"
SOURCE_URL = (
    "https://psgc.gitlab.io/api/, https://openstat.psa.gov.ph/PXWeb/api/v1/en/, "
    "https://www.philgeps.gov.ph/, https://earthquake.phivolcs.dost.gov.ph/, "
    "https://bagong.pagasa.dost.gov.ph/"
)
LICENSE = "Public - PSA OpenSTAT, PSGC, PhilGEPS, PHIVOLCS, PAGASA"

# The two metrics that need a vintage check before rows count as comparable.
_VINTAGE_METRICS = ("population", "poverty_incidence_pct")

# Which get_area_profile block each metric reads. The health rollup checks
# these blocks, so a failed PSA fetch inside a resolved profile cannot hide
# behind `matched: true`.
_METRIC_BLOCK: dict[str, str] = {
    "population": "population",
    "population_year": "population",
    "poverty_incidence_pct": "poverty",
    "poverty_year": "poverty",
    "headline_inflation_pct": "inflation",
    "employment_rate_pct": "labor",
    "infra_notice_count": "infra",
    "earthquake_risk_level": "hazard",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_metric(profile: dict, metric: str) -> object:
    """Read one compare metric from a get_area_profile response.

    Returns None when the profile block that carries this metric never
    resolved. That is the correct output, not an error: the row still shows
    every other metric it could supply.
    """
    demographics = profile.get("demographics") or {}
    economy = profile.get("economy") or {}
    correlations = profile.get("correlations") or {}
    hazard = profile.get("hazard") or {}
    if metric == "population":
        return demographics.get("population")
    if metric == "population_year":
        return demographics.get("population_year")
    if metric == "poverty_incidence_pct":
        return demographics.get("poverty_incidence_pct")
    if metric == "poverty_year":
        return demographics.get("poverty_reference_year")
    if metric == "headline_inflation_pct":
        return economy.get("headline_inflation_pct")
    if metric == "employment_rate_pct":
        return economy.get("employment_rate_pct")
    if metric == "infra_notice_count":
        return correlations.get("infra_notice_count")
    if metric == "earthquake_risk_level":
        return hazard.get("earthquake_risk_level")
    return None


def _vintage_value(profile: dict, metric: str) -> object:
    """Read the vintage year for a compared metric, straight from the block.

    Read even when the caller did not ask for the year column, because the
    comparability check needs it regardless of which columns show.
    """
    demographics = profile.get("demographics") or {}
    if metric == "population":
        return demographics.get("population_year")
    if metric == "poverty_incidence_pct":
        return demographics.get("poverty_reference_year")
    return None


def _validate_request(locations: object, metrics: object, format: object) -> str | None:
    """Check the request before any network call. Return the problem, or None."""
    if not isinstance(locations, list) or not (MIN_LOCATIONS <= len(locations) <= MAX_LOCATIONS):
        got = len(locations) if isinstance(locations, list) else type(locations).__name__
        return (
            f"locations must be a list of {MIN_LOCATIONS} to {MAX_LOCATIONS} place names, got {got}"
        )
    for loc in locations:
        if not isinstance(loc, str) or not loc.strip():
            return "every entry in locations must be a non-empty string"
    if metrics is not None:
        if not isinstance(metrics, list) or not metrics:
            return "metrics must be a non-empty list when given"
        bad = [m for m in metrics if m not in COMPARE_METRICS]
        if bad:
            return f"unknown metric(s) {bad}; pick from {list(COMPARE_METRICS)}"
    if format not in ("json", "csv"):
        return f"format must be 'json' or 'csv', got {format!r}"
    return None


def _rows_to_csv(rows: list[dict], metrics: list[str]) -> str:
    fieldnames = ["location", "resolved_name", "psgc_code", "level", *metrics]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key) for key in fieldnames})
    return buf.getvalue()


@mcp.tool(
    title="Compare civic indicators across places",
    tags={"civic", "composite", "philippines", "compare"},
    annotations={
        "title": "Compare civic indicators across places",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def compare_areas(
    locations: list[str],
    metrics: list[str] | None = None,
    format: str = "json",
) -> dict:
    """Compare civic indicators for two to five Philippine places, side by side.

    Example: compare_areas(["Cebu City", "Davao City"], metrics=["population",
    "poverty_incidence_pct"]).

    Calls get_area_profile for each place and builds one row per place, so an
    agent does not have to call the profile tool once per place and merge the
    results itself. locations needs 2 to 5 entries. metrics, when given, must
    come from a fixed allowlist of eight names; it defaults to all eight.
    format is "json" or "csv"; "csv" adds a CSV string under `export`.

    Args:
        locations: 2 to 5 place names, e.g. ["Cebu City", "Davao City"].
        metrics: Names to compare, from population, population_year,
                 poverty_incidence_pct, poverty_year, headline_inflation_pct,
                 employment_rate_pct, infra_notice_count,
                 earthquake_risk_level. Defaults to all eight.
        format: "json" (default) or "csv".

    Returns: rows (one per place, with resolved_name, psgc_code, level, and
    one column per metric), the effective metrics list, comparable, a blocks
    dict per place, caveats, data_status, upstream_error, validation_error,
    source, source_url, license, disclaimer, data_retrieved_at. csv format
    also returns export, a CSV string with a header row.

    A rejected request (wrong location count, an unknown metric, a bad
    format) never calls get_area_profile and returns validation_error: true.
    Rows for a place that never resolved still appear, with resolved_name
    set to None, and data_status becomes "indeterminate" or "unavailable".
    """
    problem = _validate_request(locations, metrics, format)
    if problem is not None:
        return failure_result(
            SOURCE,
            SOURCE_URL,
            problem,
            license=LICENSE,
            data_status=DATA_STATUS_INVALID_REQUEST,
            comparable=None,
            metrics=list(metrics) if isinstance(metrics, list) else [],
            rows=[],
            blocks={},
        )

    effective_metrics = list(metrics) if metrics is not None else list(COMPARE_METRICS)

    gathered = await asyncio.gather(
        *(get_area_profile(loc) for loc in locations), return_exceptions=True
    )

    rows: list[dict] = []
    blocks: dict[str, object] = {}
    caveats: list[str] = []
    vintages: dict[str, dict[str, object]] = {}
    resolved_count = 0

    for location, result in zip(locations, gathered):
        if isinstance(result, BaseException):
            caveats.append(
                f"{location}: get_area_profile failed: {type(result).__name__}: {result}"
            )
            blocks[location] = {"profile": DATA_STATUS_UNAVAILABLE}
            row = {"location": location, "resolved_name": None, "psgc_code": None, "level": None}
            for metric in effective_metrics:
                row[metric] = None
            rows.append(row)
            continue

        resolved = result.get("resolved") or {}
        matched = bool(resolved.get("matched"))
        blocks[location] = result.get("blocks") or {}
        row = {
            "location": location,
            "resolved_name": resolved.get("name"),
            "psgc_code": resolved.get("psgc_code"),
            "level": resolved.get("level"),
        }
        for metric in effective_metrics:
            row[metric] = _extract_metric(result, metric)
        rows.append(row)
        vintages[location] = {metric: _vintage_value(result, metric) for metric in _VINTAGE_METRICS}

        if matched:
            resolved_count += 1
        else:
            for c in result.get("caveats") or [f"'{location}' did not resolve to a place"]:
                caveats.append(f"{location}: {c}")

    comparable = True
    for metric in _VINTAGE_METRICS:
        if metric not in effective_metrics:
            continue
        years = {str(v[metric]) for v in vintages.values() if v.get(metric) is not None}
        if len(years) > 1:
            comparable = False
            caveats.append(f"{metric} vintage differs across locations: {sorted(years)}")

    levels = {row["level"] for row in rows if row["level"] is not None}
    if len(levels) > 1:
        caveats.append(
            f"locations differ in admin level: {sorted(levels)}. "
            "Comparing across scales, such as a city next to a region, "
            "is a scale mismatch."
        )

    # Codex cross-model finding on the v0.7.0 diff: a resolved profile can
    # still carry a failed block (PSA down, population None). Health must
    # read the blocks a requested metric depends on, not only resolution.
    degraded_blocks: set[str] = set()
    for location, result in zip(locations, gathered):
        if isinstance(result, BaseException):
            continue
        result_blocks = result.get("blocks") or {}
        for metric in effective_metrics:
            block = _METRIC_BLOCK.get(metric)
            status = result_blocks.get(block) if block else None
            if status in (DATA_STATUS_UNAVAILABLE, DATA_STATUS_INDETERMINATE):
                degraded_blocks.add(f"{location}:{block}")
    if degraded_blocks:
        caveats.append(
            "one or more requested metrics come from a block that failed upstream: "
            + ", ".join(sorted(degraded_blocks))
        )
        for metric in _VINTAGE_METRICS:
            if metric in effective_metrics and any(
                row[metric] is None and row["resolved_name"] is not None for row in rows
            ):
                comparable = False

    if resolved_count == len(locations) and not degraded_blocks:
        data_status = DATA_STATUS_SUCCESS
    elif resolved_count == 0:
        data_status = DATA_STATUS_UNAVAILABLE
    else:
        data_status = DATA_STATUS_INDETERMINATE

    out = {
        "rows": rows,
        "metrics": effective_metrics,
        "comparable": comparable,
        "blocks": blocks,
        "caveats": caveats,
        "data_status": data_status,
        "upstream_error": data_status in (DATA_STATUS_UNAVAILABLE, DATA_STATUS_INDETERMINATE),
        "validation_error": False,
        "source": SOURCE,
        "source_url": SOURCE_URL,
        "license": LICENSE,
        "disclaimer": PROFILE_DISCLAIMER,
        "data_retrieved_at": _now().isoformat(),
    }
    if format == "csv":
        out["export"] = _rows_to_csv(rows, effective_metrics)
    return out
