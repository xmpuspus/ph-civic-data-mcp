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
from ph_civic_data_mcp.sources.philgeps import _fetch_notices, _infer_region  # type: ignore
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_SUCCESS,
    DATA_STATUS_UNAVAILABLE,
    failure_envelope,
    failure_result,
)
from ph_civic_data_mcp.utils.http import log_stderr

PHILGEPS_PORTAL = "https://www.philgeps.gov.ph/"
INFRA_LICENSE = "Public — PhilGEPS open notice listing"
INFRA_DISCLAIMER = (
    "Statistical indicators derived from public data. Patterns may have legitimate explanations."
)

# A ~100-notice PhilGEPS sample cannot support a population-representative
# per-100k rate for a region with a multi-million population. The current
# fetch window (see philgeps._fetch_notices) is capped at the latest ~100
# notices, so it never reaches this threshold today; a caller normalizing
# by population should treat the rate as unavailable until it does.
MIN_SAMPLE_FOR_PER_CAPITA = 500


def infra_sample_coverage(sample_size: int) -> dict:
    """Coverage verdict for a per-capita rate built from an infra notice count.

    Returns whether `sample_size` clears MIN_SAMPLE_FOR_PER_CAPITA, plus a
    reason a caller can show when it does not. A caller computing a
    notices-per-100k figure (for example autostitch.get_area_profile) should
    withhold that figure, or ship it with this caveat attached, when
    `sufficient_for_per_capita` is False.
    """
    sufficient = sample_size >= MIN_SAMPLE_FOR_PER_CAPITA
    return {
        "sample_size": sample_size,
        "sufficient_for_per_capita": sufficient,
        "coverage_caveat": (
            None
            if sufficient
            else (
                f"Sample of {sample_size} PhilGEPS notices is below the "
                f"{MIN_SAMPLE_FOR_PER_CAPITA}-notice threshold for a "
                "population-representative per-100k rate."
            )
        ),
    }


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
    if ref:
        return ref
    # A reference-free record needs title, agency, and publish date together,
    # not title alone. Two agencies can post the same generic title (for
    # example "Road Rehabilitation"), and a title-only hash gave them the
    # same fallback id, so get_infra_project returned the wrong record.
    title = getattr(record, "title", "")
    agency = getattr(record, "agency", "")
    published = getattr(record, "date_published", "")
    fallback_key = f"{title}|{agency}|{published}"
    return f"PHILGEPS-{abs(hash(fallback_key)) % 10**10}"


