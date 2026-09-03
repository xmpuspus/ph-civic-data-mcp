"""Auto-stitch context layer — one resolved place, one correlated answer.

`get_area_profile` is the differentiator: instead of the calling agent
orchestrating resolve_ph_location -> population -> poverty -> inflation ->
labor -> infra -> hazard -> weather (≈8 round-trips, and the agent has to know
that population needs a region while infra needs a province and hazard is
PHIVOLCS+PAGASA), it issues ONE call. We resolve the PSGC spine once, fan out
in parallel, and return a single envelope that also carries the cross-source
normalization the agent would otherwise have to derive itself (infra notices
per 100k population, economy figures tagged against the national reference).

This is the same asyncio.gather composition pattern proven in
`cross_source.py`; kept in a separate module so it has a dedicated test file
and a clean catalog/registration story. No existing tool is modified.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.sources.cross_source import assess_area_risk
from ph_civic_data_mcp.sources.infra import infra_sample_coverage, search_infra_projects
from ph_civic_data_mcp.sources.pagasa import get_weather_forecast
from ph_civic_data_mcp.sources.psa import (
    get_inflation_stats,
    get_labor_stats,
    get_population_stats,
    get_poverty_stats,
)
from ph_civic_data_mcp.sources.psgc import get_location_hierarchy, resolve_ph_location
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_INDETERMINATE,
    DATA_STATUS_SUCCESS,
    DATA_STATUS_UNAVAILABLE,
    is_failure,
)

# The only statuses that mean "a source was actually unreachable or its
# answer could not be trusted". A block a sibling tool rejected as an
# invalid request, or answered as genuinely empty, is neither, and must not
# flip the profile's top-level upstream_error.
_OUTAGE_STATUSES = frozenset({DATA_STATUS_UNAVAILABLE, DATA_STATUS_INDETERMINATE})

PROFILE_DISCLAIMER = (
    "Statistical indicators derived from public data. Patterns may have "
    "legitimate explanations. Each block carries its own reference period; "
    "PSA series are published with a lag and are not real-time."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


BLOCK_SKIPPED = "skipped"


def _unwrap(result: object, caveats: list[str], label: str) -> tuple[dict | list | None, str]:
    """Normalize a gathered result into (data, status).

    Both a raised exception and a returned failure envelope land in `caveats`
    with the real error text, so a block that reads null downstream always has
    a caveat naming why. Before v0.6.1 only the exception branch existed: a
    sibling that returned `{upstream_error: true}` passed through as data, and
    the profile showed `population: null` beside an empty caveat list.

    The block status is the child's own `data_status` when it set one, not a
    blanket "unavailable". Codex cross-model finding on the v0.6.1 diff: an
    earlier version collapsed a sibling's `validation_error` (a mismatched
    region name, never an outage) into the same status as a real outage, so
    a profile flagged its top-level `upstream_error` for a call that never
    left the process.

    A child that reports `data_status="empty"` (a genuine no-data answer, not
    an outage) does not set `upstream_error` or `validation_error`, so
    `is_failure` alone missed it and the block read as "success" with no
    caveat. Every non-success `data_status`, whatever its own name, now
    propagates as-is and always lifts the child's caveats.
    """
    if isinstance(result, BaseException):
        caveats.append(f"{label} failed: {type(result).__name__}: {result}")
        return None, DATA_STATUS_UNAVAILABLE
    status = result.get("data_status") if isinstance(result, dict) else None  # type: ignore[union-attr]
    if status and status != DATA_STATUS_SUCCESS:
        detail = result.get("caveats") or ["upstream error with no detail"]  # type: ignore[union-attr]
        for c in detail:
            caveats.append(f"{label}: {c}")
        return None, status
    if status is None and is_failure(result):
        detail = result.get("caveats") or ["upstream error with no detail"]  # type: ignore[union-attr]
        for c in detail:
            caveats.append(f"{label}: {c}")
        return None, DATA_STATUS_UNAVAILABLE
    return result, DATA_STATUS_SUCCESS  # type: ignore[return-value]


@mcp.tool(
    title="One-call civic profile for a place",
    tags={"civic", "composite", "philippines", "profile"},
    annotations={
        "title": "One-call civic profile for a place",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_area_profile(location: str) -> dict:
    """One-call correlated civic profile for a Philippine location.

    Resolves the place once to its PSA Standard Geographic Code, then composes
    demographics (population, poverty), economy (regional inflation, national
    labor), procurement activity, multi-hazard risk, and the short-range
    weather outlook — in a single agent turn instead of eight. Adds derived
    cross-source context (e.g. infrastructure notices per 100k residents) so
    the caller does not have to normalize raw counts itself. Examples:

      get_area_profile("Cebu City")   city-level demographics, hazard, weather
      get_area_profile("NCR")         region-level, no province in the chain
      get_area_profile("Tacloban")    city under a resolved province

    On failure: each block (population, poverty, inflation, labor, hazard,
    weather, infra, resolve) gets its own status in blocks. A failed sibling
    appears there and in caveats, never as a silent null. The top-level
    upstream_error is true only when a block is genuinely unreachable, not
    when a sibling rejected an argument or returned a real empty answer.

    Args:
        location: Municipality, city, province, or region name.
                  e.g. "Leyte", "Cebu City", "Davao Region", "NCR".

    Returns: resolved location, demographics (population and poverty, plus
    each figure's own geography, PSGC code, census, and reference-date
    provenance), economy, procurement, hazard, weather, national_reference
    (the country's population and poverty for comparison), derived
    correlations (infra_notices_per_100k_population is null and carries an
    infra_coverage_caveat when the PhilGEPS sample is nonzero but below the
    500-notice threshold for a population-representative rate), per-block
    reference periods, caveats listing any upstream that failed, and the
    public-data disclaimer.

    The first call in a process pays the PSA rate limit for every block at
    once, so it can take about 15 seconds. A later call for any place reuses
    the cached national reference and PSA discovery, so it is fast.
    """
    retrieved_at = _now()
    caveats: list[str] = []
    resolve_status = DATA_STATUS_SUCCESS
    hierarchy_status: str | None = None

    resolved = await resolve_ph_location(location)
    if is_failure(resolved):
        # A resolver outage must never read as "no such place". Codex
        # cross-model finding on the v0.6.1 diff: the old code only checked
        # `matched`, so a PSGC API outage looked identical to an unknown name.
        for c in resolved.get("caveats") or ["PSGC resolver unavailable"]:
            caveats.append(f"PSGC resolve: {c}")
        resolve_status = DATA_STATUS_UNAVAILABLE
    matched = bool(resolved.get("matched"))
    region_name: str | None = None
    province_name: str | None = None
    locality_name: str | None = resolved.get("name")
    chain: list[dict] = []

    if matched and resolved.get("psgc_code"):
        if (resolved.get("level") or "") == "region":
            region_name = resolved.get("name")
        hierarchy = await get_location_hierarchy(resolved["psgc_code"])
        if is_failure(hierarchy):
            for c in hierarchy.get("caveats") or ["PSGC hierarchy unavailable"]:
                caveats.append(f"PSGC hierarchy: {c}")
            hierarchy_status = DATA_STATUS_UNAVAILABLE
        else:
            hierarchy_status = DATA_STATUS_SUCCESS
        chain = hierarchy.get("chain") or []
        for node in chain:
            lvl = node.get("level")
            if lvl == "region":
                region_name = node.get("name")
            elif lvl == "province":
                province_name = node.get("name")
            elif lvl in ("city", "municipality"):
                locality_name = node.get("name")
    elif not matched and resolve_status == DATA_STATUS_SUCCESS:
        caveats.append(
            f"'{location}' did not resolve to a PSGC record; PSA statistics "
            "(which key on region) are omitted. Hazard and weather use the raw "
            "place string."
        )

    # Fan out. PSA stats need a resolved region; hazard/weather work on the raw
    # string, so they always run even when PSGC resolution failed.
    tasks: dict[str, asyncio.Task] = {
        "hazard": asyncio.create_task(assess_area_risk(location)),
        "weather": asyncio.create_task(get_weather_forecast(location, days=3)),
        "labor": asyncio.create_task(get_labor_stats()),
        # National figures let a caller read a place's number against the
        # country's without a second tool call. Fetched here, in the same
        # gather, so national_reference costs no extra latency.
        "national_population": asyncio.create_task(get_population_stats()),
        "national_poverty": asyncio.create_task(get_poverty_stats()),
    }

    # A resolved city or province must report its own numbers, not the
    # containing region's total. Before this fix, Tacloban, a city, showed
    # Region VIII's multi-million total. Population now uses the PSGC code
    # directly (v0.6.1 added that parameter to get_population_stats). Poverty
    # has no psgc_code parameter, so it falls back to the containing
    # province, the most specific level its PXWeb table carries. A
    # region-level query, or a match with no province node in the chain,
    # keeps the region-name path for both.
    place_more_specific_than_region = matched and (resolved.get("level") or "") not in (
        "",
        "region",
    )
    if place_more_specific_than_region and resolved.get("psgc_code"):
        tasks["population"] = asyncio.create_task(
            get_population_stats(psgc_code=resolved["psgc_code"])
        )
    elif region_name:
        tasks["population"] = asyncio.create_task(get_population_stats(region=region_name))

    poverty_area = (province_name if place_more_specific_than_region else None) or region_name
    if poverty_area:
        tasks["poverty"] = asyncio.create_task(get_poverty_stats(region=poverty_area))

    # Regional CPI stays on region_name. PSA's public contract for
    # get_inflation_stats is region-level: "area: Region or Philippines". A
    # few designated price-monitoring cities, such as City of Tacloban,
    # appear in the underlying table too. Matching those select cities would
    # be inconsistent, not a general fix.
    if region_name:
        tasks["inflation"] = asyncio.create_task(get_inflation_stats(area=region_name))

    # An unresolved place has no province, region, or verified locality name,
    # so search_infra_projects(province=None, region=None) would return the
    # national notice listing and the profile would report it as this
    # place's own count. Skip the search instead of guessing.
    if matched:
        infra_filter = province_name or locality_name
        tasks["infra"] = asyncio.create_task(
            search_infra_projects(province=infra_filter, region=region_name, limit=100)
        )
    else:
        caveats.append("Infra notice search needs a resolved place; procurement block skipped.")

    gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
    results = dict(zip(tasks.keys(), gathered))
    blocks: dict[str, str] = {"resolve": resolve_status}
    if hierarchy_status is not None:
        blocks["hierarchy"] = hierarchy_status

    def _take(name: str, label: str) -> dict | list | None:
        if name not in results:
            blocks[name] = BLOCK_SKIPPED
            return None
        data, status = _unwrap(results[name], caveats, label)
        blocks[name] = status
        return data

    population = _take("population", "PSA population") or {}
    poverty = _take("poverty", "PSA poverty") or {}
    inflation = _take("inflation", "PSA inflation") or {}
    labor = _take("labor", "PSA labor") or {}
    hazard = _take("hazard", "Hazard assessment") or {}
    weather = _take("weather", "Weather forecast") or {}
    national_population = _take("national_population", "PSA national population") or {}
    national_poverty = _take("national_poverty", "PSA national poverty") or {}
    infra = _take("infra", "PhilGEPS infra search")
    if not isinstance(infra, list):
        infra = None
    if not isinstance(population, dict):
        population = {}
    if not isinstance(national_population, dict):
        national_population = {}
    if not isinstance(national_poverty, dict):
        national_poverty = {}

    pop_value = population.get("population")
    infra_count: int | None = len(infra) if infra is not None else None
    # Zero notices is a genuine negative, not an undersized sample of a real
    # signal, so it takes no coverage caveat and keeps its plain 0.0 rate.
    infra_coverage = infra_sample_coverage(infra_count) if infra_count else None
    infra_sufficient = bool(infra_coverage and infra_coverage["sufficient_for_per_capita"])
    infra_per_100k: float | None = None
    if (infra_sufficient or infra_count == 0) and isinstance(pop_value, int) and pop_value > 0:
        infra_per_100k = round(infra_count / pop_value * 100_000, 2)

    correlations = {
        "infra_notice_count": infra_count,
        "infra_notices_per_100k_population": infra_per_100k,
        "infra_sample_size": infra_count,
        "note": (
            "infra_notices_per_100k_population normalizes the PhilGEPS notice "
            "count by the PSA regional population so the figure is comparable "
            "across regions. PhilGEPS notice counts reflect the latest ~100 "
            "notices window, not a complete regional census of projects."
        ),
    }
    if infra_coverage is not None and not infra_sufficient:
        correlations["infra_coverage_caveat"] = infra_coverage["coverage_caveat"]
        caveats.append(infra_coverage["coverage_caveat"])

    # national_reference reads the place's own numbers against the country's.
    # A share or a gap is only meaningful when both sides come from the same
    # census or survey round, so a vintage mismatch withholds the figure and
    # names both years instead of comparing numbers that do not line up.
    national_pop_value = national_population.get("population")
    national_pop_year = national_population.get("year")
    national_poverty_pct = national_poverty.get("poverty_incidence_pct")
    national_poverty_year = national_poverty.get("reference_year")

    population_share_pct: float | None = None
    place_pop_year = population.get("year")
    if (
        isinstance(pop_value, (int, float))
        and isinstance(national_pop_value, (int, float))
        and national_pop_value
    ):
        if place_pop_year is None or national_pop_year is None:
            caveats.append(
                "national_reference: population vintage unknown (place "
                f"{place_pop_year}, national {national_pop_year}); "
                "population_share_pct withheld."
            )
        elif place_pop_year == national_pop_year:
            population_share_pct = round(pop_value / national_pop_value * 100, 2)
        else:
            caveats.append(
                "national_reference: population vintages differ (place "
                f"{place_pop_year}, national {national_pop_year}); "
                "population_share_pct withheld."
            )

    place_poverty_pct = poverty.get("poverty_incidence_pct")
    place_poverty_year = poverty.get("reference_year")
    poverty_gap_pct_points: float | None = None
    if isinstance(place_poverty_pct, (int, float)) and isinstance(
        national_poverty_pct, (int, float)
    ):
        if place_poverty_year is None or national_poverty_year is None:
            caveats.append(
                "national_reference: poverty vintage unknown (place "
                f"{place_poverty_year}, national {national_poverty_year}); "
                "poverty_gap_pct_points withheld."
            )
        elif place_poverty_year == national_poverty_year:
            poverty_gap_pct_points = round(place_poverty_pct - national_poverty_pct, 1)
        else:
            caveats.append(
                "national_reference: poverty vintages differ (place "
                f"{place_poverty_year}, national {national_poverty_year}); "
                "poverty_gap_pct_points withheld."
            )

    national_reference = {
        "population": national_pop_value,
        "population_year": national_pop_year,
        "poverty_incidence_pct": national_poverty_pct,
        "poverty_year": national_poverty_year,
        "population_share_pct": population_share_pct,
        "poverty_gap_pct_points": poverty_gap_pct_points,
    }

    return {
        "query": location,
        "resolved": {
            "matched": matched,
            "name": locality_name,
            "region": region_name,
            "province": province_name,
            "psgc_code": resolved.get("psgc_code"),
            "level": resolved.get("level"),
            "alternatives": resolved.get("alternatives") or [],
            "hierarchy": [{"level": n.get("level"), "name": n.get("name")} for n in chain],
        },
        "demographics": {
            "population": pop_value,
            "population_year": population.get("year"),
            "population_census": population.get("census"),
            "population_reference": population.get("reference_note"),
            "population_geography": population.get("geography"),
            "population_geography_level": population.get("geography_level"),
            "population_psgc_code": population.get("psgc_code"),
            "population_reference_date": population.get("reference_date"),
            "poverty_incidence_pct": poverty.get("poverty_incidence_pct"),
            "poverty_reference_year": poverty.get("reference_year"),
            "poverty_area": poverty.get("region"),
        },
        "economy": {
            "headline_inflation_pct": inflation.get("headline_inflation_pct"),
            "inflation_reference_period": inflation.get("reference_period"),
            "labor_force_participation_rate_pct": labor.get("labor_force_participation_rate_pct"),
            "employment_rate_pct": labor.get("employment_rate_pct"),
            "unemployment_rate_pct": labor.get("unemployment_rate_pct"),
            "underemployment_rate_pct": labor.get("underemployment_rate_pct"),
            "labor_reference_period": labor.get("reference_period"),
            "labor_scope": "national (PSA LFS key-indicator table has no regional breakdown)",
        },
        "procurement": {
            "infra_notice_count": infra_count,
            "sample": infra[:5] if infra else [],
        },
        "hazard": {
            "earthquake_risk_level": hazard.get("earthquake_risk_level"),
            "recent_earthquakes_30d": hazard.get("recent_earthquakes_30d"),
            "max_magnitude_30d": hazard.get("max_magnitude_30d"),
            "typhoon_signal_active": hazard.get("typhoon_signal_active"),
            "active_typhoon_name": hazard.get("active_typhoon_name"),
            "volcano_alerts": hazard.get("volcano_alerts") or [],
            "volcano_alerts_scope": hazard.get("volcano_alerts_scope"),
        },
        "weather": {
            "data_source": weather.get("data_source"),
            "forecast_issued": weather.get("forecast_issued"),
            "days": weather.get("days", []),
        },
        "correlations": correlations,
        "national_reference": national_reference,
        "blocks": blocks,
        "upstream_error": any(v in _OUTAGE_STATUSES for v in blocks.values()),
        "caveats": caveats,
        "assessment_datetime": retrieved_at.isoformat(),
        "source": "PSGC + PSA + PhilGEPS + PHIVOLCS + PAGASA",
        "source_url": (
            "https://psgc.gitlab.io/api/, https://openstat.psa.gov.ph/PXWeb/api/v1/en/, "
            "https://www.philgeps.gov.ph/, https://earthquake.phivolcs.dost.gov.ph/, "
            "https://bagong.pagasa.dost.gov.ph/"
        ),
        "license": "Public — PSA OpenSTAT, PSGC, PhilGEPS, PHIVOLCS, PAGASA",
        "disclaimer": PROFILE_DISCLAIMER,
        "data_retrieved_at": retrieved_at.isoformat(),
    }
