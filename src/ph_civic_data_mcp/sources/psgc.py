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

import asyncio
import re

from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

from ph_civic_data_mcp.models.location import (
    PSGCHierarchy,
    PSGCHierarchyLevel,
    PSGCRecord,
)
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_INVALID_REQUEST,
    failure_envelope,
    failure_result,
)
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

PSGC_BASE = "https://psgc.gitlab.io/api"
PSGC_LICENSE = "Public domain (PSA Philippine Standard Geographic Code)"

# A PSGC code is pure digits: the 9-digit code (leading zeros optional) or the
# 10-digit edition. httpx collapses ".." path segments, so a code carrying a
# letter, a dot, or a slash could walk a request outside `/api/<level>/<code>/`.
# world_bank.py hit the same bug class with a non-numeric indicator code.
_CODE_RE = re.compile(r"^\d{1,10}$")


def _valid_psgc_code(code: str) -> bool:
    return bool(_CODE_RE.match(code))


# Common nicknames and abbreviations that the fuzzy scorer cannot bridge on
# its own ("QC" scores nowhere near "Quezon City"). Keys are compared
# lowercase. Conservative, well-known entries only — no guesses.
LOCATION_ALIASES: dict[str, str] = {
    "qc": "Quezon City",
    "gensan": "General Santos",
    "cdo": "Cagayan de Oro",
    "zambo": "Zamboanga City",
    "bgc": "Taguig",
    "metro manila": "NCR",
    "mm": "NCR",
    "car": "Cordillera Administrative Region",
    "caraga": "Region XIII",
    "soccsksargen": "Region XII",
    "calabarzon": "Region IV-A",
    "mimaropa": "Mimaropa Region",
    "bicol": "Region V",
    "ilocos region": "Region I",
    "davao city": "City of Davao",
}

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


async def _region_name_for_code(region_code: str | None) -> str | None:
    """Look up a region's name from its code.

    v0.7.0 finding: the PSGC mirror never sends a `regionName` field on a
    child record, only `regionCode`. `item.get("regionName")` always read
    None, so every `PSGCRecord.region_name` was None. `_fetch_level("region")`
    is already 24h-cached, so this only pays for a real fetch once per
    process.
    """
    if not region_code:
        return None
    regions = await _fetch_level("region")
    for r in regions:
        if r.get("code") == region_code:
            return r.get("name")
    return None


