"""PSA OpenSTAT (PXWeb) — population and poverty statistics.

Landmines (from validation log):
- 6: Never hardcode table paths. Discover via browse API.
- The census folders under 1A carry the year in their TITLE ("2024 Census of
  Population"), never in a stable id. PSA moved the 2020 Census from
  `DB/1A/PO/` to `DB/1A/PO_2020/` and left one projection table under `PO/`,
  so population discovery returned nothing for weeks. Discover the folder by
  title, then validate the table's shape before publishing a figure.
- No census table has a Year dimension. `_pick_latest_table` cannot rank them.
- Poverty tables move between subjects. PSA relocated Full-Year Poverty
  Statistics from `DB/1E/FY` to `DB/1F/FY` some time before 2026-08-06, and
  `DB/1E/FY` now 404s. So poverty discovery walks the catalog by title:
  root -> the Poverty subject -> the Full Year subgroup -> the table leaf.
  Never pin the subject id, the subgroup id, or the `.px` id.
"""

from __future__ import annotations

import asyncio
import math
import re
from datetime import datetime, timezone

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.models.population import PopulationStats, PovertyStats
from ph_civic_data_mcp.models.psa import HealthIndicator, InflationStats, LaborStats
from ph_civic_data_mcp.sources.psgc import lookup_psgc_code
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_EMPTY,
    DATA_STATUS_SUCCESS,
    failure_result,
)
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

PSA_API_BASE = "https://openstat.psa.gov.ph/PXWeb/api/v1/en"
PSA_LICENSE = "PSA Open Data terms (Philippine Statistics Authority, OpenSTAT)"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# PSA publishes a request cap on its API guide: 10 requests per 10 seconds.
# The guide sits behind a Cloudflare challenge that no headless client can
# read, so that figure comes from the project's own record, not a live quote.
#
# A token bucket, not a sliding window. A cold get_area_profile makes about 11
# OpenSTAT calls in one asyncio.gather, and a strict window stalls that whole
# fan-out for a full 10 seconds on the flagship tool. The bucket holds the same
# sustained rate of one request per second, but it lets a burst of 10 through
# at once, so the same cold profile pays about 1 second instead of 10.
PSA_RATE_LIMIT_REQUESTS = 10
PSA_RATE_LIMIT_WINDOW_SECONDS = 10.0
PSA_REFILL_PER_SECOND = PSA_RATE_LIMIT_REQUESTS / PSA_RATE_LIMIT_WINDOW_SECONDS

_RATE_LOCK = asyncio.Lock()
_TOKENS = float(PSA_RATE_LIMIT_REQUESTS)
_LAST_REFILL: float | None = None


def _reset_rate_limiter() -> None:
    """Refill the bucket. For tests, and for a fresh process."""
    global _TOKENS, _LAST_REFILL
    _TOKENS = float(PSA_RATE_LIMIT_REQUESTS)
    _LAST_REFILL = None


async def _psa_rate_limit() -> None:
    """Hold an OpenSTAT request back until the bucket has a token for it."""
    global _TOKENS, _LAST_REFILL
    loop = asyncio.get_running_loop()

    async with _RATE_LOCK:
        now = loop.time()
        if _LAST_REFILL is None:
            _LAST_REFILL = now
        _TOKENS = min(
            float(PSA_RATE_LIMIT_REQUESTS),
            _TOKENS + (now - _LAST_REFILL) * PSA_REFILL_PER_SECOND,
        )
        _LAST_REFILL = now
        if _TOKENS >= 1.0:
            _TOKENS -= 1.0
            wait = 0.0
        else:
            # Reserve the token this call will consume, and push the refill
            # clock forward, so a concurrent caller queues behind it instead of
            # racing for the same token.
            wait = (1.0 - _TOKENS) / PSA_REFILL_PER_SECOND
            _TOKENS = 0.0
            _LAST_REFILL = now + wait

    # Sleep outside the lock. Holding it here would serialize every gathered
    # PSA call behind one wait and defeat the fan-out in the composites.
    if wait > 0:
        await asyncio.sleep(wait)


class PSAUpstreamError(RuntimeError):
    """An OpenSTAT call failed. The message carries the real error for caveats.

    Raised instead of returning an empty list so no caller can write "no
    tables" into a 24h TTL cache. See utils/envelope.py for the contract.
    """


class PSANotFoundError(PSAUpstreamError):
    """OpenSTAT answered 404. The path is wrong, so retrying will not help.

    A subclass, so every existing `except PSAUpstreamError` still catches it,
    but a caller that cares can tell a caller mistake from an outage.
    """


async def _get_json(url: str) -> dict | list | None:
    try:
        await _psa_rate_limit()
        response = await fetch_with_retry(CLIENT, "GET", url)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        log_stderr(f"PSA fetch error for {url}: {exc}")
        return None


async def _get_json_or_raise(url: str) -> dict | list:
    try:
        await _psa_rate_limit()
        response = await fetch_with_retry(CLIENT, "GET", url)
        if response.status_code == 404:
            raise PSANotFoundError(f"OpenSTAT has no such path: {url}")
        response.raise_for_status()
        return response.json()
    except PSAUpstreamError:
        raise
    except Exception as exc:
        log_stderr(f"PSA fetch error for {url}: {exc}")
        raise PSAUpstreamError(f"{type(exc).__name__}: {exc}") from exc


