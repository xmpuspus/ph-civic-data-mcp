"""PSGC — Philippine Standard Geographic Code resolver.

The official PSA classifications site (psa.gov.ph/classifications-api/psgc) is
behind a basic-auth/anti-bot wall in practice. The community-mirrored PSGC API
at https://psgc.gitlab.io/api/ exposes the same PSA dataset as flat JSON
endpoints — that is what we use here. Source attribution still credits PSA.

Tools:
- resolve_ph_location(query)  — fuzzy-resolve a free-text place name
- list_admin_units(parent_code, level, limit)  — browse children of a node
- get_location_hierarchy(psgc_code)  — full chain region -> province -> city -> brgy
"""

from __future__ import annotations

from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from ph_civic_data_mcp.models.location import (
    PSGCHierarchy,
    PSGCHierarchyLevel,
    PSGCRecord,
)
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

PSGC_BASE = "https://psgc.gitlab.io/api"
PSGC_LICENSE = "Public domain (PSA Philippine Standard Geographic Code)"

LEVEL_ENDPOINTS: dict[str, str] = {
    "region": "regions",
    "province": "provinces",
    "city": "cities",
    "municipality": "municipalities",
    "city-municipality": "cities-municipalities",
    "district": "districts",
    "sub-municipality": "sub-municipalities",
    "barangay": "barangays",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _classify_level(record: dict[str, Any], hint: str | None = None) -> str:
    """Infer the administrative level of a PSGC record.

    Prefer the upstream API's `type` field when present (most reliable for
    NCR-style cities whose codes use the province-code slot). Fall back to
    structural inference from the 9-digit code.
    """
    if hint:
        return hint

    kind = (record.get("type") or "").lower()
    if kind:
        if "barangay" in kind:
            return "barangay"
        if "city" in kind and "municipality" in kind:
            return "city-municipality"
        if "city" in kind:
            return "city"
        if "municipality" in kind:
            return "municipality"
        if "district" in kind:
            return "district"
        if "province" in kind:
            return "province"
        if "region" in kind:
            return "region"
        if "sub-municipality" in kind:
            return "sub-municipality"

    code = record.get("code") or record.get("psgcCode") or ""
    code = code.zfill(9)
    pp = code[2:4]
    mm = code[4:6]
    bbb = code[6:9]
    if pp == "00":
        return "region"
    if mm == "00" and bbb == "000":
        return "province"
    if bbb == "000":
        return "city-municipality"
    return "barangay"


def _record_to_psgc(item: dict[str, Any], level_hint: str | None = None) -> PSGCRecord:
    code = item.get("code") or item.get("psgcCode") or ""
    name = item.get("name") or ""
    parent_code = (
        item.get("regionCode")
        or item.get("provinceCode")
        or item.get("cityCode")
        or item.get("municipalityCode")
        or item.get("districtCode")
    )
    region_code = item.get("regionCode")
    region_name = item.get("regionName")
    island_group = item.get("islandGroupCode")
    level = _classify_level(item, level_hint)
    return PSGCRecord(
        psgc_code=code,
        name=name,
        level=level
        if level
        in (
            "region",
            "province",
            "city",
            "municipality",
            "district",
            "barangay",
            "sub-municipality",
        )
        else "city",
        parent_code=parent_code,
        region_code=region_code,
        region_name=region_name,
        island_group=island_group,
        source_url=f"{PSGC_BASE}/{LEVEL_ENDPOINTS.get(level, 'cities-municipalities')}/{code}/",
        license=PSGC_LICENSE,
    )


async def _fetch_level(level: str) -> list[dict[str, Any]]:
    """Fetch and cache the full list at one administrative level."""
    endpoint = LEVEL_ENDPOINTS.get(level)
    if not endpoint:
        return []
    key = cache_key({"endpoint": endpoint})
    cache = CACHES["psgc_browse"]
    if key in cache:
        return cache[key]

    try:
        response = await fetch_with_retry(CLIENT, "GET", f"{PSGC_BASE}/{endpoint}/")
        response.raise_for_status()
        data = response.json()
    except Exception as exc:
        log_stderr(f"PSGC fetch error ({endpoint}): {exc}")
        cache[key] = []
        return []

    if not isinstance(data, list):
        cache[key] = []
        return []
    cache[key] = data
    return data


async def _fetch_one(code: str) -> dict[str, Any] | None:
    """Try each level endpoint to retrieve one PSGC record by code."""
    key = cache_key({"endpoint": "one", "code": code})
    cache = CACHES["psgc_browse"]
    if key in cache:
        return cache[key]

    for endpoint in (
        "regions",
        "provinces",
        "cities-municipalities",
        "districts",
        "sub-municipalities",
    ):
        try:
            response = await fetch_with_retry(CLIENT, "GET", f"{PSGC_BASE}/{endpoint}/{code}/")
            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, dict) and payload.get("code"):
                    cache[key] = payload
                    return payload
        except Exception as exc:
            log_stderr(f"PSGC code fetch error ({endpoint}/{code}): {exc}")
            continue
    cache[key] = None
    return None


