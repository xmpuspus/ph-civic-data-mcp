"""Cross-source multi-hazard risk assessment + infra anomaly flagging."""

from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dateutil import parser as date_parser

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.sources.infra import (
    INFRA_DISCLAIMER,
    PHILGEPS_PORTAL,
    search_infra_projects,
)
from ph_civic_data_mcp.sources.pagasa import get_active_typhoons, get_weather_alerts
from ph_civic_data_mcp.sources.phivolcs import get_latest_earthquakes, get_volcano_status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(raw: object) -> datetime | None:
    """Parse an upstream datetime string defensively; None on any failure."""
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        try:
            dt = date_parser.parse(raw)
        except (ValueError, OverflowError, TypeError):
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _unwrap_list(result: object, caveats: list[str], label: str) -> list:
    """Normalize a gathered upstream result to a list.

    Upstream list tools return a dict failure envelope (results: [],
    upstream_error: true) on outage; exceptions surface via
    asyncio.gather(return_exceptions=True). Both become caveats here.
    """
    if isinstance(result, BaseException):
        caveats.append(f"{label} failed: {type(result).__name__}")
        return []
    if isinstance(result, dict):
        upstream_caveats = result.get("caveats") or []
        caveats.append(f"{label} failed: {'; '.join(upstream_caveats) or 'upstream error'}")
        return []
    return result or []


# Geographic chrome that PHIVOLCS/PAGASA include in location strings but that
# match too broadly against project titles. Compared in lowercase. Curated on
# audit 2026-05-01 from observed false-positive evidence.
_HAZARD_STOPWORDS: frozenset[str] = frozenset(
    {
        "area",
        "areas",
        "barangay",
        "city",
        "cities",
        "central",
        "coast",
        "coastal",
        "deep",
        "district",
        "east",
        "eastern",
        "island",
        "islands",
        "isle",
        "luzon",
        "metro",
        "manila",
        "mindanao",
        "mountain",
        "municipal",
        "municipality",
        "north",
        "northern",
        "ocean",
        "philippine",
        "philippines",
        "province",
        "provinces",
        "region",
        "regional",
        "regions",
        "river",
        "sea",
        "south",
        "southern",
        "valley",
        "visayas",
        "west",
        "western",
    }
)


def _proper_noun_tokens(text: str) -> list[str]:
    """Extract capitalized proper-noun tokens from a location string.

    Returns lowercase tokens of length >=5 that started with an uppercase
    letter in the original input and are not in the geographic-chrome
    stoplist. Splits on parens, slashes, hyphens, and whitespace. Strips
    trailing punctuation.

    >>> _proper_noun_tokens("012 km N 38° E of San Jose De Buan (Samar)")
    ['samar']
    """
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in re.split(r"[\s()/\-,]+", text):
        token = raw.strip(".,;:")
        if not token or len(token) < 5 or not token[0].isupper():
            continue
        # Drop pure-alpha check after stripping punct; allow accented chars by
        # falling back on isalpha for broad compatibility.
        if not token.isalpha():
            continue
        lc = token.lower()
        if lc in _HAZARD_STOPWORDS or lc in seen:
            continue
        seen.add(lc)
        out.append(lc)
    return out


def _risk_from_activity(count: int, max_magnitude: float) -> str:
    if max_magnitude >= 6.0 or count >= 50:
        return "Very High"
    if max_magnitude >= 5.0 or count >= 20:
        return "High"
    if max_magnitude >= 4.0 or count >= 8:
        return "Moderate"
    return "Low"


