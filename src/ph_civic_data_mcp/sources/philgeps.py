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

from collections import Counter, defaultdict
from datetime import date as date_cls, datetime, timezone

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from ph_civic_data_mcp.models.procurement import ProcurementRecord
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

PHILGEPS_INDEX_URL = "https://www.philgeps.gov.ph/Indexes/index"


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

    try:
        response = await fetch_with_retry(CLIENT, "GET", PHILGEPS_INDEX_URL)
        response.raise_for_status()
    except Exception as exc:
        log_stderr(f"philgeps fetch error: {exc}")
        cache[key] = []
        return []

    soup = BeautifulSoup(response.text, "lxml")
    tables = soup.find_all("table")
    if not tables:
        cache[key] = []
        return []

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


@mcp.tool()
async def search_procurement(
    keyword: str,
    agency: str | None = None,
    region: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """Search PH government procurement from PhilGEPS open data.

    Note: the PhilGEPS public portal does not expose server-side search for
    external clients, so this tool fetches the latest ~100 bid notices and
    filters them in-memory. Data is cached 6 hours. Keyword/agency/region
    filters are applied client-side (case-insensitive substring match).

    Args:
        keyword: Search term matched against title + agency + classification.
        agency: Partial match on procuring entity name.
        region: PH region filter (partial match).
        date_from / date_to: YYYY-MM-DD bounds on publish date.
        limit: Max results (default 20, max 100).
    """
    limit = max(1, min(int(limit), 100))

    try:
        records = await _fetch_notices()
    except Exception as exc:
        log_stderr(f"search_procurement error: {exc}")
        return []

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


@mcp.tool()
async def get_procurement_summary(
    agency: str | None = None,
    region: str | None = None,
    year: int | None = None,
) -> dict:
    """Aggregate procurement statistics over the latest notices cached from PhilGEPS.

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
        records = []

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
    agency_totals: defaultdict[str, int] = defaultdict(int)
    for r in filtered:
        mode_counts[r.mode_of_procurement or "Unknown"] += 1
        agency_totals[r.agency] += 1

    publish_dates = [r.date_published for r in filtered if r.date_published]
    reference_from = min(publish_dates).isoformat() if publish_dates else None
    reference_to = max(publish_dates).isoformat() if publish_dates else None

    return {
        "total_count": len(filtered),
        "total_value_php": None,
        "by_mode": dict(mode_counts),
        "by_region": {},
        "top_agencies": [
            {"agency": a, "count": c}
            for a, c in sorted(agency_totals.items(), key=lambda kv: -kv[1])[:10]
        ],
        "reference_period": {"from": reference_from, "to": reference_to},
        "note": (
            "Summary computed over latest ~100 PhilGEPS bid notices (6h cache). "
            "Approved budget totals are not published in the open listing."
        ),
        "source": "PhilGEPS",
        "data_retrieved_at": retrieved_at.isoformat(),
    }