async def _fetch_barangay_by_code(code: str) -> dict[str, Any] | None:
    """Barangay lookup endpoint exists separately."""
    try:
        response = await fetch_with_retry(CLIENT, "GET", f"{PSGC_BASE}/barangays/{code}/")
        if response.status_code == 200:
            payload = response.json()
            if isinstance(payload, dict) and payload.get("code"):
                return payload
    except Exception as exc:
        log_stderr(f"PSGC barangay fetch error ({code}): {exc}")
    return None


def _score(query: str, candidate: str) -> float:
    """Rough fuzzy score in [0,1]."""
    q = query.lower().strip()
    c = candidate.lower().strip()
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c:
        # Long candidates penalised slightly so 'manila' prefers 'Manila' over 'Manila Bay…'
        return 0.85 + min(0.1, len(q) / max(1, len(c)) * 0.1)
    if c in q:
        return 0.7
    return SequenceMatcher(None, q, c).ratio() * 0.8


def _candidate_queries(query: str) -> list[str]:
    """Generate fall-back queries from a free-text input.

    For "Sta. Mesa, Manila" the broader location is the last comma-segment
    ("Manila"), so we try (a) the original, (b) the last segment, (c) the
    first segment, (d) qualifier-stripped variants. Common Filipino
    abbreviations (Sta., Sto., Brgy.) are expanded.
    """
    if not query:
        return []
    raw = query.strip()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    expansions = {
        "sta.": "santa",
        "sto.": "santo",
        "brgy.": "barangay",
        "city of ": "",
        "municipality of ": "",
    }

    def _expand(text: str) -> str:
        out = text.lower()
        for abbr, full in expansions.items():
            out = out.replace(abbr, full)
        return " ".join(out.split())

    candidates_ordered: list[str] = [raw]
    if len(parts) > 1:
        candidates_ordered.append(parts[-1])
        candidates_ordered.append(parts[0])
    candidates_ordered.append(_expand(raw))
    if len(parts) > 1:
        candidates_ordered.append(_expand(parts[-1]))
        candidates_ordered.append(_expand(parts[0]))
    seen: set[str] = set()
    deduped: list[str] = []
    for q in candidates_ordered:
        q = q.strip()
        if not q or q in seen:
            continue
        seen.add(q)
        deduped.append(q)
    return deduped


async def _resolve_query(query: str) -> dict | None:
    """Search across cities-municipalities, provinces, regions for the best match."""
    if not query:
        return None

    levels_in_order = [
        ("city-municipality", "city-municipality"),
        ("province", "province"),
        ("region", "region"),
    ]

    best: tuple[float, dict[str, Any], str] | None = None
    queries = _candidate_queries(query)

    for q in queries:
        candidates: list[tuple[float, dict[str, Any], str]] = []
        for level_key, _ in levels_in_order:
            items = await _fetch_level(level_key)
            for item in items:
                name = item.get("name", "")
                score = _score(q, name)
                if score >= 0.6:
                    candidates.append((score, item, level_key))
        if candidates:
            candidates.sort(key=lambda t: (-t[0], len(t[1].get("name", ""))))
            top = candidates[0]
            if best is None or top[0] > best[0]:
                best = top
            if top[0] >= 0.95:
                break

    if best is None:
        # Try barangay last on the original query (slow — only on demand)
        items = await _fetch_level("barangay")
        for item in items:
            name = item.get("name", "")
            score = _score(query, name)
            if score >= 0.9:
                best = (score, item, "barangay")
                break

    if best is None:
        return None

    score, top, level = best
    record = _record_to_psgc(top, level_hint=level)
    return {
        **record.model_dump(mode="json"),
        "matched": True,
        "match_score": round(score, 3),
        "data_retrieved_at": _now().isoformat(),
    }