async def _record_to_psgc(item: dict[str, Any], level_hint: str | None = None) -> PSGCRecord:
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
    level = _classify_level(item, level_hint)
    # The cities-municipalities endpoint hands every record the hint
    # "city-municipality", which the PSGCRecord level set does not carry.
    # The old fallback mapped every unknown level to "city", so a plain
    # municipality such as Adams, Ilocos Norte read as a city. Split it on
    # the mirror's own isCity flag instead.
    if level == "city-municipality":
        level = "city" if _is_city_record(item) else "municipality"
    # A region record has no parent region to name; skip the extra fetch.
    region_name = None if level == "region" else await _region_name_for_code(region_code)
    island_group = item.get("islandGroupCode")
    return PSGCRecord(
        psgc_code=code,
        name=name,
        psgc_10digit_code=item.get("psgc10DigitCode") or None,
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


# One lock per admin level, bounded even though LEVEL_ENDPOINTS only ever
# defines 8 keys. N concurrent cold resolves for the same level used to queue
# N identical GETs behind the rate limiter, and the later ones could blow
# their own timeout while the first result sat unused. Same pattern as
# `psa._browse_lock`.
_MAX_LEVEL_LOCKS = 32
_LEVEL_LOCKS: dict[str, asyncio.Lock] = {}


def _level_lock(level: str) -> asyncio.Lock:
    lock = _LEVEL_LOCKS.get(level)
    if lock is not None:
        return lock
    if len(_LEVEL_LOCKS) >= _MAX_LEVEL_LOCKS:
        for key in [k for k, held in _LEVEL_LOCKS.items() if not held.locked()]:
            del _LEVEL_LOCKS[key]
        if len(_LEVEL_LOCKS) >= _MAX_LEVEL_LOCKS:
            # Every remaining lock is in use. Serve this call an unshared
            # lock rather than evict one; it costs one duplicate fetch, not
            # a bug.
            return asyncio.Lock()
    return _LEVEL_LOCKS.setdefault(level, asyncio.Lock())


async def _fetch_level(level: str) -> list[dict[str, Any]]:
    """Fetch and cache the full list at one administrative level."""
    endpoint = LEVEL_ENDPOINTS.get(level)
    if not endpoint:
        return []
    key = cache_key({"endpoint": endpoint})
    cache = CACHES["psgc_browse"]
    if key in cache:
        return cache[key]

    # Single-flight: without the lock, concurrent cold calls for one level
    # each miss the cache and each fetch.
    async with _level_lock(level):
        if key in cache:
            return cache[key]
        # Failures raise — a transient PSGC outage must not be cached as an
        # empty level list for 24h (which made every resolve report "no match").
        response = await fetch_with_retry(CLIENT, "GET", f"{PSGC_BASE}/{endpoint}/")
        response.raise_for_status()
        data = response.json()

        if not isinstance(data, list):
            raise RuntimeError(f"PSGC {endpoint} endpoint returned non-list payload")
        cache[key] = data
        return data


class PSGCFetchError(RuntimeError):
    """A PSGC code lookup could not complete because of a transport failure.

    Raised instead of a plain None so a caller can tell "the mirror is down"
    from "no record exists at this code". Codex cross-model finding on the
    v0.6.1 diff: `_fetch_barangay_by_code` used to swallow every exception
    into None, so `get_location_hierarchy` reported a genuine outage as an
    unknown code with no `upstream_error`.
    """


# One lock per code, bounded the same way as `_level_lock`. A caller-supplied
# code has far more possible values than an admin level, so the cap is wider.
_MAX_ONE_LOCKS = 256
_ONE_LOCKS: dict[str, asyncio.Lock] = {}


def _one_lock(code: str) -> asyncio.Lock:
    lock = _ONE_LOCKS.get(code)
    if lock is not None:
        return lock
    if len(_ONE_LOCKS) >= _MAX_ONE_LOCKS:
        for key in [k for k, held in _ONE_LOCKS.items() if not held.locked()]:
            del _ONE_LOCKS[key]
        if len(_ONE_LOCKS) >= _MAX_ONE_LOCKS:
            return asyncio.Lock()
    return _ONE_LOCKS.setdefault(code, asyncio.Lock())


async def _fetch_one(code: str) -> dict[str, Any] | None:
    """Try each level endpoint to retrieve one PSGC record by code.

    Raises PSGCFetchError only when every endpoint failed on a transport
    error and none answered cleanly (a 200 or a 404). A clean "not found at
    any level" still returns None. A malformed code returns None straight
    away; no endpoint is ever called with it.
    """
    if not _valid_psgc_code(code):
        return None
    key = cache_key({"endpoint": "one", "code": code})
    cache = CACHES["psgc_browse"]
    if key in cache:
        return cache[key]

    # Single-flight: without the lock, concurrent cold calls for one code
    # each miss the cache and each fetch every endpoint.
    async with _one_lock(code):
        if key in cache:
            return cache[key]
        had_errors = False
        last_exc: Exception | None = None
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
                had_errors = True
                last_exc = exc
                continue
        # Only cache a genuine miss; a None born from upstream errors must
        # not pin "code does not exist" for the 24h TTL.
        if had_errors:
            raise PSGCFetchError(f"PSGC mirror unreachable for code '{code}': {last_exc}")
        cache[key] = None
        return None


async def _fetch_barangay_by_code(code: str) -> dict[str, Any] | None:
    """Barangay lookup endpoint exists separately.

    Raises PSGCFetchError on a transport failure. Returns None only for a
    clean non-200 response, which means the code is not a barangay. A
    malformed code returns None straight away, with no request sent.
    """
    if not _valid_psgc_code(code):
        return None
    try:
        response = await fetch_with_retry(CLIENT, "GET", f"{PSGC_BASE}/barangays/{code}/")
    except Exception as exc:
        raise PSGCFetchError(f"PSGC mirror unreachable for barangay '{code}': {exc}") from exc
    if response.status_code == 200:
        payload = response.json()
        if isinstance(payload, dict) and payload.get("code"):
            return payload
    return None


def _lookup_shape(item: dict[str, Any], level: str) -> dict[str, Any]:
    if level == "city-municipality":
        level = "city" if item.get("isCity") else "municipality"
    return {
        "psgc_code": str(item.get("code") or ""),
        "psgc_10digit_code": item.get("psgc10DigitCode") or None,
        "name": item.get("name") or "",
        "level": level,
        "region_code": item.get("regionCode") or None,
        "province_code": item.get("provinceCode") or None,
    }


async def lookup_psgc_code(code: str) -> dict[str, Any] | None:
    """One PSGC record by 9- or 10-digit code, or None when no level carries it.

    Unlike `_fetch_one`, a transport failure raises, so a caller can tell an
    unknown code from an unreachable mirror. The mirror keys its per-record
    endpoints on the 9-digit code, so a 10-digit code is found by scanning the
    cached level lists (the barangay list is about 11 MB, fetched once a day).
    Not a tool, but `psa.py` passes a caller-supplied code straight through,
    so a malformed code still returns None with no request sent.
    """
    code = code.strip()
    if not _valid_psgc_code(code):
        return None
    if len(code) == 10:
        for level in ("region", "province", "city-municipality", "barangay"):
            for item in await _fetch_level(level):
                if str(item.get("psgc10DigitCode") or "") == code:
                    return _lookup_shape(item, level)
        return None
    for endpoint, level in (
        ("regions", "region"),
        ("provinces", "province"),
        ("cities-municipalities", "city-municipality"),
        ("barangays", "barangay"),
    ):
        response = await fetch_with_retry(CLIENT, "GET", f"{PSGC_BASE}/{endpoint}/{code}/")
        if response.status_code not in (200, 404):
            # Only a 404 means "not at this level, try the next one". A 429,
            # 401 or 403 is an outage, not a verdict on the code, and must
            # raise so the caller reports upstream_error rather than "unknown
            # code" for a mirror that is rate-limiting or blocking us.
            response.raise_for_status()
        if response.status_code != 200:
            continue
        payload = response.json()
        if isinstance(payload, dict) and payload.get("code"):
            return _lookup_shape(payload, level)
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
    alias = LOCATION_ALIASES.get(raw.lower())
    if alias:
        raw = alias
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

    def _city_variant(text: str) -> str | None:
        # PSGC names cities "City of X" while people write "X City" — the
        # fuzzy scorer can't bridge that ("Manila City" scored Danao City
        # highest before this). Generate the canonical form as a candidate.
        lc = text.lower().strip()
        if lc.endswith(" city") and len(lc) > len(" city"):
            return f"city of {text[: -len(' city')].strip()}"
        return None

    candidates_ordered: list[str] = [raw]
    if len(parts) > 1:
        candidates_ordered.append(parts[-1])
        candidates_ordered.append(parts[0])
    candidates_ordered.append(_expand(raw))
    if len(parts) > 1:
        candidates_ordered.append(_expand(parts[-1]))
        candidates_ordered.append(_expand(parts[0]))
    for base in list(candidates_ordered):
        variant = _city_variant(base)
        if variant:
            candidates_ordered.append(variant)
    seen: set[str] = set()
    deduped: list[str] = []
    for q in candidates_ordered:
        q = q.strip()
        if not q or q in seen:
            continue
        seen.add(q)
        deduped.append(q)
    return deduped


# A bare place name like "Bacolod" or "Cebu" matches its small same-named
# municipality, or the province it sits in, exactly (score 1.0). The
# well-known city ("City of Bacolod", "City of Cebu") only scores a fuzzy
# substring match, 0.85 to 0.95 by construction of `_score`. Live-checked
# 2026-09-03: the real gap for Bacolod/Cebu/Davao/Iloilo runs 0.004 to
# 0.117. Within this margin, prefer the candidate `_classify_level` calls a
# city over one it calls a municipality or a province.
_PROMINENCE_EPSILON = 0.15


def _is_city_record(item: dict[str, Any]) -> bool:
    """True when a cities-municipalities record is an incorporated city.

    The live PSGC mirror sends `isCity`/`isMunicipality` booleans on every
    record and no `type` string at all, so `_classify_level` (which reads
    `type` first, then falls back to a 9-digit code shape that cannot tell a
    city from a municipality) always answers "city-municipality" here, never
    the bare "city" a caller might expect. Read the boolean directly; fall
    back to `_classify_level` only for a record that does carry a `type`
    string, such as the fixtures in test_psgc.py.
    """
    is_city = item.get("isCity")
    if isinstance(is_city, bool):
        return is_city
    return _classify_level(item) == "city"


def _prefer_prominent(
    pool: list[tuple[float, dict[str, Any], str]],
) -> tuple[float, dict[str, Any], str]:
    """Among candidates near the top score, prefer an actual city.

    `pool` is sorted by score descending. Only a candidate within
    `_PROMINENCE_EPSILON` of the outright top score is eligible, so a
    genuinely distinct, lower-scoring match is never promoted. Several
    candidates tied at the very top score (real same-named places, like the
    four "San Juan" municipalities and cities) still keep their ranking
    among themselves; the search below just moves a top-of-pack city ahead
    of a top-of-pack municipality or province.
    """
    top_score = pool[0][0]
    for score, item, level_key in pool:
        if top_score - score > _PROMINENCE_EPSILON:
            break
        if _is_city_record(item):
            return score, item, level_key
    return pool[0]


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
    best_pool: list[tuple[float, dict[str, Any], str]] = []
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
                best_pool = candidates
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

    if best_pool:
        best = _prefer_prominent(best_pool)

    score, top, level = best
    record = await _record_to_psgc(top, level_hint=level)

    # Surface runner-up candidates so ambiguous names ("San Juan" exists in
    # several provinces) don't resolve silently to one of many equals.
    top_code = top.get("code") or top.get("psgcCode")
    alternatives: list[dict] = []
    seen_codes: set[str] = {top_code} if top_code else set()
    for alt_score, alt_item, alt_level in best_pool:
        if alt_score < 0.8 or len(alternatives) >= 3:
            break
        alt_code = alt_item.get("code") or alt_item.get("psgcCode")
        if not alt_code or alt_code in seen_codes:
            continue
        seen_codes.add(alt_code)
        alternatives.append(
            {
                "psgc_code": alt_code,
                "name": alt_item.get("name", ""),
                "region_name": alt_item.get("regionName"),
                "level": alt_level,
                "match_score": round(alt_score, 3),
            }
        )

    return {
        **record.model_dump(mode="json"),
        "matched": True,
        "match_score": round(score, 3),
        "alternatives": alternatives,
        "data_retrieved_at": _now().isoformat(),
    }


@mcp.tool(
    title="Resolve a Philippine place name to PSGC",
    tags={"geocoding", "location", "philippines", "psgc"},
    annotations={
        "title": "Resolve a Philippine place name to PSGC",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def resolve_ph_location(query: str) -> dict:
    """Fuzzy-resolve a Philippine place name to its canonical PSGC record.

    Args:
        query: Free-text place name. Examples:
               "Sta. Mesa, Manila", "Cebu City", "NCR", "Pampanga", "Tagaytay".

    Returns: psgc_code, name, level (region|province|city|municipality|barangay),
    parent_code, region_name, source_url, license, match_score, alternatives
    (runner-up candidates for ambiguous names), data_retrieved_at.
    {"matched": false, "caveats": [...]} when no match; the same shape plus
    "upstream_error": true when the PSGC API itself was unreachable.

    Common nicknames resolve directly: "QC", "Gensan", "CDO", "Metro Manila".
    """
    key = cache_key({"tool": "resolve", "query": query.lower().strip()})
    cache = CACHES["psgc_resolve"]
    if key in cache:
        return cache[key]

    try:
        result = await _resolve_query(query)
    except Exception as exc:
        # Upstream outage — report it as such and do NOT cache, so a blip
        # doesn't pin "no match" for the 24h TTL.
        log_stderr(f"resolve_ph_location error: {exc}")
        return {
            "query": query,
            "matched": False,
            "upstream_error": True,
            "caveats": [
                f"PSGC API unavailable ({type(exc).__name__}: {exc}). "
                f"Could not attempt resolution of '{query}' — retry later."
            ],
            "source": "PSGC",
            "source_url": PSGC_BASE,
            "license": PSGC_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

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


@mcp.tool(
    title="List Philippine administrative units",
    tags={"location", "philippines", "psgc"},
    annotations={
        "title": "List Philippine administrative units",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def list_admin_units(
    parent_code: str | None = None,
    level: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict] | dict:
    """Browse children of a PSGC node, or top-level regions when parent_code is None.

    Args:
        parent_code: Parent PSGC code. None returns the regions list.
        level: Filter children by level
               (region|province|city|municipality|district|barangay).
        limit: Max units to return (default 50, capped at 500).
        offset: Skip this many matching units before returning results —
                page past 500 children (e.g. Manila has 897 barangays) by
                calling again with offset=500.

    Returns: list of PSGC records with psgc_code, name, level, parent_code,
    region_name, source_url, license, source. On PSGC API failure returns
    {results: [], upstream_error: true, caveats}.
    """
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))

    try:
        if parent_code is None:
            target_level = level or "region"
            items = await _fetch_level(target_level)
            return [
                (await _record_to_psgc(it, level_hint=target_level)).model_dump(mode="json")
                for it in items[offset : offset + limit]
            ]
        return await _list_children(parent_code, level, limit, offset)
    except Exception as exc:
        log_stderr(f"list_admin_units error: {exc}")
        return failure_envelope(
            "PSGC",
            PSGC_BASE,
            f"PSGC API unavailable ({type(exc).__name__}: {exc}).",
            license=PSGC_LICENSE,
        )


async def _list_children(
    parent_code: str, level: str | None, limit: int, offset: int
) -> list[dict]:
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
    matched_so_far = 0
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
            matched_so_far += 1
            if matched_so_far <= offset:
                continue
            filtered.append(
                (await _record_to_psgc(item, level_hint=target_level)).model_dump(mode="json")
            )
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


@mcp.tool(
    title="Full PSGC hierarchy for a place",
    tags={"location", "philippines", "psgc"},
    annotations={
        "title": "Full PSGC hierarchy for a place",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_location_hierarchy(psgc_code: str) -> dict:
    """Return the full chain region -> province -> city/municipality -> barangay
    for one PSGC code.

    Args:
        psgc_code: 9-digit PSGC code (leading zeros optional).

    Returns: psgc_code, chain (list of {psgc_code, name, level, source_url}),
    source, license, data_retrieved_at.
    """
    code = (psgc_code or "").strip()
    if not _valid_psgc_code(code):
        # A caller mistake, not an outage: never reaches a URL. httpx
        # collapses ".." path segments, so a shape check must run before the
        # code is ever interpolated into a request.
        return failure_result(
            "PSGC",
            PSGC_BASE,
            f"psgc_code must be 1 to 10 digits (leading zeros optional). Got {psgc_code!r}.",
            license=PSGC_LICENSE,
            data_status=DATA_STATUS_INVALID_REQUEST,
            psgc_code=psgc_code or "",
            chain=[],
        )

    record: dict[str, Any] | None = None
    level_hint = "region"

    try:
        # Try cheaper endpoints first.
        record_padded = code.zfill(9)
        if record_padded[6:] != "000":
            record = await _fetch_barangay_by_code(code)
            level_hint = "barangay"

        if record is None:
            record = await _fetch_one(code)
            if record is not None:
                level_hint = _classify_level(record)
    except PSGCFetchError as exc:
        log_stderr(f"get_location_hierarchy lookup error: {exc}")
        return {
            "psgc_code": code,
            "chain": [],
            "upstream_error": True,
            "caveats": [f"PSGC API unavailable while looking up code '{code}' ({exc})."],
            "source": "PSGC",
            "source_url": PSGC_BASE,
            "license": PSGC_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

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

    try:
        chain = await _walk_hierarchy(record, level_hint)
    except Exception as exc:
        log_stderr(f"get_location_hierarchy walk error: {exc}")
        return {
            "psgc_code": code,
            "chain": [],
            "upstream_error": True,
            "caveats": [f"PSGC API unavailable while walking hierarchy ({type(exc).__name__})."],
            "source": "PSGC",
            "source_url": PSGC_BASE,
            "license": PSGC_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }
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