def _to_infra_project(record: object) -> InfraProject:
    return InfraProject(
        project_id=_record_id(record),
        title=getattr(record, "title", "(untitled)"),
        agency=getattr(record, "agency", "(unknown)"),
        region=_infer_region(getattr(record, "agency", None), getattr(record, "region", None)),
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
    is null in most records. min_cost_php almost never matches, because the
    open listing omits approved budget for nearly every record. The DPWH
    transparency portal API is currently blocked by Cloudflare and not
    used. Examples:

      search_infra_projects(keyword="flood control")
      search_infra_projects(region="ncr")
      search_infra_projects(min_cost_php=100_000_000)

    On failure: returns {results: [], upstream_error: true, data_status:
    "unavailable", caveats: [...]} instead of a bare list, with the real
    upstream error in caveats. This tool never checks an argument before
    the PhilGEPS fetch runs, so validation_error is always false.

    Args:
        keyword: Title/agency substring (e.g. 'flood control', 'bridge').
        region: PH region filter (partial match against agency text).
        province: Province name filter (partial match).
        year: Filter publish date to this calendar year. Excludes records
              with no publish date, so a year filter never returns an
              undated record.
        min_cost_php: Minimum approved cost in PHP. The open notice listing
                      does not publish approved budget for almost any
                      record, so setting this today returns few or no
                      results rather than a true cost-ranked subset.
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
    undated_dropped = 0
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
        if year and date_pub is None:
            undated_dropped += 1
            continue
        if year and date_pub.year != year:
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

    if year and undated_dropped:
        log_stderr(
            f"search_infra_projects: year={year} filter dropped {undated_dropped} "
            "record(s) with no publish date."
        )

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

    This tool looks up project_id inside the same latest ~100-notice
    PhilGEPS window search_infra_projects reads. An id from an older window
    returns matched: false, not an error, because the window has already
    moved on. Examples:

      get_infra_project("PHILGEPS-INF-003")
      get_infra_project("DOES-NOT-EXIST")

    On failure: an empty project_id or a project missing from the current
    window both return matched: false with a caveat, no upstream_error, and
    no data_status. A PhilGEPS fetch failure returns matched: false,
    data_status "unavailable", upstream_error true, and the real error text
    in caveats.

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
        return failure_result(
            "PhilGEPS",
            PHILGEPS_PORTAL,
            f"PhilGEPS fetch failed ({type(exc).__name__}: {exc})",
            license=INFRA_LICENSE,
            project_id=project_id,
            matched=False,
        )

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

    This tool aggregates the same infra-keyword-matched notice window
    search_infra_projects reads (cached 6h). rules_evaluated names which
    breakdowns ran, today by_category and by_region. rules_not_computable
    names by_funding_source, retired to an always-empty dict because
    PhilGEPS notices carry no funding source field. sample_size and
    sufficient_for_per_capita flag whether the window, today under the
    500-notice threshold, is large enough for a per-100k rate. Examples:

      summarize_infra_spending()
      summarize_infra_spending(region="ncr", year=2025)

    On failure: data_status "unavailable", upstream_error true, totals zero
    and by_category/by_region/top_agencies empty, with the real PhilGEPS
    error in caveats. No validation_error path exists.

    Args:
        region: PH region filter (partial match).
        year: Filter publish date to this calendar year. Drops an
            undated record and notes the dropped count in caveats.
        funding_source: Reserved for future DPWH integration; PhilGEPS notices
                        do not expose funding source, so this filter is a
                        no-op today.

    Returns: total_count, total_value_php (null where costs not exposed),
    by_category, by_funding_source (always empty, see rules_not_computable),
    by_region, top_agencies, reference_period, note, rules_evaluated,
    rules_not_computable, sample_size, sufficient_for_per_capita,
    coverage_caveat, source, source_url, license, disclaimer.
    """
    retrieved_at = _now()

    rules_not_computable = [
        {
            "rule": "by_funding_source",
            "reason": (
                "PhilGEPS open notices carry no funding source field, so this "
                "breakdown is always empty rather than a guessed 'unknown' total."
            ),
        }
    ]

    try:
        records = await _load_infra_records()
    except Exception as exc:
        log_stderr(f"summarize_infra_spending error: {exc}")
        empty_coverage = infra_sample_coverage(0)
        return failure_result(
            "PhilGEPS",
            PHILGEPS_PORTAL,
            f"PhilGEPS notice listing unavailable ({type(exc).__name__}: {exc}); "
            "totals below are zero because of the outage, not zero activity.",
            license=INFRA_LICENSE,
            data_status=DATA_STATUS_UNAVAILABLE,
            total_count=0,
            total_value_php=None,
            by_category={},
            by_funding_source={},
            by_region={},
            top_agencies=[],
            reference_period={"from": None, "to": None},
            note=(
                "Computed over the latest infra-keyword-matched PhilGEPS notice "
                "window (cached 6h). Approved budget totals are not published in "
                "the open notice listing, so total_value_php is typically null."
            ),
            rules_evaluated=[],
            rules_not_computable=rules_not_computable,
            sample_size=empty_coverage["sample_size"],
            sufficient_for_per_capita=empty_coverage["sufficient_for_per_capita"],
            coverage_caveat=empty_coverage["coverage_caveat"],
            disclaimer=INFRA_DISCLAIMER,
        )

    region_lc = (region or "").lower().strip() or None

    by_category: Counter[str] = Counter()
    by_region: Counter[str] = Counter()
    agency_totals: defaultdict[str, int] = defaultdict(int)
    publish_dates: list[date_cls] = []
    cost_total = 0.0
    cost_known = 0
    filtered = 0

    undated_dropped = 0
    for record in records:
        agency = getattr(record, "agency", None) or ""
        record_region = (getattr(record, "region", None) or "").lower()
        date_pub = getattr(record, "date_published", None)

        if region_lc and region_lc not in record_region and region_lc not in agency.lower():
            continue
        if year and date_pub is None:
            undated_dropped += 1
            continue
        if year and date_pub.year != year:
            continue

        filtered += 1
        category = _categorize(record)
        by_category[category] += 1
        # by_funding_source is intentionally not populated: PhilGEPS notices
        # carry no funding source field. See rules_not_computable below.
        region_label = _infer_region(agency, getattr(record, "region", None))
        by_region[region_label or "unspecified"] += 1
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
        by_funding_source={},
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
    caveats: list[str] = []
    if year and undated_dropped:
        caveats.append(
            f"{undated_dropped} record(s) excluded from the year={year} filter "
            "because they have no publish date."
        )

    coverage = infra_sample_coverage(filtered)
    return {
        **summary.model_dump(mode="json"),
        "caveats": caveats,
        "upstream_error": False,
        "data_status": DATA_STATUS_SUCCESS,
        "data_retrieved_at": retrieved_at.isoformat(),
        "disclaimer": INFRA_DISCLAIMER,
        "rules_evaluated": ["by_category", "by_region"],
        "rules_not_computable": rules_not_computable,
        "sample_size": coverage["sample_size"],
        "sufficient_for_per_capita": coverage["sufficient_for_per_capita"],
        "coverage_caveat": coverage["coverage_caveat"],
    }