@mcp.tool()
async def resolve_ph_location(query: str) -> dict:
    """Fuzzy-resolve a Philippine place name to its canonical PSGC record.

    Args:
        query: Free-text place name. Examples:
               "Sta. Mesa, Manila", "Cebu City", "NCR", "Pampanga", "Tagaytay".

    Returns: psgc_code, name, level (region|province|city|municipality|barangay),
    parent_code, region_name, source_url, license, match_score, data_retrieved_at.
    Empty {"caveats": [...]} when no match.
    """
    key = cache_key({"tool": "resolve", "query": query.lower().strip()})
    cache = CACHES["psgc_resolve"]
    if key in cache:
        return cache[key]

    try:
        result = await _resolve_query(query)
    except Exception as exc:
        log_stderr(f"resolve_ph_location error: {exc}")
        result = None

    if result is None:
        result = {
            "query": query,
            "matched": False,
            "caveats": [
                f"No PSGC record matched '{query}'. Try a more specific name (e.g. 'Manila City' instead of 'Manila')."
            ],
            "source": "PSGC",
            "source_url": PSGC_BASE,
            "license": PSGC_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }
    cache[key] = result
    return result


@mcp.tool()
async def list_admin_units(
    parent_code: str | None = None,
    level: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Browse children of a PSGC node, or top-level regions when parent_code is None.

    Args:
        parent_code: Parent PSGC code. None returns the regions list.
        level: Filter children by level
               (region|province|city|municipality|district|barangay).
        limit: Max units to return (default 50, capped at 500).

    Returns: list of PSGC records with psgc_code, name, level, parent_code,
    region_name, source_url, license, source.
    """
    limit = max(1, min(int(limit), 500))

    if parent_code is None:
        target_level = level or "region"
        items = await _fetch_level(target_level)
        out = [
            _record_to_psgc(it, level_hint=target_level).model_dump(mode="json")
            for it in items[:limit]
        ]
        return out

    parent_code = parent_code.strip()
    target_level = level
    if target_level is None:
        # Auto-pick: regions -> provinces, provinces -> cities/munis, cities -> barangays
        parent_padded = parent_code.zfill(9)
        if parent_padded[2:] == "0000000":
            target_level = "province"
        elif parent_padded[4:] == "00000":
            target_level = "city-municipality"
        else:
            target_level = "barangay"

    items = await _fetch_level(target_level)
    parent_norm = parent_code.lstrip("0")
    filtered: list[dict] = []
    for item in items:
        # Children fields vary by level
        child_parent_keys = (
            "regionCode",
            "provinceCode",
            "cityCode",
            "municipalityCode",
            "cityMunicipalityCode",
            "districtCode",
        )
        match = False
        for k in child_parent_keys:
            v = item.get(k)
            if v and (v == parent_code or v.lstrip("0") == parent_norm):
                match = True
                break
        if match:
            filtered.append(_record_to_psgc(item, level_hint=target_level).model_dump(mode="json"))
            if len(filtered) >= limit:
                break
    return filtered


async def _walk_hierarchy(record: dict[str, Any], level_hint: str) -> list[PSGCHierarchyLevel]:
    """Walk a record up to its region. Returns ordered chain region -> ... -> record."""
    chain: list[PSGCHierarchyLevel] = []
    seen: set[str] = set()

    def _to_level(item: dict[str, Any], lvl: str) -> PSGCHierarchyLevel:
        code = item.get("code") or item.get("psgcCode") or ""
        return PSGCHierarchyLevel(
            psgc_code=code,
            name=item.get("name", ""),
            level=lvl
            if lvl
            in (
                "region",
                "province",
                "city",
                "municipality",
                "district",
                "barangay",
                "sub-municipality",
            )
            else "city",
            source_url=f"{PSGC_BASE}/{LEVEL_ENDPOINTS.get(lvl, 'cities-municipalities')}/{code}/",
        )

    chain.append(_to_level(record, level_hint))
    seen.add(record.get("code", ""))

    current = record
    current_level = level_hint
    while current_level != "region":
        if current_level == "barangay":
            parent_code = (
                current.get("cityMunicipalityCode")
                or current.get("districtCode")
                or current.get("subMunicipalityCode")
            )
            parent_level = "city-municipality"
        elif current_level in (
            "city",
            "municipality",
            "city-municipality",
            "district",
            "sub-municipality",
        ):
            parent_code = (
                current.get("provinceCode")
                or current.get("districtCode")
                or current.get("regionCode")
            )
            parent_level = "province" if current.get("provinceCode") else "region"
        elif current_level == "province":
            parent_code = current.get("regionCode")
            parent_level = "region"
        else:
            break

        if not parent_code or parent_code in seen:
            break
        seen.add(parent_code)

        # NCR cities have provinceCode == "" in some snapshots — skip and go to region
        if current_level in ("city", "municipality", "city-municipality") and not current.get(
            "provinceCode"
        ):
            region_code = current.get("regionCode")
            if region_code:
                regions = await _fetch_level("region")
                for r in regions:
                    if r.get("code") == region_code:
                        chain.append(_to_level(r, "region"))
                        return list(reversed(chain))
            break

        if parent_level == "province":
            provinces = await _fetch_level("province")
            for p in provinces:
                if p.get("code") == parent_code:
                    chain.append(_to_level(p, "province"))
                    current = p
                    current_level = "province"
                    break
            else:
                break
        elif parent_level == "region":
            regions = await _fetch_level("region")
            for r in regions:
                if r.get("code") == parent_code:
                    chain.append(_to_level(r, "region"))
                    current = r
                    current_level = "region"
                    break
            else:
                break
        else:
            break

    return list(reversed(chain))


@mcp.tool()
async def get_location_hierarchy(psgc_code: str) -> dict:
    """Return the full chain region -> province -> city/municipality -> barangay
    for one PSGC code.

    Args:
        psgc_code: 9-digit PSGC code (leading zeros optional).

    Returns: psgc_code, chain (list of {psgc_code, name, level, source_url}),
    source, license, data_retrieved_at.
    """
    if not psgc_code:
        return {
            "psgc_code": "",
            "chain": [],
            "caveats": ["psgc_code is empty"],
            "source": "PSGC",
            "source_url": PSGC_BASE,
            "license": PSGC_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    code = psgc_code.strip()

    record: dict[str, Any] | None = None
    level_hint = "region"

    # Try cheaper endpoints first.
    record_padded = code.zfill(9)
    if record_padded[6:] != "000":
        record = await _fetch_barangay_by_code(code)
        level_hint = "barangay"

    if record is None:
        record = await _fetch_one(code)
        if record is not None:
            level_hint = _classify_level(record)

    if record is None:
        return {
            "psgc_code": code,
            "chain": [],
            "caveats": [f"No PSGC record found for code '{code}'."],
            "source": "PSGC",
            "source_url": PSGC_BASE,
            "license": PSGC_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    chain = await _walk_hierarchy(record, level_hint)
    hierarchy = PSGCHierarchy(
        psgc_code=code,
        chain=chain,
    )
    return {
        **hierarchy.model_dump(mode="json"),
        "data_retrieved_at": _now().isoformat(),
    }


# Internal helper exposed for utils/geo.py refactor — not a tool.
async def find_coords_for_query(query: str) -> tuple[float, float] | None:
    """Best-effort lat/lng for a query via PSGC + a fallback table.

    PSGC API does not currently expose coordinates, so this returns None for
    most queries. Callers must fall back to utils/geo.CITY_COORDS.
    """
    return None