async def _post_json_or_raise(url: str, query: dict) -> dict:
    try:
        await _psa_rate_limit()
        response = await fetch_with_retry(CLIENT, "POST", url, json=query)
        response.raise_for_status()
        payload = response.json()
    except PSAUpstreamError:
        raise
    except Exception as exc:
        log_stderr(f"PSA POST error for {url}: {exc}")
        raise PSAUpstreamError(f"{type(exc).__name__}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PSAUpstreamError(f"PSA query of {url} returned a non-object body")
    return payload


async def _post_json(url: str, query: dict) -> dict | None:
    try:
        await _psa_rate_limit()
        response = await fetch_with_retry(CLIENT, "POST", url, json=query)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        log_stderr(f"PSA POST error for {url}: {exc}")
        return None


# Discovered table locations live in a TTL cache, never a bare dict. A process
# that ran for weeks kept serving a table PSA had since moved.
_DISCOVERY_CACHE = CACHES["psa_discovery"]

# ---------------------------------------------------------------------------
# Population: the Census of Population, discovered by title under subject 1A
# ---------------------------------------------------------------------------
#
# PSA moved the 2020 Census from `DB/1A/PO/` to `DB/1A/PO_2020/` and left one
# projection table under `PO/`, so a pinned path found nothing for weeks. The
# 2024 Census sits under `DB/1A/PO_2024/` with PSGC-coded geography down to
# barangay level. Nothing below pins a folder id or a table id: the census
# folders come from their titles, the summary table from its title plus a
# shape check, and the regional barangay tables from the region each title
# names after its colon.

_CENSUS_FOLDER_TITLE = re.compile(r"\b((?:19|20)\d{2})\b.*\bcensus of population\b", re.IGNORECASE)

# Reference dates PSA states for each census. The 2024 barangay tables carry
# theirs in the title ("as of 01 July 2024"); the summary tables do not, so
# these are the published values, keyed by census year.
CENSUS_REFERENCE_DATES: dict[int, str] = {
    2010: "2010-05-01",
    2015: "2015-08-01",
    2020: "2020-05-01",
    2024: "2024-07-01",
}

# Words a census summary table must not carry. Each names a sibling table in
# the same folder that also says "Total Population" and "Region".
_SUMMARY_TITLE_MUST_NOT = (
    "barangay",
    # "urban" alone would reject every title, because they all say "Highly
    # Urbanized City". The urban-classification tables say "Urban Population".
    "urban population",
    "age group",
    "age-group",
    "growth",
    "density",
    "land area",
    "housing unit",
)

# ASCII digits only. `\d` accepts Unicode digits, which no PSGC code carries.
_PSGC_CODE = re.compile(r"^[0-9]{9}[0-9]?$")
_LEADING_DOTS = re.compile(r"^\.+")
_TRAILING_FOOTNOTE = re.compile(r"\s+(?:\d+/|/[a-z]|\d*\*+)$")
_PHILIPPINES_FOOTNOTE = re.compile(r"^(philippines)\s+[a-z]$", re.IGNORECASE)


def _clean_geo_label(text: str) -> str:
    """Strip PXWeb indentation dots and PSA footnote markers from a geography label.

    "..Negros Island Region (NIR) 3/" -> "Negros Island Region (NIR)".
    "Philippines a" -> "Philippines". "PHILIPPINES /a" -> "PHILIPPINES".
    """
    s = _LEADING_DOTS.sub("", str(text)).strip()
    s = _TRAILING_FOOTNOTE.sub("", s).strip()
    match = _PHILIPPINES_FOOTNOTE.match(s)
    if match:
        s = match.group(1)
    return s


def _geo_depth(text: str) -> int:
    """Indentation depth of a PXWeb geography label, from its leading dots."""
    return len(str(text)) - len(_LEADING_DOTS.sub("", str(text)))


def _geo_level_from_label(text: str) -> str:
    """Administrative level of a census row, from its indentation and wording.

    0 dots is the national total, 2 a region, 4 a province or a highly
    urbanized city (NCR cities take the province slot). The barangay tables
    go on to 6 dots for a city or municipality and 8 for a barangay.
    """
    depth = _geo_depth(text)
    label = _clean_geo_label(text).lower()
    if depth == 0:
        return "national"
    if depth <= 2:
        return "region"
    if depth <= 4:
        if label.startswith("city of ") or label.endswith(" city"):
            return "highly_urbanized_city"
        return "province"
    if depth <= 6:
        return "city_or_municipality"
    return "barangay"


def _level_from_psgc10(code: str) -> str:
    """Administrative level a 10-digit PSGC code encodes: RR PPP MM BBB."""
    if code[7:] != "000":
        return "barangay"
    if code[5:7] != "00":
        return "city_or_municipality"
    if code[2:5] != "000":
        return "province"
    if code[:2] != "00":
        return "region"
    return "national"


def _summary_title_matches(title: str) -> bool:
    t = title.lower()
    if "total population" not in t or "region" not in t:
        return False
    return not any(bad in t for bad in _SUMMARY_TITLE_MUST_NOT)


def _barangay_title_matches(title: str) -> bool:
    t = title.lower()
    return "total population" in t and "barangay" in t and "urban population" not in t


def _population_layout(meta: object) -> dict | None:
    """Codes needed to query a census table, or None when its shape is wrong.

    A table passes only when it declares a geography variable and a Parameter
    variable with a "Total Population" value. `geo_values` is empty for the
    barangay tables: PXWeb leaves the value list out of their metadata, so a
    row there is reachable only by a PSGC code the caller already knows.
    """
    if not isinstance(meta, dict):
        return None
    geo = _var_by_code(meta, "geographic", "geolocation")
    param = _var_by_code(meta, "parameter")
    if geo is None or param is None:
        return None
    total_code: str | None = None
    for code, text in zip(param.get("values") or [], param.get("valueTexts") or []):
        if str(text).strip().lower().startswith("total population"):
            total_code = str(code)
            break
    if total_code is None:
        return None
    values = geo.get("values")
    texts = geo.get("valueTexts")
    geo_values = [str(v) for v in values] if isinstance(values, list) else []
    geo_texts = [str(t) for t in texts] if isinstance(texts, list) else []
    return {
        "geo_code": str(geo.get("code") or "Geographic Location"),
        "param_code": str(param.get("code") or "Parameter"),
        "total_code": total_code,
        "geo_values": geo_values,
        "geo_texts": geo_texts,
        "has_national": any("philippines" in t.lower() for t in geo_texts),
        "psgc_coded": bool(geo_values)
        and all(len(v) == 10 and v.isascii() and v.isdigit() for v in geo_values),
    }


async def _discover_census_vintages() -> dict[int, str]:
    """{census_year: folder path under DB/} for every census folder in subject 1A.

    Read from the folder titles ("2024 Census of Population"), never from the
    folder ids, because PSA renamed the ids once already.
    """
    cached = _DISCOVERY_CACHE.get("census_vintages")
    if cached is not None:
        return cached
    entries = await _browse("1A")
    vintages: dict[int, str] = {}
    titles: dict[int, str] = {}
    for entry in entries:
        if entry.get("type") == "t" or not entry.get("id"):
            continue
        title = str(entry.get("text") or "").strip()
        match = _CENSUS_FOLDER_TITLE.search(title)
        if match:
            year = int(match.group(1))
            vintages[year] = f"1A/{entry['id']}"
            titles[year] = title
    if not vintages:
        raise PSAUpstreamError(
            "No 'Census of Population' folder under PSA OpenSTAT subject 1A; the catalog moved."
        )
    _DISCOVERY_CACHE["census_titles"] = titles
    _DISCOVERY_CACHE["census_vintages"] = vintages
    return vintages


def _census_title_for(year: int) -> str:
    titles = _DISCOVERY_CACHE.get("census_titles") or {}
    return str(titles.get(year) or f"{year} Census of Population")


async def _discover_census_summary_table(year: int, folder: str) -> tuple[str, dict, dict]:
    """(table_url, meta, layout) for the census table with national, region and province rows.

    Title first, then shape: the table must declare a geography variable with
    a Philippines row and a Parameter variable with Total Population. A table
    that matches the title but fails the shape is skipped, and a folder where
    none passes raises, so a wrong table can never publish a figure.
    """
    slot = f"census_summary::{year}"
    cached = _DISCOVERY_CACHE.get(slot)
    if cached is not None:
        return cached
    entries = await _browse(folder)
    rejected: list[str] = []
    for entry in entries:
        if entry.get("type") != "t":
            continue
        title = str(entry.get("text") or "")
        if not _summary_title_matches(title):
            continue
        table_url = f"{PSA_API_BASE}/DB/{folder}/{entry.get('id')}"
        meta = await _get_json_or_raise(table_url)
        layout = _population_layout(meta)
        if layout is None or not layout["has_national"]:
            rejected.append(title)
            continue
        found = (table_url, meta, layout)
        _DISCOVERY_CACHE[slot] = found
        return found
    raise PSAUpstreamError(
        f"No table under PSA {folder} passed validation as the {year} census summary "
        "(title, geography with a Philippines row, Total Population measure). "
        f"Rejected: {rejected or 'none matched the title'}."
    )


def _region_names(label: str) -> set[str]:
    """Names a region label answers to: the text outside and inside its parentheses.

    "Region VIII (Eastern Visayas)" -> {"region viii", "viii", "eastern visayas"}.
    "MIMAROPA Region" -> {"mimaropa region", "mimaropa"}. Pairs a summary-table
    region row with the barangay table that names that region in its title,
    where PSA writes one region three different ways (and once with a typo).
    """
    text = _clean_geo_label(label).lower()
    names: set[str] = set()
    for inner in re.findall(r"\(([^)]*)\)", text):
        inner = " ".join(inner.split())
        if inner:
            names.add(inner)
    outer = " ".join(re.sub(r"\([^)]*\)", " ", text).split())
    if outer:
        names.add(outer)
        stripped = " ".join(re.sub(r"\bregion\b", " ", outer).split())
        if stripped and stripped != outer:
            names.add(stripped)
    return names


async def _discover_barangay_table(region_label: str, folder: str) -> tuple[str, dict, dict] | None:
    """(table_url, meta, layout) for the barangay-level table of one region, or None.

    The barangay tables carry no geography values in their metadata, so the
    only handle is the region named after the colon in each title.
    """
    slot = f"census_barangay::{folder}::{_clean_geo_label(region_label).lower()}"
    cached = _DISCOVERY_CACHE.get(slot)
    if cached is not None:
        return cached
    wanted = _region_names(region_label)
    entries = await _browse(folder)
    for entry in entries:
        if entry.get("type") != "t":
            continue
        title = str(entry.get("text") or "")
        if not _barangay_title_matches(title):
            continue
        suffix = title.rsplit(":", 1)[-1] if ":" in title else ""
        if not (_region_names(suffix) & wanted):
            continue
        table_url = f"{PSA_API_BASE}/DB/{folder}/{entry.get('id')}"
        meta = await _get_json_or_raise(table_url)
        layout = _population_layout(meta)
        if layout is None:
            continue
        found = (table_url, meta, layout)
        _DISCOVERY_CACHE[slot] = found
        return found
    return None


async def _query_total_population(table_url: str, layout: dict, geo_value: str) -> float | None:
    """One bounded POST for the Total Population cell of one geography code.

    Raises PSAUpstreamError on a transport failure. Returns None when PXWeb
    answers with no row, which is how it reports a code the table lacks.
    """
    query = {
        "query": [
            {"code": layout["geo_code"], "selection": {"filter": "item", "values": [geo_value]}},
            {
                "code": layout["param_code"],
                "selection": {"filter": "item", "values": [layout["total_code"]]},
            },
        ],
        "response": {"format": "json"},
    }
    payload = await _post_json_or_raise(table_url, query)
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    return _first_cell(payload)


def _containing_region(layout: dict, index: int) -> str | None:
    """Label of the nearest region row above `index` in a summary table.

    None for the national row and for a region row itself: a region has no
    parent region, and the national total sits above every region.
    """
    if _geo_depth(layout["geo_texts"][index]) <= 2:
        return None
    for i in range(index, -1, -1):
        if _geo_depth(layout["geo_texts"][i]) == 2:
            return _clean_geo_label(layout["geo_texts"][i])
    return None


def _region_row_for_code(layout: dict, code10: str) -> tuple[int, str] | None:
    """(index, raw label) of the region row a 10-digit PSGC code belongs to."""
    wanted = code10[:2] + "00000000"
    for i, value in enumerate(layout["geo_values"]):
        if value == wanted:
            return i, layout["geo_texts"][i]
    return None


def _region_field(level: str, geography: str | None, parent: str | None, fallback: str) -> str:
    """Value of the compatibility `region` field.

    For a national or regional row it is the row itself. For anything below a
    region it names the containing region, which is what the field always
    claimed to hold.
    """
    if level in ("national", "region") and geography:
        return geography
    return parent or geography or fallback


def _first_cell(payload: dict) -> float | None:
    """First numeric cell of a PXWeb response, or None.

    Indexing `values[0]` directly turns a wrong-typed string like "10.9" into
    its first character, "1", and publishes that as a statistic.
    """
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    row = data[0]
    if not isinstance(row, dict):
        return None
    values = row.get("values")
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        return None
    return _to_float(values[0]) if values else None


def _year_from_label(label: str) -> int | None:
    """Parse a 4-digit year out of a PSA dimension label, or return None.

    Returning None matters: the old code fell back to a hardcoded 2023, which
    published a reference period nobody measured.
    """
    digits = "".join(ch for ch in str(label) if ch.isdigit())
    if len(digits) < 4:
        return None
    year = int(digits[:4])
    return year if 1900 < year < 2100 else None


def _pick_folder(entries: list[dict], exact: str, *needles: str) -> dict | None:
    """Pick a catalog folder by title. An exact title wins over a partial match.

    The root carries both "Poverty" and "Living Conditions, Poverty and
    Cross-cutting Social Issues", so a bare substring match picks the wrong one.
    """
    folders = [e for e in entries if e.get("type") != "t"]
    for entry in folders:
        if (entry.get("text") or "").strip().lower() == exact:
            return entry
    for entry in folders:
        text = (entry.get("text") or "").lower()
        if all(n in text for n in needles):
            return entry
    return None


async def _discover_fy_poverty_path() -> str:
    """Walk the live catalog to the Full Year Poverty Statistics folder.

    Returns a relative path such as "1F/FY". PSA moved this subtree once
    already, so nothing here is hardcoded except the folder titles.
    """
    cached = _DISCOVERY_CACHE.get("poverty_fy_path")
    if cached is not None:
        return cached  # type: ignore[return-value]

    roots = await _browse("")
    subject = _pick_folder(roots, "poverty", "poverty")
    if subject is None:
        raise PSAUpstreamError(
            "No Poverty subject in the PSA OpenSTAT root listing; the catalog moved."
        )

    subject_id = subject["id"]
    groups = await _browse(subject_id)
    full_year = _pick_folder(groups, "full year poverty statistics", "full year", "poverty")
    if full_year is None:
        raise PSAUpstreamError(
            f"No Full Year Poverty Statistics folder under PSA subject {subject_id}."
        )

    path = f"{subject_id}/{full_year['id']}"
    _DISCOVERY_CACHE["poverty_fy_path"] = path  # type: ignore[assignment]
    return path


async def _discover_fy_poverty_entries() -> tuple[str, list[dict]]:
    """Browse the Full Year folder once; poverty and subsistence both reuse it."""
    path = await _discover_fy_poverty_path()
    return path, await _browse(path)


async def _discover_poverty_like_table(
    cache_slot: str, table_prefix: str, *needles: str
) -> tuple[str, dict]:
    cached = _DISCOVERY_CACHE.get(cache_slot)
    if cached is not None:
        return cached
    path, tables = await _discover_fy_poverty_entries()
    for entry in tables:
        text = (entry.get("text") or "").lower()
        if not text.startswith(table_prefix):
            continue
        if not all(n in text for n in needles):
            continue
        table_url = f"{PSA_API_BASE}/DB/{path}/{entry.get('id')}"
        meta = await _get_json_or_raise(table_url)
        if isinstance(meta, dict):
            found = (table_url, meta)
            _DISCOVERY_CACHE[cache_slot] = found
            return found
    raise PSAUpstreamError(
        f"No '{table_prefix.strip()}' table under PSA {path}; the table titles changed."
    )


async def _discover_poverty_table() -> tuple[str, dict]:
    return await _discover_poverty_like_table(
        "poverty", "table 1.", "poverty incidence", "families"
    )


async def _discover_subsistence_table() -> tuple[str, dict]:
    return await _discover_poverty_like_table(
        "subsistence", "table 3.", "subsistence incidence", "families"
    )


_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")


def _token_match(needle: str, haystack: str) -> bool:
    """Match on whole tokens only, never on a fragment.

    Two failures this closes. A bare `needle in haystack` let the region code
    "I" match "philippines", so a caller asking for Region I got the national
    figure under their region's name. Falling back to a raw substring for a
    multi-word needle let "region i" match "region ii" the same way.

    Both queries are now compared token by token, so "region i" matches
    ["region", "i"] and never ["region", "ii"].
    """
    if not needle:
        return False
    want = [tok for tok in _TOKEN_SPLIT.split(needle) if tok]
    have = [tok for tok in _TOKEN_SPLIT.split(haystack) if tok]
    if not want or len(want) > len(have):
        return False
    return any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1))


