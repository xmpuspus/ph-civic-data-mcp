"""Cross-source multi-hazard risk assessment + infra anomaly flagging."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dateutil import parser as date_parser

from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.sources.infra import (
    INFRA_DISCLAIMER,
    PHILGEPS_PORTAL,
    search_infra_projects,
)
from ph_civic_data_mcp.sources.pagasa import get_active_typhoons, get_weather_alerts
from ph_civic_data_mcp.sources.phivolcs import get_latest_earthquakes


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _risk_from_activity(count: int, max_magnitude: float) -> str:
    if max_magnitude >= 6.0 or count >= 50:
        return "Very High"
    if max_magnitude >= 5.0 or count >= 20:
        return "High"
    if max_magnitude >= 4.0 or count >= 8:
        return "Moderate"
    return "Low"


@mcp.tool()
async def assess_area_risk(location: str) -> dict:
    """Multi-hazard risk assessment combining PHIVOLCS + PAGASA.

    Makes parallel upstream calls to PHIVOLCS (earthquakes) and PAGASA
    (active typhoons, weather alerts). Expect 3-6 second response time.

    Args:
        location: Municipality, city, or province name.

    Returns:
        earthquake_risk_level derived from recent 30-day seismic activity (not an
        official PHIVOLCS assessment), typhoon signal status, active alerts, and
        caveats describing any failed sub-calls.
    """
    retrieved_at = _now()
    caveats: list[str] = []

    earthquakes_task = asyncio.create_task(
        get_latest_earthquakes(min_magnitude=1.0, limit=100, region=location)
    )
    typhoons_task = asyncio.create_task(get_active_typhoons())
    alerts_task = asyncio.create_task(get_weather_alerts(region=location))

    results = await asyncio.gather(
        earthquakes_task, typhoons_task, alerts_task, return_exceptions=True
    )
    earthquakes_result, typhoons_result, alerts_result = results

    recent_earthquakes_30d = 0
    max_magnitude_30d = 0.0
    if isinstance(earthquakes_result, BaseException):
        caveats.append(f"PHIVOLCS query failed: {type(earthquakes_result).__name__}")
    else:
        earthquakes = earthquakes_result or []
        cutoff = retrieved_at - timedelta(days=30)
        for quake in earthquakes:
            dt_raw = quake.get("datetime_pst")
            try:
                dt = date_parser.parse(dt_raw) if isinstance(dt_raw, str) else dt_raw
            except (ValueError, TypeError):
                continue
            if dt is None:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                recent_earthquakes_30d += 1
                max_magnitude_30d = max(max_magnitude_30d, quake.get("magnitude", 0.0))

    typhoon_signal_active = False
    active_typhoon_name: str | None = None
    if isinstance(typhoons_result, BaseException):
        caveats.append(f"PAGASA typhoon query failed: {type(typhoons_result).__name__}")
    else:
        typhoons = typhoons_result or []
        for t in typhoons:
            if t.get("signal_numbers"):
                typhoon_signal_active = True
                active_typhoon_name = t.get("local_name")
                break
        if typhoons and not active_typhoon_name:
            active_typhoon_name = typhoons[0].get("local_name")

    active_alerts: list[dict] = []
    if isinstance(alerts_result, BaseException):
        caveats.append(f"PAGASA alerts query failed: {type(alerts_result).__name__}")
    else:
        active_alerts = alerts_result or []

    risk_level = _risk_from_activity(recent_earthquakes_30d, max_magnitude_30d)

    return {
        "location": location,
        "earthquake_risk_level": risk_level,
        "recent_earthquakes_30d": recent_earthquakes_30d,
        "max_magnitude_30d": max_magnitude_30d,
        "typhoon_signal_active": typhoon_signal_active,
        "active_typhoon_name": active_typhoon_name,
        "active_alerts": active_alerts,
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


@mcp.tool()
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
    - high_cost_no_progress: cost_php exceeds min_cost_php with no progress
      data published (raises a transparency flag, not a malfeasance flag)
    - hazard_overlap: project location keywords overlap with a recent
      PHIVOLCS earthquake (>=M4.0 in last 30d) or an active PAGASA typhoon
      footprint, suggesting urgency or post-disaster reconstruction context

    Args:
        region: PH region filter for the project list.
        province: Province filter (partial match).
        min_cost_php: Threshold for the high_cost_no_progress rule
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

    if isinstance(projects_result, BaseException):
        caveats.append(f"PhilGEPS fetch failed: {type(projects_result).__name__}")
        projects = []
    else:
        projects = projects_result or []

    if isinstance(earthquakes_result, BaseException):
        caveats.append(f"PHIVOLCS fetch failed: {type(earthquakes_result).__name__}")
        earthquakes = []
    else:
        earthquakes = earthquakes_result or []

    if isinstance(typhoons_result, BaseException):
        caveats.append(f"PAGASA fetch failed: {type(typhoons_result).__name__}")
        typhoons = []
    else:
        typhoons = typhoons_result or []

    # Hazard footprint: lower-cased location keywords from recent quakes + typhoons.
    cutoff = retrieved_at - timedelta(days=30)
    hazard_keywords: set[str] = set()
    for quake in earthquakes:
        dt_raw = quake.get("datetime_pst")
        try:
            dt = date_parser.parse(dt_raw) if isinstance(dt_raw, str) else dt_raw
        except (ValueError, TypeError):
            continue
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt >= cutoff:
            loc = (quake.get("location") or "").lower()
            for token in loc.replace("(", " ").replace(")", " ").split():
                if len(token) >= 4 and token.isalpha():
                    hazard_keywords.add(token)

    for typhoon in typhoons:
        for area in (typhoon.get("signal_numbers") or {}).keys():
            for token in (area or "").lower().split():
                if len(token) >= 4 and token.isalpha():
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
                    "rule_fired": "high_cost_no_progress",
                    "evidence": (
                        f"approved_budget = ₱{cost:,.0f} but no progress_pct "
                        "published in the public listing"
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
            "recent_earthquake_count_30d": sum(
                1
                for q in earthquakes
                if (
                    isinstance(q.get("datetime_pst"), str)
                    and (
                        (
                            date_parser.parse(q["datetime_pst"]).replace(tzinfo=timezone.utc)
                            if date_parser.parse(q["datetime_pst"]).tzinfo is None
                            else date_parser.parse(q["datetime_pst"])
                        )
                        >= cutoff
                    )
                )
            ),
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