@mcp.tool(
    title="Multi-hazard risk snapshot for a place",
    tags={"composite", "hazard", "philippines", "risk"},
    annotations={
        "title": "Multi-hazard risk snapshot for a place",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def assess_area_risk(location: str) -> dict:
    """Multi-hazard risk assessment combining PHIVOLCS + PAGASA.

    Makes parallel upstream calls to PHIVOLCS (earthquakes, volcano alert
    levels) and PAGASA (active typhoons, weather alerts). Expect 3-6 second
    response time.

    Args:
        location: Municipality, city, or province name.

    Returns:
        earthquake_risk_level derived from recent 30-day seismic activity (not an
        official PHIVOLCS assessment), typhoon signal status, active alerts,
        elevated volcano alerts (national scope), and caveats describing any
        failed sub-calls.
    """
    retrieved_at = _now()
    caveats: list[str] = []

    earthquakes_task = asyncio.create_task(
        get_latest_earthquakes(min_magnitude=1.0, limit=100, region=location)
    )
    typhoons_task = asyncio.create_task(get_active_typhoons())
    alerts_task = asyncio.create_task(get_weather_alerts(region=location))
    volcano_task = asyncio.create_task(get_volcano_status())

    results = await asyncio.gather(
        earthquakes_task, typhoons_task, alerts_task, volcano_task, return_exceptions=True
    )
    earthquakes_result, typhoons_result, alerts_result, volcano_result = results

    earthquakes = _unwrap_list(earthquakes_result, caveats, "PHIVOLCS earthquake query")
    typhoons = _unwrap_list(typhoons_result, caveats, "PAGASA typhoon query")
    active_alerts = _unwrap_list(alerts_result, caveats, "PAGASA alerts query")
    volcanoes = _unwrap_list(volcano_result, caveats, "PHIVOLCS volcano query")

    recent_earthquakes_30d = 0
    max_magnitude_30d = 0.0
    cutoff = retrieved_at - timedelta(days=30)
    for quake in earthquakes:
        dt = _parse_dt(quake.get("datetime_pst"))
        if dt is None:
            continue
        if dt >= cutoff:
            recent_earthquakes_30d += 1
            max_magnitude_30d = max(max_magnitude_30d, quake.get("magnitude", 0.0))

    typhoon_signal_active = False
    active_typhoon_name: str | None = None
    for t in typhoons:
        if t.get("signal_numbers"):
            typhoon_signal_active = True
            active_typhoon_name = t.get("local_name")
            break
    if typhoons and not active_typhoon_name:
        active_typhoon_name = typhoons[0].get("local_name")

    # Volcanoes are monitored nationally; PHIVOLCS bulletins don't map to an
    # arbitrary location string, so this block reports elevated alert levels
    # countrywide rather than pretending to geo-filter them.
    volcano_alerts = [
        {
            "name": v.get("name"),
            "alert_level": v.get("alert_level"),
            "status_description": v.get("status_description"),
            "bulletin_url": v.get("bulletin_url"),
        }
        for v in volcanoes
        if isinstance(v.get("alert_level"), int) and v["alert_level"] >= 1
    ]

    risk_level = _risk_from_activity(recent_earthquakes_30d, max_magnitude_30d)

    return {
        "location": location,
        "earthquake_risk_level": risk_level,
        "recent_earthquakes_30d": recent_earthquakes_30d,
        "max_magnitude_30d": max_magnitude_30d,
        "typhoon_signal_active": typhoon_signal_active,
        "active_typhoon_name": active_typhoon_name,
        "active_alerts": active_alerts,
        "volcano_alerts": volcano_alerts,
        "volcano_alerts_scope": "national — PHIVOLCS volcano bulletins are not geo-filtered",
        "assessment_datetime": retrieved_at.isoformat(),
        "caveats": caveats,
        "note": (
            "earthquake_risk_level is derived from recent seismic activity, "
            "not an official PHIVOLCS hazard assessment. For emergencies refer "
            "to ndrrmc.gov.ph and official PHIVOLCS/PAGASA channels."
        ),
        "source": "PHIVOLCS + PAGASA",
        "source_url": "https://earthquake.phivolcs.dost.gov.ph/, https://bagong.pagasa.dost.gov.ph/",
        "license": "Public — PHIVOLCS and PAGASA bulletin pages",
        "disclaimer": (
            "Statistical indicators derived from public data. "
            "Patterns may have legitimate explanations."
        ),
    }


@mcp.tool(
    title="Flag infrastructure spending anomalies",
    tags={"accountability", "heuristics", "infrastructure", "philippines"},
    annotations={
        "title": "Flag infrastructure spending anomalies",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def flag_infra_anomalies(
    region: str | None = None,
    province: str | None = None,
    min_cost_php: float = 50_000_000,
) -> dict:
    """Flag PhilGEPS infrastructure projects that warrant further review by
    cross-referencing PHIVOLCS earthquakes and PAGASA typhoon footprints.

    This tool emits heuristic anomaly indicators, not accusations. Every
    flagged item ships with the rule that fired and a disclaimer noting that
    patterns may have legitimate explanations.

    Heuristic rules:
    - duplicate_titles_same_agency: same agency files multiple notices with
      effectively identical titles (case-insensitive) within the window
    - high_cost_no_published_progress: cost_php exceeds min_cost_php. The
      PhilGEPS open listing publishes no progress data for ANY notice, so
      this is a cost-threshold transparency flag, not a project-specific
      "progress is missing" finding.
    - hazard_overlap: project location keywords overlap with a recent
      PHIVOLCS earthquake (>=M4.0 in last 30d) or an active PAGASA typhoon
      footprint, suggesting urgency or post-disaster reconstruction context

    Args:
        region: PH region filter for the project list.
        province: Province filter (partial match).
        min_cost_php: Threshold for the high_cost_no_published_progress rule
                      (default 50,000,000 PHP).

    Returns: flagged list with each entry containing project_id, title,
    agency, rule_fired, evidence, source_url, plus the global disclaimer.
    """
    retrieved_at = _now()
    caveats: list[str] = []

    projects_task = asyncio.create_task(
        search_infra_projects(region=region, province=province, limit=100)
    )
    earthquakes_task = asyncio.create_task(get_latest_earthquakes(min_magnitude=4.0, limit=50))
    typhoons_task = asyncio.create_task(get_active_typhoons())

    projects_result, earthquakes_result, typhoons_result = await asyncio.gather(
        projects_task, earthquakes_task, typhoons_task, return_exceptions=True
    )

    projects = _unwrap_list(projects_result, caveats, "PhilGEPS fetch")
    earthquakes = _unwrap_list(earthquakes_result, caveats, "PHIVOLCS fetch")
    typhoons = _unwrap_list(typhoons_result, caveats, "PAGASA fetch")

    # Hazard footprint: proper-noun location tokens from recent quakes + typhoons.
    # Use only capitalized words from the original (un-lowercased) location string
    # to keep generic words ("city", "road", "area") out of the keyword set, then
    # apply an explicit stoplist of common geographic chrome that survives the
    # capitalization filter. Audit 2026-05-01 found tokens like "city" and
    # "surigao" matching project titles like "Pasig City" with no real overlap.
    cutoff = retrieved_at - timedelta(days=30)
    hazard_keywords: set[str] = set()
    recent_quake_count_30d = 0
    for quake in earthquakes:
        dt = _parse_dt(quake.get("datetime_pst"))
        if dt is None or dt < cutoff:
            continue
        recent_quake_count_30d += 1
        loc = quake.get("location") or ""
        for token in _proper_noun_tokens(loc):
            hazard_keywords.add(token)

    for typhoon in typhoons:
        for area in (typhoon.get("signal_numbers") or {}).keys():
            for token in _proper_noun_tokens(area or ""):
                hazard_keywords.add(token)

    # Duplicate-title detector by (agency, normalised title).
    seen_titles: defaultdict[tuple[str, str], list[dict]] = defaultdict(list)
    for project in projects:
        agency = (project.get("agency") or "").lower().strip()
        norm_title = " ".join((project.get("title") or "").lower().split())
        if agency and norm_title:
            seen_titles[(agency, norm_title)].append(project)

    flags: list[dict] = []

    for project in projects:
        cost = project.get("cost_php")
        if cost is not None and cost >= min_cost_php and project.get("progress_pct") is None:
            flags.append(
                {
                    "project_id": project.get("project_id"),
                    "title": project.get("title"),
                    "agency": project.get("agency"),
                    "region": project.get("region"),
                    "rule_fired": "high_cost_no_published_progress",
                    "evidence": (
                        f"approved_budget = ₱{cost:,.0f} exceeds the "
                        f"₱{min_cost_php:,.0f} threshold. Note: the PhilGEPS "
                        "open listing publishes no progress data for any "
                        "notice, so this is a cost-threshold transparency "
                        "flag, not a project-specific progress finding."
                    ),
                    "source_url": project.get("source_url"),
                }
            )

        title_text = (project.get("title") or "").lower()
        matching_keywords = [kw for kw in hazard_keywords if kw in title_text]
        if matching_keywords:
            flags.append(
                {
                    "project_id": project.get("project_id"),
                    "title": project.get("title"),
                    "agency": project.get("agency"),
                    "region": project.get("region"),
                    "rule_fired": "hazard_overlap",
                    "evidence": (
                        f"project title overlaps with recent hazard footprint "
                        f"keywords: {sorted(set(matching_keywords))[:5]}"
                    ),
                    "source_url": project.get("source_url"),
                }
            )

    for (_, _), bucket in seen_titles.items():
        if len(bucket) >= 2:
            for project in bucket:
                flags.append(
                    {
                        "project_id": project.get("project_id"),
                        "title": project.get("title"),
                        "agency": project.get("agency"),
                        "region": project.get("region"),
                        "rule_fired": "duplicate_titles_same_agency",
                        "evidence": (
                            f"{len(bucket)} notices with the same title from this agency "
                            "in the current window"
                        ),
                        "source_url": project.get("source_url"),
                    }
                )

    # Deduplicate so the same project isn't reported twice for the same rule.
    seen_keys: set[tuple] = set()
    unique_flags: list[dict] = []
    for flag in flags:
        key = (flag.get("project_id"), flag.get("rule_fired"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_flags.append(flag)

    rule_counts: dict[str, int] = {}
    for flag in unique_flags:
        rule_counts[flag["rule_fired"]] = rule_counts.get(flag["rule_fired"], 0) + 1

    return {
        "filters": {
            "region": region,
            "province": province,
            "min_cost_php": min_cost_php,
        },
        "projects_examined": len(projects),
        "flagged_count": len(unique_flags),
        "rules_summary": rule_counts,
        "flagged": unique_flags,
        "hazard_inputs": {
            "recent_earthquake_count_30d": recent_quake_count_30d,
            "active_typhoon_count": len(typhoons),
        },
        "caveats": caveats,
        "assessment_datetime": retrieved_at.isoformat(),
        "source": "PhilGEPS + PHIVOLCS + PAGASA",
        "source_url": (
            f"{PHILGEPS_PORTAL}, https://earthquake.phivolcs.dost.gov.ph/, "
            "https://bagong.pagasa.dost.gov.ph/"
        ),
        "license": "Public — PhilGEPS, PHIVOLCS, PAGASA notice and bulletin pages",
        "disclaimer": INFRA_DISCLAIMER,
    }
