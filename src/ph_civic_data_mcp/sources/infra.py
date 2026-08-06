"""Infrastructure project data, backed by PhilGEPS open notice listing.

The DPWH transparency portal (transparency.dpwh.gov.ph) is currently behind
Cloudflare's bot challenge and not reachable by any non-browser client without
fingerprint impersonation, so we do not depend on it. Instead, we filter the
existing PhilGEPS notice cache for infra-related procurement (construction,
road, bridge, flood control, drainage, dredging, etc.) and expose it under a
dedicated `infra_*` interface. Approved budget amounts are not in the public
notice listing, so cost_php is null in most records.

If/when DPWH (or a non-protected mirror) becomes reachable, this module is the
single integration point to swap in.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date as date_cls, datetime, timezone

from ph_civic_data_mcp.models.infra import InfraProject, InfraSpendingSummary
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.sources.philgeps import _fetch_notices  # type: ignore
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import failure_envelope
from ph_civic_data_mcp.utils.http import log_stderr

PHILGEPS_PORTAL = "https://www.philgeps.gov.ph/"
INFRA_LICENSE = "Public — PhilGEPS open notice listing"
INFRA_DISCLAIMER = (
    "Statistical indicators derived from public data. Patterns may have legitimate explanations."
)

# Province -> additional terms that commonly appear in DPWH agency / PhilGEPS
# region strings. Used by the province filter to widen substring match beyond
# the literal province name, since notice payloads rarely contain the bare
# province token. Expanded conservatively from observed PhilGEPS records.
_PROVINCE_AGENCY_HINTS: dict[str, list[str]] = {
    "abra": ["region i", "ilocos", "abra"],
    "agusan del norte": ["region xiii", "caraga", "agusan"],
    "agusan del sur": ["region xiii", "caraga", "agusan"],
    "aklan": ["region vi", "western visayas", "aklan"],
    "albay": ["region v", "bicol", "albay"],
    "antique": ["region vi", "western visayas", "antique"],
    "apayao": ["car", "cordillera", "apayao"],
    "aurora": ["region iii", "central luzon", "aurora"],
    "basilan": ["barmm", "basilan"],
    "bataan": ["region iii", "central luzon", "bataan"],
    "batanes": ["region ii", "cagayan valley", "batanes"],
    "batangas": ["region iv-a", "calabarzon", "batangas"],
    "benguet": ["car", "cordillera", "benguet", "baguio"],
    "biliran": ["region viii", "eastern visayas", "biliran"],
    "bohol": ["region vii", "central visayas", "bohol"],
    "bukidnon": ["region x", "northern mindanao", "bukidnon"],
    "bulacan": ["region iii", "central luzon", "bulacan"],
    "cagayan": ["region ii", "cagayan valley", "cagayan"],
    "camarines norte": ["region v", "bicol", "camarines"],
    "camarines sur": ["region v", "bicol", "camarines"],
    "camiguin": ["region x", "northern mindanao", "camiguin"],
    "capiz": ["region vi", "western visayas", "capiz", "roxas"],
    "catanduanes": ["region v", "bicol", "catanduanes"],
    "cavite": ["region iv-a", "calabarzon", "cavite"],
    "cebu": ["region vii", "central visayas", "cebu"],
    "compostela valley": ["region xi", "davao", "compostela", "davao de oro"],
    "cotabato": ["region xii", "soccsksargen", "cotabato"],
    "davao de oro": ["region xi", "davao", "davao de oro", "compostela"],
    "davao del norte": ["region xi", "davao", "davao"],
    "davao del sur": ["region xi", "davao", "davao"],
    "davao occidental": ["region xi", "davao", "davao"],
    "davao oriental": ["region xi", "davao", "davao"],
    "dinagat islands": ["region xiii", "caraga", "dinagat"],
    "eastern samar": ["region viii", "eastern visayas", "samar"],
    "guimaras": ["region vi", "western visayas", "guimaras"],
    "ifugao": ["car", "cordillera", "ifugao"],
    "ilocos norte": ["region i", "ilocos", "ilocos"],
    "ilocos sur": ["region i", "ilocos", "ilocos"],
    "iloilo": ["region vi", "western visayas", "iloilo"],
    "isabela": ["region ii", "cagayan valley", "isabela"],
    "kalinga": ["car", "cordillera", "kalinga"],
    "la union": ["region i", "ilocos", "la union"],
    "laguna": ["region iv-a", "calabarzon", "laguna"],
    "lanao del norte": ["region x", "northern mindanao", "lanao"],
    "lanao del sur": ["barmm", "lanao"],
    "leyte": ["region viii", "eastern visayas", "leyte"],
    "maguindanao": ["barmm", "maguindanao"],
    "marinduque": ["mimaropa", "marinduque"],
    "masbate": ["region v", "bicol", "masbate"],
    "metro manila": ["ncr", "metro manila", "manila"],
    "misamis occidental": ["region x", "northern mindanao", "misamis"],
    "misamis oriental": ["region x", "northern mindanao", "misamis", "cagayan de oro"],
    "mountain province": ["car", "cordillera", "mountain province"],
    "ncr": ["ncr", "metro manila"],
    "negros occidental": ["region vi", "western visayas", "negros", "bacolod"],
    "negros oriental": ["region vii", "central visayas", "negros", "dumaguete"],
    "northern samar": ["region viii", "eastern visayas", "samar"],
    "nueva ecija": ["region iii", "central luzon", "nueva ecija"],
    "nueva vizcaya": ["region ii", "cagayan valley", "nueva vizcaya"],
    "occidental mindoro": ["mimaropa", "mindoro"],
    "oriental mindoro": ["mimaropa", "mindoro"],
    "palawan": ["mimaropa", "palawan"],
    "pampanga": ["region iii", "central luzon", "pampanga", "san fernando"],
    "pangasinan": ["region i", "ilocos", "pangasinan"],
    "quezon": ["region iv-a", "calabarzon", "quezon"],
    "quirino": ["region ii", "cagayan valley", "quirino"],
    "rizal": ["region iv-a", "calabarzon", "rizal", "antipolo"],
    "romblon": ["mimaropa", "romblon"],
    "samar": ["region viii", "eastern visayas", "samar"],
    "sarangani": ["region xii", "soccsksargen", "sarangani"],
    "siquijor": ["region vii", "central visayas", "siquijor"],
    "sorsogon": ["region v", "bicol", "sorsogon"],
    "south cotabato": ["region xii", "soccsksargen", "cotabato"],
    "southern leyte": ["region viii", "eastern visayas", "leyte"],
    "sultan kudarat": ["region xii", "soccsksargen", "sultan kudarat"],
    "sulu": ["barmm", "sulu"],
    "surigao del norte": ["region xiii", "caraga", "surigao"],
    "surigao del sur": ["region xiii", "caraga", "surigao"],
    "tarlac": ["region iii", "central luzon", "tarlac"],
    "tawi-tawi": ["barmm", "tawi-tawi", "tawi tawi"],
    "zambales": ["region iii", "central luzon", "zambales", "olongapo"],
    "zamboanga del norte": ["region ix", "zamboanga peninsula", "zamboanga"],
    "zamboanga del sur": ["region ix", "zamboanga peninsula", "zamboanga"],
    "zamboanga sibugay": ["region ix", "zamboanga peninsula", "zamboanga"],
}


def _province_search_terms(province: str | None) -> list[str]:
    """Expand a province name into all substring terms to match in notice text."""
    if not province:
        return []
    key = province.strip().lower()
    if key in _PROVINCE_AGENCY_HINTS:
        return _PROVINCE_AGENCY_HINTS[key]
    return [key]


# Keywords used to identify infra-related procurement notices.
INFRA_KEYWORDS = [
    "construction",
    "road",
    "highway",
    "bridge",
    "flood control",
    "drainage",
    "drain ",
    "dredging",
    "rehabilitation",
    "infrastructure",
    "school building",
    "civil works",
    "pavement",
    "concreting",
    "shoreline",
    "seawall",
    "revetment",
    "slope protection",
    "asphalt",
    "water system",
    "irrigation",
    "barangay road",
    "farm-to-market",
    "multi-purpose building",
    "evacuation center",
]

CATEGORY_RULES: list[tuple[str, list[str]]] = [
    (
        "flood control",
        [
            "flood control",
            "drainage",
            "drain ",
            "dredging",
            "river ",
            "creek ",
            "shoreline",
            "seawall",
            "revetment",
            "slope protection",
        ],
    ),
    (
        "road / highway",
        ["road", "highway", "pavement", "concreting", "asphalt", "barangay road", "farm-to-market"],
    ),
    ("bridge", ["bridge"]),
    ("school building", ["school building"]),
    ("multi-purpose building", ["multi-purpose building", "barangay hall", "evacuation center"]),
    ("water / irrigation", ["water system", "irrigation"]),
    ("civil works (other)", ["construction", "rehabilitation", "civil works", "infrastructure"]),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _is_infra(record: object) -> bool:
    text = f"{getattr(record, 'title', '')} {getattr(record, 'agency', '')} {getattr(record, 'mode_of_procurement', '') or ''}"
    text = _normalize(text)
    return any(kw in text for kw in INFRA_KEYWORDS)


def _categorize(record: object) -> str:
    text = _normalize(
        f"{getattr(record, 'title', '')} {getattr(record, 'mode_of_procurement', '') or ''}"
    )
    for category, kws in CATEGORY_RULES:
        if any(kw in text for kw in kws):
            return category
    return "other"


def _record_id(record: object) -> str:
    ref = getattr(record, "reference_number", None) or ""
    title = getattr(record, "title", "")
    return ref or f"PHILGEPS-{abs(hash(title)) % 10**10}"


def _to_infra_project(record: object) -> InfraProject:
    return InfraProject(
        project_id=_record_id(record),
        title=getattr(record, "title", "(untitled)"),
        agency=getattr(record, "agency", "(unknown)"),
        region=getattr(record, "region", None),
        province=None,
        category=_categorize(record),
        cost_php=getattr(record, "approved_budget", None),
        currency=getattr(record, "currency", "PHP") or "PHP",
        progress_pct=None,
        funding_source=None,
        contractor=None,
        status=getattr(record, "status", None),
        date_published=getattr(record, "date_published", None),
        award_date=getattr(record, "award_date", None),
        lat=None,
        lng=None,
        documents=[],
        source_url=PHILGEPS_PORTAL,
        license=INFRA_LICENSE,
    )


async def _load_infra_records() -> list[object]:
    key = cache_key({"endpoint": "infra_window_v1"})
    cache = CACHES["infra_projects"]
    if key in cache:
        return cache[key]
    # _fetch_notices raises on upstream failure; let it propagate so callers
    # report the outage. Never cache an error as an empty window for 6h.
    records = await _fetch_notices()
    infra = [r for r in records if _is_infra(r)]
    cache[key] = infra
    return infra


@mcp.tool(
    title="Search infrastructure notices",
    tags={"accountability", "infrastructure", "philgeps", "philippines"},
    annotations={
        "title": "Search infrastructure notices",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def search_infra_projects(
    keyword: str | None = None,
    region: str | None = None,
    province: str | None = None,
    year: int | None = None,
    min_cost_php: float | None = None,
    status: str | None = None,
    limit: int = 25,
) -> list[dict] | dict:
    """Search Philippine government infrastructure projects.

    Backed by PhilGEPS open notice listing filtered for infra-related work
    (construction / road / bridge / flood control / drainage / school
    building / civil works). Source: https://www.philgeps.gov.ph/. Approved
    budget amounts are not published in the open notice listing, so cost_php
    is null in most records. The DPWH transparency portal API is currently
    blocked by Cloudflare and not used.

    Args:
        keyword: Title/agency substring (e.g. 'flood control', 'bridge').
        region: PH region filter (partial match against agency text).
        province: Province name filter (partial match).
        year: Filter publish date to this calendar year.
        min_cost_php: Minimum approved cost in PHP (filters out null-cost
                      records when set).
        status: Status filter (partial match, e.g. 'open', 'awarded').
        limit: Max results (default 25, capped at 100).

    Each result: project_id, title, agency, region, province, category,
    cost_php, currency, progress_pct, funding_source, contractor, status,
    date_published, award_date, lat, lng, documents, source, source_url,
    license, data_retrieved_at.
    """
    limit = max(1, min(int(limit), 100))
    retrieved_at = _now()

    try:
        records = await _load_infra_records()
    except Exception as exc:
        log_stderr(f"search_infra_projects error: {exc}")
        return failure_envelope(
            "PhilGEPS",
            PHILGEPS_PORTAL,
            f"PhilGEPS notice listing unavailable ({type(exc).__name__}: {exc}).",
            license=INFRA_LICENSE,
        )

    kw_lc = (keyword or "").lower().strip() or None
    region_lc = (region or "").lower().strip() or None
    province_lc = (province or "").lower().strip() or None
    province_terms = _province_search_terms(province)
    status_lc = (status or "").lower().strip() or None

    results: list[dict] = []
    for record in records:
        title = (getattr(record, "title", None) or "").lower()
        agency = (getattr(record, "agency", None) or "").lower()
        record_status = (getattr(record, "status", None) or "").lower()
        record_region = (getattr(record, "region", None) or "").lower()
        date_pub: date_cls | None = getattr(record, "date_published", None)
        cost = getattr(record, "approved_budget", None)

        if kw_lc and kw_lc not in title and kw_lc not in agency:
            continue
        if (
            region_lc
            and region_lc not in record_region
            and region_lc not in agency
            and region_lc not in title
        ):
            continue
        if province_lc:
            haystack = f"{title} {agency} {record_region}"
            if not any(term in haystack for term in province_terms):
                continue
        if year and date_pub and date_pub.year != year:
            continue
        if status_lc and status_lc not in record_status:
            continue
        if min_cost_php is not None and (cost is None or cost < min_cost_php):
            continue

        project = _to_infra_project(record)
        results.append(
            {
                **project.model_dump(mode="json"),
                "data_retrieved_at": retrieved_at.isoformat(),
            }
        )
        if len(results) >= limit:
            break

    return results


@mcp.tool(
    title="Infrastructure notice detail",
    tags={"accountability", "infrastructure", "philgeps", "philippines"},
    annotations={
        "title": "Infrastructure notice detail",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_infra_project(project_id: str) -> dict:
    """Return the full record for one infrastructure project by project_id.

    Args:
        project_id: Reference number from search_infra_projects.

    Returns: full InfraProject fields (cost_php, progress_pct, funding_source,
    contractor, lat/lng, documents) where exposed; null where the upstream
    listing does not publish that field.
    """
    if not project_id:
        return {
            "project_id": "",
            "matched": False,
            "caveats": ["project_id is empty"],
            "source": "PhilGEPS",
            "source_url": PHILGEPS_PORTAL,
            "license": INFRA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    try:
        records = await _load_infra_records()
    except Exception as exc:
        log_stderr(f"get_infra_project error: {exc}")
        return {
            "project_id": project_id,
            "matched": False,
            "upstream_error": True,
            "caveats": [f"PhilGEPS fetch failed ({type(exc).__name__}: {exc})"],
            "source": "PhilGEPS",
            "source_url": PHILGEPS_PORTAL,
            "license": INFRA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    target = project_id.strip()
    for record in records:
        if _record_id(record) == target or (getattr(record, "reference_number", None) == target):
            project = _to_infra_project(record)
            return {
                **project.model_dump(mode="json"),
                "matched": True,
                "data_retrieved_at": _now().isoformat(),
            }

    return {
        "project_id": project_id,
        "matched": False,
        "caveats": [
            f"No infra project found with id '{project_id}' in the current "
            "PhilGEPS notice window. The portal exposes only the latest ~100 "
            "notices; older project IDs may not be retrievable here."
        ],
        "source": "PhilGEPS",
        "source_url": PHILGEPS_PORTAL,
        "license": INFRA_LICENSE,
        "data_retrieved_at": _now().isoformat(),
    }


@mcp.tool(
    title="Summarize infrastructure spending",
    tags={"accountability", "infrastructure", "philgeps", "philippines"},
    annotations={
        "title": "Summarize infrastructure spending",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def summarize_infra_spending(
    region: str | None = None,
    year: int | None = None,
    funding_source: str | None = None,
) -> dict:
    """Aggregate infrastructure procurement statistics over the latest PhilGEPS window.

    Args:
        region: PH region filter (partial match).
        year: Filter publish date to this calendar year.
        funding_source: Reserved for future DPWH integration; PhilGEPS notices
                        do not expose funding source, so this filter is a
                        no-op today.

    Returns: total_count, total_value_php (null where costs not exposed),
    by_category, by_funding_source, by_region, top_agencies,
    reference_period, note, source, source_url, license, disclaimer.
    """
    retrieved_at = _now()

    summary_caveats: list[str] = []
    try:
        records = await _load_infra_records()
    except Exception as exc:
        log_stderr(f"summarize_infra_spending error: {exc}")
        records = []
        summary_caveats.append(
            f"PhilGEPS notice listing unavailable ({type(exc).__name__}: {exc}); "
            "totals below are zero because of the outage, not zero activity."
        )

    region_lc = (region or "").lower().strip() or None

    by_category: Counter[str] = Counter()
    by_funding_source: Counter[str] = Counter()
    by_region: Counter[str] = Counter()
    agency_totals: defaultdict[str, int] = defaultdict(int)
    publish_dates: list[date_cls] = []
    cost_total = 0.0
    cost_known = 0
    filtered = 0

    for record in records:
        agency = getattr(record, "agency", None) or ""
        record_region = (getattr(record, "region", None) or "").lower()
        date_pub = getattr(record, "date_published", None)

        if region_lc and region_lc not in record_region and region_lc not in agency.lower():
            continue
        if year and date_pub and date_pub.year != year:
            continue

        filtered += 1
        category = _categorize(record)
        by_category[category] += 1
        by_funding_source["unknown"] += 1
        if record_region:
            by_region[record_region.title()] += 1
        else:
            by_region["unspecified"] += 1
        agency_totals[agency or "(unknown)"] += 1
        cost = getattr(record, "approved_budget", None)
        if cost is not None:
            cost_total += float(cost)
            cost_known += 1
        if date_pub:
            publish_dates.append(date_pub)

    summary = InfraSpendingSummary(
        total_count=filtered,
        total_value_php=cost_total if cost_known > 0 else None,
        by_category=dict(by_category),
        by_funding_source=dict(by_funding_source),
        by_region=dict(by_region),
        top_agencies=[
            {"agency": a, "count": c}
            for a, c in sorted(agency_totals.items(), key=lambda kv: -kv[1])[:10]
        ],
        reference_period={
            "from": min(publish_dates).isoformat() if publish_dates else None,
            "to": max(publish_dates).isoformat() if publish_dates else None,
        },
        note=(
            "Computed over the latest infra-keyword-matched PhilGEPS notice "
            "window (cached 6h). Approved budget totals are not published in "
            "the open notice listing, so total_value_php is typically null."
        ),
        source_url=PHILGEPS_PORTAL,
        license=INFRA_LICENSE,
    )
    return {
        **summary.model_dump(mode="json"),
        "caveats": summary_caveats,
        "upstream_error": bool(summary_caveats),
        "data_retrieved_at": retrieved_at.isoformat(),
        "disclaimer": INFRA_DISCLAIMER,
    }