def _find_geo_value(meta: dict, region: str | None, geo_code: str) -> tuple[str, str] | None:
    """Return (value_code, value_text) matching the requested region in the geo variable.

    geo_code is either "Geographic Location" or "Geolocation" depending on table.
    """
    for var in meta.get("variables", []):
        code = var.get("code") or var.get("text", "")
        if code.lower() != geo_code.lower() and geo_code.lower() not in code.lower():
            continue
        values = var.get("values", [])
        texts = var.get("valueTexts", [])
        if region is None:
            for val, txt in zip(values, texts):
                if "philippines" in txt.lower():
                    return val, txt
            # zip stops at the shorter list, so a short valueTexts cannot make
            # texts[0] raise the way a bare index would.
            for val, txt in zip(values, texts):
                return val, txt
            # An empty values list used to fall through to region.strip() and
            # raise AttributeError on None.
            return None
        region_norm = region.strip().lower()
        for val, txt in zip(values, texts):
            t_norm = txt.lower().strip(" .")
            if region_norm == t_norm:
                return val, txt.strip(" .")
        # Substring matching only on a token boundary. Plain `in` let the
        # one-letter region "I" match "philippines" and hand back national
        # figures under a regional label.
        for val, txt in zip(values, texts):
            t_norm = txt.lower().strip(" .")
            if _token_match(region_norm, t_norm):
                return val, txt.strip(" .")
        # try matching against region codes (I, II, III, NCR, CAR, BARMM)
        # PSA does not label every region the way people name it. A live probe
        # of the 108-entry geolocation list found these four with no match.
        aliases = {
            "ncr": "national capital",
            "metro manila": "national capital",
            "car": "cordillera",
            "barmm": "bangsamoro",
            "region iv-b": "mimaropa",
            "iv-b": "mimaropa",
            "region iv-a": "calabarzon",
            "iv-a": "calabarzon",
        }
        target = aliases.get(region_norm, region_norm)
        for val, txt in zip(values, texts):
            if _token_match(target, txt.lower()):
                return val, txt.strip(" .")
    return None


