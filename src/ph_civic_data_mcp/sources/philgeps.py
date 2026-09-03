"""PhilGEPS — government procurement notices.

Landmine (from validation log):
- 9: open.philgeps.gov.ph does not expose filterable API or bulk xlsx publicly.
  The spec's xlsx streaming approach remains the target when/if files become
  available; today we fall back to scraping the public Indexes listing
  (latest ~100 bid notices) and filter in-memory. Keyword/agency/region
  filters operate on that window. Documented clearly in tool docstrings.
- 10: explicit cache key via cache_key()
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date as date_cls, datetime, timezone

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from ph_civic_data_mcp.models.procurement import ProcurementRecord
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_SUCCESS,
    DATA_STATUS_UNAVAILABLE,
    failure_envelope,
    failure_result,
)
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

PHILGEPS_INDEX_URL = "https://www.philgeps.gov.ph/Indexes/index"
PHILGEPS_LICENSE = "Public — PhilGEPS open notice listing"

# The Indexes page scrape never fills ProcurementRecord.region (see
# _fetch_notices below), but the agency string often names one, for example
# "DPWH - Region III" or "DepEd NCR". _infer_region reads this populated
# field instead of the always-empty one, so region breakdowns get a real
# signal on the notices that carry it. Matches numbered regions (I to XIII,
# with the IV-A / IV-B split) and the named regions (NCR, CAR, BARMM,
# CARAGA, MIMAROPA, CALABARZON, SOCCSKSARGEN).
_REGION_NUMERAL_RE = re.compile(r"\bREGION\s+([IVX]{1,4}(?:-[AB])?)\b", re.IGNORECASE)
_REGION_NAME_RE = re.compile(
    r"\b(NCR|CAR|BARMM|CARAGA|MIMAROPA|CALABARZON|SOCCSKSARGEN)\b", re.IGNORECASE
)


def _infer_region(agency: str | None, region: str | None = None) -> str | None:
    """Best-effort region label from a structured field or agency text.

    Returns `region` when the caller already has one. Otherwise it searches
    `agency` for a region token and returns that. Returns None when neither
    source names a region, so a caller can tell "no signal" from "NCR".
    """
    if region:
        return region
    text = agency or ""
    numeral = _REGION_NUMERAL_RE.search(text)
    if numeral:
        return f"Region {numeral.group(1).upper()}"
    name = _REGION_NAME_RE.search(text)
    return name.group(1).upper() if name else None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_phil_date(text: str) -> date_cls | None:
    if not text:
        return None
    try:
        return date_parser.parse(text, fuzzy=True, dayfirst=True).date()
    except (ValueError, OverflowError):
        return None


async def _fetch_notices() -> list[ProcurementRecord]:
    key = cache_key({"endpoint": "notices_v1"})
    cache = CACHES["philgeps_data"]
    if key in cache:
        return cache[key]

    # Fetch and parse failures raise — callers turn them into a failure
    # envelope. Never cache an error as "no notices" for the 6h success TTL.
    response = await fetch_with_retry(CLIENT, "GET", PHILGEPS_INDEX_URL)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    tables = soup.find_all("table")
    if not tables:
        raise RuntimeError("PhilGEPS Indexes page has no table (HTML drift?)")

    rows = tables[0].find_all("tr")
    records: list[ProcurementRecord] = []
    for row in rows[1:]:
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 7:
            continue
        ref, title, mode, classification, agency, pub_date, close_date = cells[:7]
        records.append(
            ProcurementRecord(
                reference_number=ref or None,
                title=title or "(untitled)",
                agency=agency or "(unknown)",
                region=None,
                mode_of_procurement=mode or None,
                approved_budget=None,
                currency="PHP",
                status="Open" if close_date else None,
                date_published=_parse_phil_date(pub_date),
                award_date=None,
            )
        )

    cache[key] = records
    return records


@mcp.tool(
    title="Search PhilGEPS procurement notices",
    tags={"accountability", "philgeps", "philippines", "procurement"},
    annotations={
        "title": "Search PhilGEPS procurement notices",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def search_procurement(
    keyword: str,
    agency: str | None = None,
    region: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict] | dict:
    """Search PH government procurement from PhilGEPS open data.

    Note: the PhilGEPS public portal does not expose server-side search for
    external clients, so this tool fetches the latest ~100 bid notices and
    filters them in-memory. Data is cached 6 hours. Keyword/agency/region
    filters are applied client-side (case-insensitive substring match).
    Examples:

      search_procurement(keyword="flood")
      search_procurement(keyword="", agency="DPWH", limit=10)

    On failure: returns {results: [], upstream_error: true, data_status:
    "unavailable", caveats: [...]} instead of a bare list, so an outage is
    never read as "no matching notices". No validation_error path exists.

    Args:
        keyword: Search term matched against title + agency + classification.
        agency: Partial match on procuring entity name.
        region: PH region filter (partial match).
        date_from / date_to: YYYY-MM-DD bounds on publish date.
        limit: Max results (default 20, max 100).

    Returns a list of matching notices on success.
    """
    limit = max(1, min(int(limit), 100))

    try:
        records = await _fetch_notices()
    except Exception as exc:
        log_stderr(f"search_procurement error: {exc}")
        return failure_envelope(
            "PhilGEPS",
            PHILGEPS_INDEX_URL,
            f"PhilGEPS notice listing unavailable ({type(exc).__name__}: {exc}).",
            license=PHILGEPS_LICENSE,
        )

    retrieved_at = _now()
    kw_lc = keyword.lower().strip() if keyword else ""
    agency_lc = agency.lower().strip() if agency else None
    region_lc = region.lower().strip() if region else None

    from_date = _parse_phil_date(date_from) if date_from else None
    to_date = _parse_phil_date(date_to) if date_to else None

    results: list[dict] = []
    for record in records:
        haystack = f"{record.title} {record.agency} {record.mode_of_procurement or ''}".lower()
        if kw_lc and kw_lc not in haystack:
            continue
        if agency_lc and agency_lc not in record.agency.lower():
            continue
        if region_lc:
            record_region = (record.region or "").lower()
            if region_lc not in record_region and region_lc not in record.agency.lower():
                continue
        if from_date and record.date_published and record.date_published < from_date:
            continue
        if to_date and record.date_published and record.date_published > to_date:
            continue

        results.append(
            {
                **record.model_dump(mode="json"),
                "data_retrieved_at": retrieved_at.isoformat(),
            }
        )
        if len(results) >= limit:
            break

    return results


@mcp.tool(
    title="Summarize PhilGEPS procurement",
    tags={"accountability", "philgeps", "philippines", "procurement"},
    annotations={
        "title": "Summarize PhilGEPS procurement",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_procurement_summary(
    agency: str | None = None,
    region: str | None = None,
    year: int | None = None,
) -> dict:
    """Aggregate procurement statistics over the latest notices cached from PhilGEPS.

    This tool aggregates the same latest ~100-notice window
    search_procurement reads (6h cache). rules_evaluated names which
    breakdowns ran, by_mode and by_region. rules_not_computable explains
    why total_value_php stays null: PhilGEPS open notices do not publish
    approved budget amounts. Examples:

      get_procurement_summary()
      get_procurement_summary(agency="DPWH", year=2025)

    On failure: data_status "unavailable", upstream_error true, totals zero
    and by_mode/by_region/top_agencies empty, with the real PhilGEPS error
    in caveats. No validation_error path exists.

    Args:
        agency: Partial agency match filter.
        region: PH region filter.
        year: Filter publish date to this year.

    Returns:
        Totals, breakdown by procurement mode, top agencies, reference period.
    """
    retrieved_at = _now()
    try:
        records = await _fetch_notices()
    except Exception as exc:
        log_stderr(f"procurement_summary error: {exc}")
        return failure_result(
            "PhilGEPS",
            PHILGEPS_INDEX_URL,
            f"PhilGEPS notice listing unavailable ({type(exc).__name__}: {exc}); "
            "totals below are zero because of the outage, not zero activity.",
            license=PHILGEPS_LICENSE,
            data_status=DATA_STATUS_UNAVAILABLE,
            total_count=0,
            total_value_php=None,
            by_mode={},
            by_region={},
            top_agencies=[],
            reference_period={"from": None, "to": None},
            note=(
                "Summary computed over latest ~100 PhilGEPS bid notices (6h cache). "
                "Approved budget totals are not published in the open listing."
            ),
            rules_evaluated=[],
            rules_not_computable=[
                {
                    "rule": "by_mode",
                    "reason": "PhilGEPS notice listing unavailable; no notices to aggregate.",
                },
                {
                    "rule": "by_region",
                    "reason": "PhilGEPS notice listing unavailable; no notices to aggregate.",
                },
                {
                    "rule": "total_value_php",
                    "reason": "PhilGEPS open notices do not publish approved budget amounts.",
                },
            ],
        )

    agency_lc = agency.lower().strip() if agency else None
    region_lc = region.lower().strip() if region else None

    filtered: list[ProcurementRecord] = []
    for r in records:
        if agency_lc and agency_lc not in r.agency.lower():
            continue
        if (
            region_lc
            and region_lc not in (r.region or "").lower()
            and region_lc not in r.agency.lower()
        ):
            continue
        if year and r.date_published and r.date_published.year != year:
            continue
        filtered.append(r)

    mode_counts: Counter[str] = Counter()
    region_counts: Counter[str] = Counter()
    agency_totals: defaultdict[str, int] = defaultdict(int)
    for r in filtered:
        mode_counts[r.mode_of_procurement or "Unknown"] += 1
        region_label = _infer_region(r.agency, r.region)
        region_counts[region_label or "unspecified"] += 1
        agency_totals[r.agency] += 1

    publish_dates = [r.date_published for r in filtered if r.date_published]
    reference_from = min(publish_dates).isoformat() if publish_dates else None
    reference_to = max(publish_dates).isoformat() if publish_dates else None

    return {
        "total_count": len(filtered),
        "total_value_php": None,
        "by_mode": dict(mode_counts),
        "by_region": dict(region_counts),
        "top_agencies": [
            {"agency": a, "count": c}
            for a, c in sorted(agency_totals.items(), key=lambda kv: -kv[1])[:10]
        ],
        "reference_period": {"from": reference_from, "to": reference_to},
        "note": (
            "Summary computed over latest ~100 PhilGEPS bid notices (6h cache). "
            "Approved budget totals are not published in the open listing."
        ),
        "rules_evaluated": ["by_mode", "by_region"],
        "rules_not_computable": [
            {
                "rule": "total_value_php",
                "reason": "PhilGEPS open notices do not publish approved budget amounts.",
            }
        ],
        "caveats": [],
        "upstream_error": False,
        "data_status": DATA_STATUS_SUCCESS,
        "source": "PhilGEPS",
        "data_retrieved_at": retrieved_at.isoformat(),
    }