def _variable_values(meta: dict, code_match: str) -> tuple[str, list[str], list[str]]:
    """Return (code_exact, values, texts) for first variable whose code contains code_match."""
    for var in meta.get("variables", []):
        code = var.get("code", "") or var.get("text", "")
        if code_match.lower() in code.lower():
            return code, var.get("values", []), var.get("valueTexts", [])
    return "", [], []


@mcp.tool(
    title="Philippine population statistics",
    tags={"openstat", "philippines", "population", "psa", "census"},
    annotations={
        "title": "Philippine population statistics",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_population_stats(
    region: str | None = None,
    year: int | None = None,
    psgc_code: str | None = None,
) -> dict:
    """Population from the PSA Census of Population, discovered live on OpenSTAT.

    Defaults to the latest census PSA publishes: the 2024 Census of Population,
    reference date 2024-07-01, as of 2026-09. Pass `year` for an older census
    (2010, 2015, 2020). Every result names its census, reference date and
    geography level, and for the 2024 tables the PSGC code PSA keyed the row
    on. Examples:

      get_population_stats()                       national total, latest census
      get_population_stats(region="NCR")           one region, province or HUC by PSA label
      get_population_stats(region="Cebu", year=2020)
      get_population_stats(psgc_code="0831600000") City of Tacloban, 2024 Census
      get_population_stats(psgc_code="083747000")  same place, 9-digit code
      get_population_stats(psgc_code="1380100001") one barangay, 2024 Census

    On an OpenSTAT outage: population null, upstream_error true, data_status
    "unavailable", the real error in caveats. On a bad argument:
    validation_error true, data_status "invalid_request". A code no census
    table carries: data_status "empty". Failures are never cached.

    Args:
        region: Region, province or highly urbanized city as PSA labels it,
            such as "NCR", "Region VII", "CAR", "BARMM", "Leyte",
            "City of Manila". None returns the national total.
        year: Census year. None picks the latest. A year PSA has no census
            for returns validation_error with available_vintages.
        psgc_code: A 9- or 10-digit PSGC code from resolve_ph_location.
            Reaches cities, municipalities and barangays (2024 Census only).
            Not combined with region.

    Returns: population, year, census, reference_date, geography,
    geography_level, psgc_code, region, parent_region, available_vintages,
    source_table, data_status, caveats, source, source_url, license,
    data_retrieved_at.
    """
    label_arg = region or "Philippines"
    subject_url = f"{PSA_API_BASE}/DB/1A/"

    def _invalid(msg: str, **extra: object) -> dict:
        return failure_result(
            "PSA",
            subject_url,
            msg,
            license=PSA_LICENSE,
            validation_error=True,
            region=label_arg,
            population=None,
            year=year,
            **extra,
        )

    def _down(msg: str, **extra: object) -> dict:
        return failure_result(
            "PSA",
            subject_url,
            msg,
            license=PSA_LICENSE,
            region=label_arg,
            population=None,
            year=year,
            **extra,
        )

    if region and psgc_code is not None:
        return _invalid("Pass either region or psgc_code, not both.")
    code: str | None = None
    if psgc_code is not None:
        code = psgc_code.strip()
        if not _PSGC_CODE.match(code):
            return _invalid(
                f"psgc_code {psgc_code!r} is not a 9- or 10-digit PSGC code. "
                "Get one from resolve_ph_location."
            )

    key = cache_key({"tool": "population", "region": region, "year": year, "psgc_code": code})
    cache = CACHES["psa_population"]
    if key in cache:
        return cache[key]

    try:
        vintages = await _discover_census_vintages()
    except PSAUpstreamError as exc:
        return _down(f"PSA census discovery failed: {exc}")
    available = sorted(vintages)
    chosen = year if year is not None else available[-1]
    if chosen not in vintages:
        return _invalid(
            f"PSA OpenSTAT has no Census of Population for {year}. "
            f"Available census years: {available}.",
            available_vintages=available,
        )
    folder = vintages[chosen]

    try:
        summary_url, summary_meta, summary = await _discover_census_summary_table(chosen, folder)
    except PSAUpstreamError as exc:
        return _down(f"PSA census summary discovery failed: {exc}", available_vintages=available)

    caveats: list[str] = []
    cacheable = True
    geography: str | None
    level: str
    out_code: str | None
    parent_region: str | None
    table_url = summary_url
    layout = summary

    if code is None:
        geo_hit = _find_geo_value(summary_meta, region, summary["geo_code"])
        if geo_hit is None:
            return _invalid(
                f"Region '{region}' not found in the PSA {chosen} census geography. "
                "Use a PSA label such as 'NCR', 'Region VII', 'CAR', 'Leyte', or pass "
                "psgc_code from resolve_ph_location.",
                available_vintages=available,
            )
        geo_value = geo_hit[0]
        index = summary["geo_values"].index(geo_value)
        raw_label = summary["geo_texts"][index]
        geography = _clean_geo_label(raw_label)
        level = _geo_level_from_label(raw_label)
        parent_region = _containing_region(summary, index)
        out_code = geo_value if summary["psgc_coded"] else None
    else:
        record: dict | None = None
        try:
            record = await lookup_psgc_code(code)
        except Exception as exc:
            log_stderr(f"PSGC lookup failed for {code}: {exc}")
            caveats.append(
                f"PSGC name lookup unavailable ({type(exc).__name__}: {exc}); "
                "geography name omitted."
            )
            cacheable = False
        if len(code) == 9:
            code10 = str((record or {}).get("psgc_10digit_code") or "")
            if not code10:
                if not cacheable:
                    return _down(
                        f"Cannot widen 9-digit PSGC code {code} without the PSGC mirror. "
                        "Retry later or pass the 10-digit code.",
                        available_vintages=available,
                    )
                return _invalid(
                    f"PSGC code {code} is unknown to the PSGC mirror.", available_vintages=available
                )
        else:
            code10 = code
        geography = (record or {}).get("name") or None
        level = str((record or {}).get("level") or _level_from_psgc10(code10))
        out_code = code10

        if not summary["psgc_coded"]:
            # An older census keys geography on sequential codes, so the only
            # bridge is the PSGC record's name, and only down to province/HUC.
            if _level_from_psgc10(code10) in ("city_or_municipality", "barangay"):
                return _invalid(
                    "City, municipality and barangay populations exist in the 2024 Census "
                    f"only; PSA's {chosen} tables stop at province and highly urbanized city. "
                    "Omit year or pass year=2024.",
                    available_vintages=available,
                )
            if not geography:
                return _down(
                    f"The {chosen} census keys geography by label, and the PSGC mirror "
                    f"could not name code {code}.",
                    available_vintages=available,
                )
            geo_hit = _find_geo_value(summary_meta, geography, summary["geo_code"])
            if geo_hit is None:
                return _invalid(
                    f"'{geography}' (PSGC {code}) is not a row in the PSA {chosen} census "
                    "summary table.",
                    available_vintages=available,
                )
            geo_value = geo_hit[0]
            index = summary["geo_values"].index(geo_value)
            raw_label = summary["geo_texts"][index]
            geography = _clean_geo_label(raw_label)
            level = _geo_level_from_label(raw_label)
            parent_region = _containing_region(summary, index)
            out_code = None
        elif code10 in summary["geo_values"]:
            geo_value = code10
            index = summary["geo_values"].index(geo_value)
            raw_label = summary["geo_texts"][index]
            geography = _clean_geo_label(raw_label)
            level = _geo_level_from_label(raw_label)
            parent_region = _containing_region(summary, index)
        else:
            region_row = _region_row_for_code(summary, code10)
            if region_row is None:
                return _invalid(
                    f"PSGC code {code10} names a region ({code10[:2]}) that the PSA {chosen} "
                    "census summary does not list.",
                    available_vintages=available,
                )
            parent_region = _clean_geo_label(region_row[1])
            try:
                found = await _discover_barangay_table(region_row[1], folder)
            except PSAUpstreamError as exc:
                return _down(
                    f"PSA barangay table discovery failed: {exc}", available_vintages=available
                )
            if found is None:
                return _down(
                    f"No barangay-level {chosen} census table for {parent_region} under PSA "
                    f"{folder}; the table titles changed.",
                    available_vintages=available,
                )
            table_url, _meta, layout = found
            geo_value = code10

    try:
        raw_population = await _query_total_population(table_url, layout, geo_value)
    except PSAUpstreamError as exc:
        return _down(
            f"PSA PXWeb population query failed: {exc}",
            available_vintages=available,
            source_table=table_url,
        )
    if raw_population is None and code is not None and table_url != summary_url:
        # PXWeb answers a code its table lacks with zero rows, not an error.
        return failure_result(
            "PSA",
            table_url,
            f"PSGC code {code10} has no row in the PSA {chosen} census table for "
            f"{parent_region}. The census keyed this table on the PSGC edition current at "
            "its reference date, so a code created or retired since may be absent.",
            license=PSA_LICENSE,
            validation_error=True,
            data_status=DATA_STATUS_EMPTY,
            region=parent_region or label_arg,
            population=None,
            year=chosen,
            psgc_code=code10,
            available_vintages=available,
            source_table=table_url,
        )
    if raw_population is None:
        return _down(
            "PSA PXWeb returned no readable population cell. That is a query or parse "
            "failure, never a population of zero.",
            available_vintages=available,
            source_table=table_url,
        )
    if raw_population != int(raw_population):
        caveats.append(
            "PSA returned a non-integral population cell; rounded to the nearest whole person."
        )
    population = int(round(raw_population))

    reference_date = CENSUS_REFERENCE_DATES.get(chosen)
    census_title = _census_title_for(chosen)
    stats = PopulationStats(
        region=_region_field(level, geography, parent_region, label_arg),
        year=chosen,
        population=population,
        geography=geography,
        geography_level=level,
        psgc_code=out_code,
        census=census_title,
        reference_date=reference_date,
        reference_note=(
            f"PSA {census_title}, reference date {reference_date or 'see table'}. "
            f"Latest census PSA publishes on OpenSTAT: {available[-1]}."
        ),
    )
    result = {
        **stats.model_dump(mode="json"),
        "parent_region": parent_region,
        "available_vintages": available,
        "data_status": DATA_STATUS_SUCCESS,
        "upstream_error": False,
        "validation_error": False,
        "caveats": caveats,
        "source_url": table_url,
        "source_table": table_url,
        "license": PSA_LICENSE,
        "data_retrieved_at": _now().isoformat(),
    }
    if cacheable:
        cache[key] = result
    return result


@mcp.tool(
    title="Philippine poverty incidence",
    tags={"openstat", "philippines", "poverty", "psa"},
    annotations={
        "title": "Philippine poverty incidence",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_poverty_stats(region: str | None = None) -> dict:
    """Poverty incidence from PSA (latest: 2023 Full-Year).

    Args:
        region: PH region (None returns national).
    """
    key = cache_key({"tool": "poverty", "region": region})
    cache = CACHES["psa_poverty"]
    if key in cache:
        return cache[key]

    def _err(msg: str) -> dict:
        # Never cached: a transient PXWeb failure must not pin a null poverty
        # figure for the 24h success TTL.
        return {
            "region": region or "Philippines",
            "poverty_incidence_pct": None,
            "upstream_error": True,
            "caveats": [msg],
            "source": "PSA",
            "source_url": f"{PSA_API_BASE}/DB/",
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    try:
        table_url, meta = await _discover_poverty_table()
    except PSAUpstreamError as exc:
        return _err(f"PSA poverty table discovery failed: {exc}")

    subsistence: tuple[str, dict] | None
    partial: list[str] = []
    try:
        subsistence = await _discover_subsistence_table()
    except PSAUpstreamError as exc:
        subsistence = None
        log_stderr(f"PSA subsistence discovery failed: {exc}")
        # Surfaced, not swallowed. A null subsistence figure otherwise reads as
        # "PSA does not publish this" and caches that for 24h.
        partial.append(f"Subsistence table unavailable: {exc}")

    geo_hit = _find_geo_value(meta, region, "Geolocation")
    if geo_hit is None:
        return {
            "region": region or "Philippines",
            "caveats": [f"Region '{region}' not found in PSA poverty table"],
            "source": "PSA",
            "data_retrieved_at": _now().isoformat(),
        }
    geo_val, geo_text = geo_hit

    measure_code, measure_values, measure_texts = _variable_values(meta, "Incidence")
    if not measure_values:
        # Indexing [0] here raised IndexError and killed the whole call.
        return _err("PSA poverty table declares no Incidence dimension; its schema changed.")
    incidence_val = measure_values[0]
    for val, txt in zip(measure_values, measure_texts):
        if "poverty incidence" in txt.lower() and "famil" in txt.lower():
            incidence_val = val
            break

    year_code, year_values, year_texts = _variable_values(meta, "Year")
    year_val = year_values[-1] if year_values else "0"
    year_text = year_texts[-1] if year_texts else "latest"
    year_int = _year_from_label(year_text)
    if year_int is None:
        # Hardcoding 2023 here published a reference period nobody measured.
        return _err(
            f"PSA poverty table year label {year_text!r} is not a year; the table schema changed."
        )

    query = {
        "query": [
            {"code": "Geolocation", "selection": {"filter": "item", "values": [geo_val]}},
            {
                "code": measure_code or "Threshold/Incidence/Measures of Precision",
                "selection": {"filter": "item", "values": [incidence_val]},
            },
            {
                "code": year_code or "Year",
                "selection": {"filter": "item", "values": [year_val]},
            },
        ],
        "response": {"format": "json"},
    }
    try:
        payload = await _post_json_or_raise(table_url, query)
    except PSAUpstreamError as exc:
        return _err(f"PSA poverty query failed: {exc}")

    poverty_pct = _first_cell(payload)

    if poverty_pct is None:
        # The call worked, so this is a real gap in PSA's data, not an outage.
        # _err would say upstream_error and send the agent back to retry.
        return {
            "region": geo_text,
            "poverty_incidence_pct": None,
            "caveats": [
                "PSA published no poverty figure for this area and year "
                f"({year_text}). PSA writes '..' for a cell it does not publish."
            ],
            "source": "PSA",
            "source_table": table_url,
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    subsistence_pct: float | None = None
    if subsistence is not None:
        sub_url, sub_meta = subsistence
        sub_geo = _find_geo_value(sub_meta, region, "Geolocation")
        sub_measure_code, sub_mv, sub_mt = _variable_values(sub_meta, "Incidence")
        if sub_geo and sub_mv:
            sub_incidence_val = sub_mv[0]
            for v, t in zip(sub_mv, sub_mt):
                if "subsistence" in t.lower() and "famil" in t.lower():
                    sub_incidence_val = v
                    break
            sub_year_code, sub_yv, sub_yt = _variable_values(sub_meta, "Year")
            sub_year_val = sub_yv[-1] if sub_yv else "0"
            sub_year_int = _year_from_label(sub_yt[-1]) if sub_yt else None
            # The two tables can publish different latest years. Reporting the
            # subsistence figure under the poverty table's reference year would
            # misstate its vintage, so withhold it instead. An unreadable label
            # counts as a mismatch: an unknown year is not a matching one.
            if sub_year_int != year_int:
                seen = (
                    sub_year_int if sub_year_int is not None else (sub_yt[-1] if sub_yt else None)
                )
                partial.append(
                    f"Subsistence table's latest year is {seen!r}, not {year_int}. "
                    "The subsistence figure is withheld rather than mislabelled."
                )
            else:
                sub_query = {
                    "query": [
                        {
                            "code": "Geolocation",
                            "selection": {"filter": "item", "values": [sub_geo[0]]},
                        },
                        {
                            "code": sub_measure_code,
                            "selection": {"filter": "item", "values": [sub_incidence_val]},
                        },
                        {
                            "code": sub_year_code or "Year",
                            "selection": {"filter": "item", "values": [sub_year_val]},
                        },
                    ],
                    "response": {"format": "json"},
                }
                try:
                    sub_payload = await _post_json_or_raise(sub_url, sub_query)
                except PSAUpstreamError as exc:
                    sub_payload = None
                    partial.append(f"Subsistence query failed: {exc}")
                if sub_payload:
                    subsistence_pct = _first_cell(sub_payload)

    stats = PovertyStats(
        region=geo_text,
        poverty_incidence_pct=poverty_pct,
        subsistence_incidence_pct=subsistence_pct,
        reference_year=year_int,
    )
    result = {
        **stats.model_dump(mode="json"),
        "source_table": table_url,
        "license": PSA_LICENSE,
        "caveats": partial,
        "data_retrieved_at": _now().isoformat(),
    }
    if partial:
        # A partial answer never enters the 24h cache. The poverty figure is
        # right; the subsistence null is an outage, not a published absence.
        result["upstream_error"] = True
        return result
    cache[key] = result
    return result


# ---------------------------------------------------------------------------
# v0.4.0 expansion: generic browse-discovery helpers + inflation/labor/health.
#
# Every new tool follows the SAME convention as population/poverty above:
# hardcode only the stable subject path prefix (e.g. "2M/PI/CPI/2018NEW"),
# discover the .px leaf by text predicate (landmine #6 — never hardcode .px
# IDs), POST with response format "json" (the proven shape that returns real
# cells: {"data": [{"key": [...], "values": ["str"]}]}), and read the data
# vintage from the table's own Year/time dimension — never from the response
# generation timestamp.
# ---------------------------------------------------------------------------

_MISSING = {"..", "...", "-", "", "n.a.", "na", "*"}


def _to_float(raw: object) -> float | None:
    """PSA encodes missing cells as the literal string '..' (and friends).

    float("nan") and float("inf") both succeed, so a bare float() would publish
    either as a statistic and JSON-encode it as an out-of-spec literal.
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower() in _MISSING:
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _year_max(meta: dict) -> int:
    """Largest 4-digit year across the table's time/Year dimension.

    1D health tables carry a 'Year' variable that is NOT time-typed, so match
    on the code/text as well as the time flag.
    """
    best = 0
    for var in meta.get("variables", []):
        code = (var.get("code") or "").lower()
        text = (var.get("text") or "").lower()
        if not (var.get("time") or code == "year" or text == "year"):
            continue
        for vt in var.get("valueTexts", []):
            digits = "".join(ch for ch in str(vt) if ch.isdigit())
            if len(digits) >= 4:
                year = int(digits[:4])
                if 1900 < year < 2100:
                    best = max(best, year)
    return best


# One lock per catalog path, bounded so a caller cannot grow it without limit.
# The paths that matter are a couple of dozen subject folders.
_MAX_PATH_LOCKS = 256
_PATH_LOCKS: dict[str, asyncio.Lock] = {}


def _browse_lock(subpath: str) -> asyncio.Lock:
    lock = _PATH_LOCKS.get(subpath)
    if lock is not None:
        return lock
    if len(_PATH_LOCKS) >= _MAX_PATH_LOCKS:
        # Only drop entries nobody is holding. Clearing the whole registry
        # would let a second caller build a fresh lock for a path someone is
        # already inside, which silently un-does single-flight.
        for path in [p for p, held in _PATH_LOCKS.items() if not held.locked()]:
            del _PATH_LOCKS[path]
        if len(_PATH_LOCKS) >= _MAX_PATH_LOCKS:
            # Every remaining lock is in use. Serve this call an unshared lock
            # rather than evict one; it costs one duplicate fetch, not a bug.
            return asyncio.Lock()
    return _PATH_LOCKS.setdefault(subpath, asyncio.Lock())


async def _browse(subpath: str) -> list[dict]:
    """List entries under a catalog path. Successes cache 24h; errors never do.

    Raises PSAUpstreamError on a transport failure, a non-list body, or an
    empty listing. An empty listing under a path that normally holds entries
    means the path moved, which is exactly the failure that pinned "no poverty
    tables" for 24h before v0.6.0.
    """
    key = cache_key({"browse": subpath})
    cache = CACHES["psa_browse"]
    if key in cache:
        return cache[key]
    url = f"{PSA_API_BASE}/DB/{subpath}/" if subpath else f"{PSA_API_BASE}/DB/"

    # Single-flight. Without it, concurrent cold browses of one path queue
    # duplicate GETs behind the rate limiter and the later ones time out while
    # the first result sits unused.
    async with _browse_lock(subpath):
        if key in cache:
            return cache[key]
        entries = await _get_json_or_raise(url)
        if isinstance(entries, dict):
            raise PSANotFoundError(
                f"{url} is a dataset, not a folder. Use describe_psa_dataset for a .px path."
            )
        if not isinstance(entries, list) or not entries:
            raise PSAUpstreamError(f"PSA browse of {url} returned no entries")
        cache[key] = entries
        return entries


async def _pick_latest_table(
    subpath: str,
    must_have: list[str],
    must_not: list[str] | None = None,
) -> tuple[str, dict] | None:
    """Discover the .px leaf under `subpath` whose text matches the predicate.

    PSA splits long series into era tables with near-identical titles
    (backcasted 1958-1994 vs current 2019-2026). Among all predicate matches we
    pick the table whose Year dimension reaches the most recent year, so callers
    always get the current series, never a backcast trap.
    """
    must_not = must_not or []
    discovery_key = f"latest::{subpath}::{must_have}::{must_not}"
    if discovery_key in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE[discovery_key]
    entries = await _browse(subpath)
    best: tuple[int, str, dict] | None = None
    for entry in entries:
        if entry.get("type") != "t":
            continue
        text = (entry.get("text") or "").lower()
        if not all(m in text for m in must_have):
            continue
        if any(n in text for n in must_not):
            continue
        table_url = f"{PSA_API_BASE}/DB/{subpath}/{entry['id']}"
        meta = await _get_json(table_url)
        if not isinstance(meta, dict):
            continue
        ymax = _year_max(meta)
        if best is None or ymax > best[0]:
            best = (ymax, table_url, meta)
    if best is None:
        return None
    found = (best[1], best[2])
    _DISCOVERY_CACHE[discovery_key] = found  # type: ignore[assignment]
    return found


def _key_columns(payload: dict) -> list[str]:
    """Column codes that line up positionally with each data row's `key`.

    The PXWeb "json" format appends the content column (type 'c') to `columns`
    but NOT to `key`; key positions are the dimension columns ('t'/'d') in order.
    """
    return [c.get("code", "") for c in payload.get("columns", []) if c.get("type") in ("t", "d")]


def _rows(payload: dict) -> list[tuple[dict[str, str], float | None]]:
    """Return [( {col_code: value_code}, numeric_or_None ), ...] for each cell."""
    cols = _key_columns(payload)
    out: list[tuple[dict[str, str], float | None]] = []
    for row in payload.get("data", []):
        key = row.get("key", [])
        values = row.get("values", [])
        mapping = {cols[i]: key[i] for i in range(min(len(cols), len(key)))}
        if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
            # PXWeb always sends a list. Indexing a string here would hand back
            # its first character as a statistic.
            out.append((mapping, None))
            continue
        out.append((mapping, _to_float(values[0] if values else None)))
    return out


def _value_text(meta: dict, code: str, value_code: str) -> str:
    for var in meta.get("variables", []):
        if var.get("code") == code:
            for v, t in zip(var.get("values", []), var.get("valueTexts", [])):
                if v == value_code:
                    return t
    return value_code


def _match_value(meta: dict, code: str, *needles: str) -> str | None:
    """First value-code in variable `code` whose text contains all needles."""
    for var in meta.get("variables", []):
        if var.get("code") != code:
            continue
        for v, t in zip(var.get("values", []), var.get("valueTexts", [])):
            low = t.lower()
            if all(n.lower() in low for n in needles):
                return v
    return None


def _var_by_code(meta: dict, *substrs: str) -> dict | None:
    for var in meta.get("variables", []):
        code = (var.get("code") or "").lower()
        if any(s in code for s in substrs):
            return var
    return None


_PERIOD_ORDER = [
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
]


@mcp.tool(
    title="Philippine consumer-price inflation",
    tags={"economy", "inflation", "openstat", "philippines", "psa"},
    annotations={
        "title": "Philippine consumer-price inflation",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_inflation_stats(area: str | None = None) -> dict:
    """Headline consumer-price inflation (year-on-year, all items) from PSA.

    Source: PSA OpenSTAT Consumer Price Index, 2018-based. The tool discovers
    the current CPI series by text (never a hardcoded table id) and returns the
    most recently published month's year-on-year change. Reports the exact
    reference period — PSA publishes with a lag, so this is the latest available
    figure, not necessarily the current month.

    Args:
        area: Region or "Philippines". None returns the national figure.
              e.g. "NCR", "Region VII", "Davao Region".
    """
    key = cache_key({"tool": "inflation", "area": area})
    cache = CACHES["psa_prices"]
    if key in cache:
        return cache[key]

    def _err(msg: str) -> dict:
        # Error results are never cached — a transient PXWeb failure must not
        # pin a null inflation figure for the 24h success TTL.
        return {
            "area": area or "Philippines",
            "headline_inflation_pct": None,
            "reference_period": None,
            "upstream_error": True,
            "caveats": [msg],
            "source": "PSA",
            "source_url": f"{PSA_API_BASE}/DB/2M/PI/CPI/",
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    try:
        discovered = await _pick_latest_table(
            "2M/PI/CPI/2018NEW",
            ["year-on-year changes", "by commodity group"],
            ["core"],
        )
    except PSAUpstreamError as exc:
        return _err(f"PSA CPI table discovery failed: {exc}")
    if discovered is None:
        return _err("PSA CPI table discovery failed")
    table_url, meta = discovered

    geo_hit = _find_geo_value(meta, area, "Geolocation")
    if geo_hit is None:
        return _err(f"Area '{area}' not found in PSA CPI geographic dimension")
    geo_val, geo_text = geo_hit

    all_items = _match_value(meta, "Commodity Description", "all item")
    if all_items is None:
        return _err("Could not locate the 'ALL ITEMS' commodity row in PSA CPI table")

    year_var = _var_by_code(meta, "year") or {}
    year_codes = year_var.get("values", [])
    period_var = _var_by_code(meta, "period") or {}
    period_codes = period_var.get("values", [])
    if not year_codes or not period_codes:
        return _err("PSA CPI table is missing Year/Period dimensions")
    base_year = None
    title = meta.get("title", "")
    if "2018=100" in title.replace(" ", ""):
        base_year = "2018"

    # Walk newest year backwards until we find a published month.
    for year_code in reversed(year_codes):
        query = {
            "query": [
                {"code": "Geolocation", "selection": {"filter": "item", "values": [geo_val]}},
                {
                    "code": "Commodity Description",
                    "selection": {"filter": "item", "values": [all_items]},
                },
                {"code": "Year", "selection": {"filter": "item", "values": [year_code]}},
                {"code": "Period", "selection": {"filter": "item", "values": period_codes}},
            ],
            "response": {"format": "json"},
        }
        payload = await _post_json(table_url, query)
        if not payload or not payload.get("data"):
            continue
        period_code_var = next(
            (c for c in _key_columns(payload) if c.lower() == "period"), "Period"
        )
        by_period: dict[str, float] = {}
        for mapping, val in _rows(payload):
            if val is None:
                continue
            pcode = mapping.get(period_code_var)
            if pcode is not None:
                by_period[_value_text(meta, "Period", pcode).strip().lower()] = val
        if not by_period:
            continue
        # Latest published month in calendar order; fall back to annual average.
        chosen_label = None
        chosen_val = None
        for month in reversed(_PERIOD_ORDER):
            if month in by_period:
                chosen_label = month.capitalize()
                chosen_val = by_period[month]
                break
        if chosen_val is None:
            for avg_key in ("ave", "average", "annual"):
                if avg_key in by_period:
                    chosen_label = "full-year average"
                    chosen_val = by_period[avg_key]
                    break
        if chosen_val is None:
            continue
        year_text = _value_text(meta, "Year", year_code)
        stats = InflationStats(
            area=geo_text,
            headline_inflation_pct=chosen_val,
            reference_period=f"{year_text} {chosen_label}",
            base_year=base_year,
            reference_note=(
                "Year-on-year change of the Consumer Price Index, All Items. "
                "PSA publishes monthly with a lag; this is the latest available "
                "reference period, not necessarily the current month."
            ),
        )
        result = {
            **stats.model_dump(mode="json"),
            "source_table": table_url,
            "source": "PSA",
            "source_url": table_url,
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }
        cache[key] = result
        return result

    return _err("PSA CPI query returned no published data")


@mcp.tool(
    title="Philippine labor-force indicators",
    tags={"economy", "labor", "openstat", "philippines", "psa"},
    annotations={
        "title": "Philippine labor-force indicators",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_labor_stats(region: str | None = None) -> dict:
    """Key labor-force indicators from the PSA Labor Force Survey.

    Returns labor-force participation, employment, unemployment, and
    underemployment rates for the latest published reference period. The PSA
    key-indicator series is national; a `region` argument is recorded as a
    caveat because this table has no regional breakdown.

    Args:
        region: Accepted for API symmetry. The LFS key-indicator table is
                national only; passing a region adds an explanatory caveat.
    """
    key = cache_key({"tool": "labor", "region": region})
    cache = CACHES["psa_labor"]
    if key in cache:
        return cache[key]
    caveats: list[str] = []
    if region:
        caveats.append(
            "PSA LFS key-indicator table is national; regional breakdown is not "
            "available in this series."
        )

    def _err(msg: str) -> dict:
        # Error results are never cached (see get_inflation_stats._err).
        return {
            "area": "Philippines",
            "employment_rate_pct": None,
            "unemployment_rate_pct": None,
            "underemployment_rate_pct": None,
            "labor_force_participation_rate_pct": None,
            "reference_period": None,
            "upstream_error": True,
            "caveats": [*caveats, msg],
            "source": "PSA",
            "source_url": f"{PSA_API_BASE}/DB/1B/LFS/",
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    try:
        discovered = await _pick_latest_table("1B/LFS", ["rates", "key employment indicators"], [])
    except PSAUpstreamError as exc:
        return _err(f"PSA Labor Force Survey table discovery failed: {exc}")
    if discovered is None:
        return _err("PSA Labor Force Survey table discovery failed")
    table_url, meta = discovered

    sex_both = _match_value(meta, "Sex", "both")
    rates_var = _var_by_code(meta, "rate") or {}
    year_var = _var_by_code(meta, "year") or {}
    month_var = _var_by_code(meta, "month") or {}
    if not (sex_both and rates_var and year_var and month_var):
        return _err("PSA LFS table is missing expected Sex/Rates/Year/Month dimensions")

    rate_codes = rates_var.get("values", [])
    month_codes = month_var.get("values", [])

    for year_code in reversed(year_var.get("values", [])):
        query = {
            "query": [
                {"code": "Year", "selection": {"filter": "item", "values": [year_code]}},
                {"code": "Month", "selection": {"filter": "item", "values": month_codes}},
                {"code": "Rates", "selection": {"filter": "item", "values": rate_codes}},
                {"code": "Sex", "selection": {"filter": "item", "values": [sex_both]}},
            ],
            "response": {"format": "json"},
        }
        payload = await _post_json(table_url, query)
        if not payload or not payload.get("data"):
            continue
        cols = _key_columns(payload)
        month_col = next((c for c in cols if c.lower() == "month"), "Month")
        rate_col = next((c for c in cols if c.lower() == "rates"), "Rates")
        # month_code -> {rate_code: value}
        grid: dict[str, dict[str, float]] = {}
        for mapping, val in _rows(payload):
            if val is None:
                continue
            mc = mapping.get(month_col)
            rc = mapping.get(rate_col)
            if mc is None or rc is None:
                continue
            grid.setdefault(mc, {})[rc] = val
        if not grid:
            continue

        def _month_rank(mc: str) -> int:
            label = _value_text(meta, "Month", mc).strip().lower()
            if label in ("annual", "average", "ave"):
                return 99
            for i, m in enumerate(
                [
                    "january",
                    "february",
                    "march",
                    "april",
                    "may",
                    "june",
                    "july",
                    "august",
                    "september",
                    "october",
                    "november",
                    "december",
                ]
            ):
                if label.startswith(m):
                    return i
            return -1

        best_month = max(grid.keys(), key=_month_rank)
        rates = grid[best_month]

        def _rate(*needles: str) -> float | None:
            code = _match_value(meta, "Rates", *needles)
            return rates.get(code) if code else None

        stats = LaborStats(
            area="Philippines",
            labor_force_participation_rate_pct=_rate("labor force participation"),
            employment_rate_pct=_rate("employment rate"),
            unemployment_rate_pct=_rate("unemployment rate"),
            underemployment_rate_pct=_rate("underemployment rate"),
            reference_period=(
                f"{_value_text(meta, 'Year', year_code)} {_value_text(meta, 'Month', best_month)}"
            ),
            reference_note=(
                "PSA Labor Force Survey key employment indicators, national. "
                "Latest available reference period."
            ),
        )
        result = {
            **stats.model_dump(mode="json"),
            "caveats": caveats,
            "source_table": table_url,
            "source": "PSA",
            "source_url": table_url,
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }
        cache[key] = result
        return result

    return _err("PSA LFS query returned no published data")


def _unit_from_title(title: str) -> str | None:
    """Pull a parenthetical unit out of a health table title.

    >>> _unit_from_title("Maternal mortality ratio (per 100,000 live births)")
    'per 100,000 live births'
    """
    start = title.find("(")
    end = title.find(")", start + 1)
    if start != -1 and end != -1:
        return title[start + 1 : end].strip()
    return None


async def _latest_health_value(
    table_url: str, meta: dict
) -> tuple[float | None, str | None, str | None]:
    """Latest numeric cell + its reference year for a (small) 1D health table.

    1D tables are tiny (≤ a few hundred cells). We select the single newest year
    and the first geolocation value (these tables are national), keeping the
    query well under the cell cap.
    """
    year_var = _var_by_code(meta, "year") or next(
        (v for v in meta.get("variables", []) if (v.get("text") or "").lower() == "year"),
        {},
    )
    year_codes = year_var.get("values", [])
    if not year_codes:
        return None, None, None
    query_dims: list[dict] = []
    for var in meta.get("variables", []):
        code = var.get("code", "")
        values = var.get("values", [])
        if not values:
            continue
        if var is year_var or code == year_var.get("code"):
            continue
        # Geolocation / background characteristics: take the first (aggregate) value.
        query_dims.append({"code": code, "selection": {"filter": "item", "values": [values[0]]}})
    for year_code in reversed(year_codes):
        query = {
            "query": [
                *query_dims,
                {
                    "code": year_var.get("code", "Year"),
                    "selection": {"filter": "item", "values": [year_code]},
                },
            ],
            "response": {"format": "json"},
        }
        try:
            payload = await _post_json_or_raise(table_url, query)
        except PSAUpstreamError as exc:
            # A failed POST is not "PSA publishes nothing for this year". Say so,
            # so the caller does not cache a null as a real indicator value.
            return None, None, f"{table_url}: {exc}"
        if not payload.get("data"):
            continue
        for _, val in _rows(payload):
            if val is not None:
                return val, _value_text(meta, year_var.get("code", "Year"), year_code), None
    return None, None, None


@mcp.tool(
    title="Philippine health indicators",
    tags={"health", "openstat", "philippines", "psa"},
    annotations={
        "title": "Philippine health indicators",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_health_indicators(indicator: str | None = None) -> dict:
    """National health indicators from PSA OpenSTAT (subject 1D).

    With no argument, returns the curated national headline set (maternal
    mortality ratio and total fertility rate). Pass a free-text `indicator` to
    fuzzy-match any table published under the Health subject — the available
    list is browse-discovered, never hardcoded.

    Args:
        indicator: Optional free-text indicator name, e.g. "maternal mortality",
                   "fertility". None returns the default headline set.
    """
    key = cache_key({"tool": "health", "indicator": indicator})
    cache = CACHES["psa_health"]
    if key in cache:
        return cache[key]

    def _health_err(msg: str) -> dict:
        # Not cached: discovery failure is usually a transient PXWeb error.
        return {
            "indicators": [],
            "upstream_error": True,
            "caveats": [msg],
            "source": "PSA",
            "source_url": f"{PSA_API_BASE}/DB/1D/",
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    try:
        entries = await _browse("1D")
    except PSAUpstreamError as exc:
        return _health_err(f"PSA Health (1D) table discovery failed: {exc}")
    tables = [e for e in entries if e.get("type") == "t"]
    available = [e.get("text", "") for e in tables]
    if not tables:
        return _health_err("PSA Health (1D) listing carries no tables")

    if indicator:
        want = indicator.lower().strip()
        chosen = [
            e
            for e in tables
            if want in (e.get("text") or "").lower()
            or any(tok in (e.get("text") or "").lower() for tok in want.split())
        ]
        caveat = None
        if not chosen:
            caveat = f"No PSA Health table matched '{indicator}'. Available indicators: {available}"
    else:
        chosen = [
            e
            for e in tables
            if "maternal mortality" in (e.get("text") or "").lower()
            or "fertility rate" in (e.get("text") or "").lower()
        ]
        caveat = None

    indicators: list[dict] = []
    unavailable: list[str] = []
    for entry in chosen:
        table_url = f"{PSA_API_BASE}/DB/1D/{entry['id']}"
        try:
            meta = await _get_json_or_raise(table_url)
        except PSAUpstreamError as exc:
            # Silently skipping made a fetch failure look like an indicator PSA
            # does not publish, and the short list then cached for 24h.
            unavailable.append(f"{entry.get('text') or entry['id']}: {exc}")
            continue
        if not isinstance(meta, dict):
            unavailable.append(f"{entry.get('text') or entry['id']}: metadata was not an object")
            continue
        value, year_text, fetch_error = await _latest_health_value(table_url, meta)
        if fetch_error:
            unavailable.append(fetch_error)
            continue
        title = meta.get("title", entry.get("text", ""))
        model = HealthIndicator(
            indicator=title,
            value=value,
            unit=_unit_from_title(title),
            area="Philippines",
            reference_period=year_text,
        )
        indicators.append({**model.model_dump(mode="json"), "source_table": table_url})

    caveats = [caveat] if caveat else []
    if unavailable:
        caveats.append(
            f"{len(unavailable)} of {len(chosen)} matched tables did not load: {unavailable}"
        )

    result = {
        "indicators": indicators,
        "available_indicators": available,
        "caveats": caveats,
        "source": "PSA",
        "source_url": f"{PSA_API_BASE}/DB/1D/",
        "license": PSA_LICENSE,
        "data_retrieved_at": _now().isoformat(),
    }
    if unavailable:
        # A partial answer is not a success; do not pin it for the 24h TTL.
        result["upstream_error"] = True
        return result
    cache[key] = result
    return result
