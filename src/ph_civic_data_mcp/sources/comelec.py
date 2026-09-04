"""COMELEC 2025 election results archive: precinct-level vote tallies.

The archive froze on 2025-05-16 10:00:09 AM (`/data/common/latestTime.json`).
It is a fixed public record, not a live feed.

Landmines (from the ledger probe log and a live check on 2026-09-04):
- Every listing, at every level (region, province, city, barangay, and the
  precinct family), wraps its array under one `regions` key. The body is
  never a bare list.
- The documented `/data/regions/local/<code>.json` tree stops at the
  barangay level and returns 403 below it. The site's own JS bundle names a
  second family, `/data/regions/precinct/<code[:2]>/<code>.json`, that lists
  the precincts inside one barangay.
- A precinct's vote tally sits at `/data/er/<precinct_code[:3]>/<precinct_code>.json`,
  a third, undocumented family, also found in the JS bundle.
- A contest's candidate list sits one level deeper than it looks:
  `contest["candidates"]["candidates"]`, not `contest["candidates"]`.
- The upstream field is `statistic` (singular). This module still reports it
  under the key `statistics`, matching the tool's documented shape.
"""

from __future__ import annotations

import asyncio
import re

from datetime import datetime, timezone

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_EMPTY,
    DATA_STATUS_INDETERMINATE,
    DATA_STATUS_INVALID_REQUEST,
    DATA_STATUS_SUCCESS,
    failure_result,
)
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

COMELEC_BASE = "https://2025electionresults.comelec.gov.ph"
COMELEC_SOURCE = "COMELEC 2025 election results archive, government record"
COMELEC_LICENSE = "Public, COMELEC 2025 election results archive"
COMELEC_NOTE = (
    "Official COMELEC 2025 election returns as published on the results "
    "archive. Retrieval only, no interpretation."
)

# "0" is the root. A region code is "R" plus 6 more characters. Most are
# digits ("R001000"), but live-checked 2026-09-04: 6 of the 20 root
# entries are not ("R04A000" CALABARZON, "R04B000" MIMAROPA, "R00LAV0",
# "R00NIR0", "R0BARMM", "R0CAR00", "R0NCR00", which is NCR itself). A digits-only
# regex rejects NCR and CALABARZON as invalid_request, so it must accept
# any alphanumeric tail. Every other node (province, city or municipality,
# barangay) is a 7-digit numeric code PPCCBBB: province (2 digits), city
# or municipality (2), barangay (3).
_REGION_RE = re.compile(r"^R[A-Z0-9]{6}$")
_NODE_RE = re.compile(r"^\d{7}$")
_PRECINCT_RE = re.compile(r"^\d{8}$")

MAX_CHILDREN = 500

# "16 May 2025" + "10:00:09 AM" from latestTime.json. The source names no
# timezone, so the parsed value stays naive rather than guessing one.
_FROZEN_FMT = "%d %B %Y %I:%M:%S %p"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _valid_browse_code(code: str) -> bool:
    return code == "0" or bool(_REGION_RE.match(code)) or bool(_NODE_RE.match(code))


def _level_for_code(code: str) -> str:
    """Admin level a browse code sits at, from its own shape.

    A barangay-shaped code (bbb != "000") always 403s the local tree per the
    ledger probe, so this level is only ever reached through the precinct
    fallback in `_fetch_tree`.
    """
    if code == "0":
        return "root"
    if _REGION_RE.match(code):
        return "region"
    city, barangay = code[2:4], code[4:7]
    if city == "00" and barangay == "000":
        return "province"
    if barangay == "000":
        return "city_municipality"
    return "barangay"


_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _snake_case(key: str) -> str:
    return _CAMEL_RE.sub("_", key).lower()


def _unwrap_regions(payload: object) -> list[dict]:
    """Every tree level, including the precinct family, wraps its array
    under a `regions` key. Live-checked 2026-09-04 at the root, a region, a
    province, a city, and a precinct list: all five use this one wrapper.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("regions"), list):
        raise ValueError("COMELEC tree endpoint returned an unexpected body shape")
    return payload["regions"]


def _parse_children(items: list[dict]) -> tuple[list[dict], int]:
    """Parsed rows, and a count of rows that carried no usable code.

    A caller that gets a nonempty `items` back but zero parsed children
    sees drift, not a genuine empty listing, and must not cache the
    result. Some good rows and some bad rows is not drift: the caller
    reports the skipped count in `caveats` and keeps the good rows.
    """
    children: list[dict] = []
    skipped = 0
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        code = str(item.get("code") or "")
        if not code:
            skipped += 1
            continue
        children.append(
            {
                "code": code,
                "name": item.get("name"),
                "category_code": item.get("categoryCode"),
                "master_code": item.get("masterCode"),
            }
        )
    return children, skipped


async def _fetch_tree(code: str) -> list[dict] | None:
    """Children of `code` in the local geography tree, or its precincts.

    Returns None when the archive answers 403 with its AccessDenied body at
    every path this code can take, meaning the code is unknown. Raises on a
    transport failure, a status other than 200 or 403, or a 403 whose body
    carries no AccessDenied marker, since that last case is an outage, not
    an answer.
    """
    url = f"{COMELEC_BASE}/data/regions/local/{code}.json"
    response = await fetch_with_retry(CLIENT, "GET", url)
    if response.status_code == 200:
        return _unwrap_regions(response.json())
    if response.status_code != 403:
        response.raise_for_status()
    elif not _is_access_denied_body(response.text):
        raise RuntimeError(f"COMELEC results archive returned status 403: {response.text[:200]!r}")

    # Only a 7-digit code can be a barangay, so only a 7-digit code has a
    # precinct fallback. A 403 on "0" or a region code is a genuine unknown.
    if not _NODE_RE.match(code):
        return None

    precinct_url = f"{COMELEC_BASE}/data/regions/precinct/{code[:2]}/{code}.json"
    response = await fetch_with_retry(CLIENT, "GET", precinct_url)
    if response.status_code == 200:
        return _unwrap_regions(response.json())
    if response.status_code != 403:
        response.raise_for_status()
    elif not _is_access_denied_body(response.text):
        raise RuntimeError(f"COMELEC results archive returned status 403: {response.text[:200]!r}")
    return None


async def _get_data_frozen_at() -> tuple[str | None, str | None]:
    """(frozen_at, error_text). Cached 86400s on success only.

    A failure here must never fail the calling tool: it returns
    (None, error_text) so the caller can add the text to caveats and carry
    on with data_frozen_at: None.
    """
    cache = CACHES["comelec_meta"]
    key = "latest_time"
    if key in cache:
        return cache[key], None
    url = f"{COMELEC_BASE}/data/common/latestTime.json"
    try:
        response = await fetch_with_retry(CLIENT, "GET", url)
        response.raise_for_status()
        payload = response.json()
        frozen = datetime.strptime(f"{payload['date']} {payload['time']}", _FROZEN_FMT)
    except Exception as exc:
        log_stderr(f"COMELEC latestTime unavailable: {exc}")
        return None, f"COMELEC archive freeze time unavailable ({type(exc).__name__}: {exc})."
    value = frozen.isoformat()
    cache[key] = value
    return value, None


# One lock per cache key, the hdx._search_lock shape: twenty cold browse
# calls for one code must not become twenty tree fetches.
_MAX_TREE_LOCKS = 256
_TREE_LOCKS: dict[str, asyncio.Lock] = {}


def _tree_lock(key: str) -> asyncio.Lock:
    lock = _TREE_LOCKS.get(key)
    if lock is not None:
        return lock
    if len(_TREE_LOCKS) >= _MAX_TREE_LOCKS:
        for stale in [k for k, held in _TREE_LOCKS.items() if not held.locked()]:
            del _TREE_LOCKS[stale]
        if len(_TREE_LOCKS) >= _MAX_TREE_LOCKS:
            return asyncio.Lock()
    return _TREE_LOCKS.setdefault(key, asyncio.Lock())


@mcp.tool(
    title="Browse the COMELEC 2025 election results tree",
    tags={"comelec", "elections", "philippines", "government"},
    annotations={
        "title": "Browse the COMELEC 2025 election results tree",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def browse_election_results(code: str = "0") -> dict:
    """Walk the COMELEC 2025 election results tree: region down to precinct.

    Starts at the 20 regions when `code` is "0", the default. Pass a
    child's `code` from the response to go one level deeper. A barangay's
    children are its precincts, since the geography tree stops at the
    barangay level and precincts sit in a separate family. Examples:

      browse_election_results()                the 20 regions
      browse_election_results(code="R001000")   provinces of Region I
      browse_election_results(code="2801000")   barangays of Adams, Ilocos Norte
      browse_election_results(code="2801001")   precincts of one barangay

    On failure: a code that is not "0", a region code, or 7 digits gives
    validation_error true and data_status "invalid_request", with no
    request sent. A well-formed code the archive does not recognize gives
    the same shape after the lookup. A tree response whose rows all fail to
    parse gives data_status "indeterminate", never cached. A response with
    some bad rows returns the good rows, with a caveat naming the skipped
    count. A real outage gives upstream_error true and data_status
    "unavailable", with the real error in caveats.

    Args:
        code: "0" for the region list, a region code such as "R001000", or
              a 7-digit province, city, or barangay code from a previous
              call.

    Returns: code, level ("root", "region", "province",
    "city_municipality", or "barangay"), children (each with code, name,
    category_code, master_code), child_count, truncated, data_frozen_at,
    source, source_url, license, data_status, note, data_retrieved_at.
    """
    code = "0" if code is None else code.strip()
    if not _valid_browse_code(code):
        return failure_result(
            COMELEC_SOURCE,
            COMELEC_BASE,
            f"code must be '0', a region code like 'R001000', or a 7-digit "
            f"numeric code. Got {code!r}.",
            license=COMELEC_LICENSE,
            data_status=DATA_STATUS_INVALID_REQUEST,
            code=code,
            level=None,
            children=[],
            child_count=0,
            truncated=False,
            data_frozen_at=None,
            note=COMELEC_NOTE,
        )

    ckey = cache_key({"tool": "comelec_browse", "code": code})
    cache = CACHES["comelec_tree"]
    if ckey in cache:
        return cache[ckey]

    # Single-flight. Without it, concurrent cold browses of one code race
    # between the cache check and the write, and every one of them fetches.
    async with _tree_lock(ckey):
        if ckey in cache:
            return cache[ckey]
        return await _browse_uncached(code, ckey)


async def _browse_uncached(code: str, ckey: str) -> dict:
    cache = CACHES["comelec_tree"]
    frozen_at, frozen_err = await _get_data_frozen_at()

    try:
        raw_items = await _fetch_tree(code)
    except Exception as exc:
        log_stderr(f"COMELEC browse error: {exc}")
        return failure_result(
            COMELEC_SOURCE,
            COMELEC_BASE,
            f"COMELEC results archive unavailable ({type(exc).__name__}: {exc}).",
            license=COMELEC_LICENSE,
            code=code,
            level=None,
            children=[],
            child_count=0,
            truncated=False,
            data_frozen_at=frozen_at,
            note=COMELEC_NOTE,
        )

    if raw_items is None:
        return failure_result(
            COMELEC_SOURCE,
            COMELEC_BASE,
            f"No COMELEC record found for code {code!r}.",
            license=COMELEC_LICENSE,
            data_status=DATA_STATUS_INVALID_REQUEST,
            code=code,
            level=None,
            children=[],
            child_count=0,
            truncated=False,
            data_frozen_at=frozen_at,
            note=COMELEC_NOTE,
        )

    all_children, skipped = _parse_children(raw_items)
    if raw_items and not all_children:
        # Every row failed to parse. A tree endpoint that always has rows
        # sent none this run: that is drift, not a genuine empty listing.
        return failure_result(
            COMELEC_SOURCE,
            COMELEC_BASE,
            f"COMELEC tree endpoint for code {code!r} sent {len(raw_items)} "
            "row(s) but none carried a usable code.",
            license=COMELEC_LICENSE,
            data_status=DATA_STATUS_INDETERMINATE,
            code=code,
            level=None,
            children=[],
            child_count=0,
            truncated=False,
            data_frozen_at=frozen_at,
            note=COMELEC_NOTE,
        )

    truncated = len(all_children) > MAX_CHILDREN
    children = all_children[:MAX_CHILDREN]

    caveats = [frozen_err] if frozen_err else []
    if skipped:
        caveats.append(
            f"{skipped} of {len(raw_items)} row(s) in the COMELEC tree response "
            "carried no usable code and were skipped."
        )

    result = {
        "code": code,
        "level": _level_for_code(code),
        "children": children,
        "child_count": len(children),
        "truncated": truncated,
        "data_frozen_at": frozen_at,
        "source": COMELEC_SOURCE,
        "source_url": COMELEC_BASE,
        "license": COMELEC_LICENSE,
        "data_status": DATA_STATUS_SUCCESS if all_children else DATA_STATUS_EMPTY,
        "upstream_error": False,
        "validation_error": False,
        "caveats": caveats,
        "note": COMELEC_NOTE,
        "data_retrieved_at": _now().isoformat(),
    }
    # A frozen_err means the archive freeze time was temporarily
    # unreachable. Caching that degraded data_frozen_at: None would pin it
    # for the full 24h TTL even after latestTime.json recovers seconds
    # later, since the value never changes once it does resolve.
    if frozen_err is None:
        cache[ckey] = result
    return result


class _ContestParseError(Exception):
    """A contest's nested `candidates` value is present but not a list."""

    def __init__(self, contest_code: str):
        self.contest_code = contest_code
        super().__init__(f"non-list candidates in contest {contest_code!r}")


def _is_access_denied_body(body_text: str) -> bool:
    """True for the archive's own "unknown code" marker on a 403.

    The archive answers 403 both for a genuine unknown code, an S3-style
    body carrying `<Error><Code>AccessDenied</Code>...`, and for an
    infrastructure block such as a WAF or CDN page. Only the marked body is
    a real answer. Any other 403 body is an outage, not an answer.
    """
    return "AccessDenied" in body_text


def _parse_contest(raw: dict) -> dict:
    """One contest, or raises `_ContestParseError` if `candidates` is malformed.

    Two shapes count as malformed, not a genuine empty ballot: the outer
    `contest["candidates"]` value present but not a dict, and the nested
    `contest["candidates"]["candidates"]` value present but not a list. A
    missing key at either level stays a genuine empty candidate list. The
    caller catches `_ContestParseError` and reports the whole return as
    indeterminate, never cached.
    """
    wrapper = raw.get("candidates")
    if wrapper is not None and not isinstance(wrapper, dict):
        raise _ContestParseError(raw.get("contestCode") or "")
    inner = wrapper.get("candidates") if isinstance(wrapper, dict) else None
    if inner is not None and not isinstance(inner, list):
        raise _ContestParseError(raw.get("contestCode") or "")
    candidates = [
        {
            "name": c.get("name", ""),
            "votes": c.get("votes"),
            "percentage": c.get("percentage"),
        }
        for c in (inner or [])
        if isinstance(c, dict)
    ]
    return {
        "contest_code": raw.get("contestCode", ""),
        "contest_name": raw.get("contestName", ""),
        "statistics": raw.get("statistic") or {},
        "candidates": candidates,
    }


# One lock per cache key, the hdx._search_lock shape: twenty cold ER calls
# for one precinct must not become twenty ER fetches.
_MAX_RETURN_LOCKS = 256
_RETURN_LOCKS: dict[str, asyncio.Lock] = {}


def _return_lock(key: str) -> asyncio.Lock:
    lock = _RETURN_LOCKS.get(key)
    if lock is not None:
        return lock
    if len(_RETURN_LOCKS) >= _MAX_RETURN_LOCKS:
        for stale in [k for k, held in _RETURN_LOCKS.items() if not held.locked()]:
            del _RETURN_LOCKS[stale]
        if len(_RETURN_LOCKS) >= _MAX_RETURN_LOCKS:
            return asyncio.Lock()
    return _RETURN_LOCKS.setdefault(key, asyncio.Lock())


@mcp.tool(
    title="COMELEC 2025 precinct election return",
    tags={"comelec", "elections", "philippines", "government"},
    annotations={
        "title": "COMELEC 2025 precinct election return",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_election_return(precinct_code: str) -> dict:
    """One precinct's official vote tally from the COMELEC 2025 archive.

    Give the 8-digit precinct code from browse_election_results. Returns
    every national and local contest counted at that precinct, with each
    candidate's vote count and share. Examples:

      get_election_return("28010001")   Adams, Ilocos Norte, precinct 0001

    On failure: a code that is not 8 digits gives validation_error true and
    data_status "invalid_request", with no request sent. A code the
    archive does not recognize (a 403 body carrying its AccessDenied
    marker) gives the same shape. A body missing 'information', where
    'national' or 'local' is not a list, or where a contest carries a
    non-list nested 'candidates' value, gives data_status "indeterminate"
    with upstream_error true. Any other outage, including a 403 with no
    AccessDenied marker, gives data_status "unavailable" with
    upstream_error true and the real error in caveats.

    Args:
        precinct_code: 8-digit precinct code, such as "28010001".

    Returns: precinct_code, information (machine_id, location,
    voting_center, and voter counts, snake_case keys), total_er_received,
    national_contests and local_contests (each with contest_code,
    contest_name, statistics, candidates), data_frozen_at, source,
    source_url, license, data_status, note, data_retrieved_at.
    """
    code = (precinct_code or "").strip()
    if not _PRECINCT_RE.match(code):
        return failure_result(
            COMELEC_SOURCE,
            COMELEC_BASE,
            f"precinct_code must be exactly 8 digits. Got {precinct_code!r}.",
            license=COMELEC_LICENSE,
            data_status=DATA_STATUS_INVALID_REQUEST,
            precinct_code=precinct_code or "",
            information=None,
            total_er_received=None,
            national_contests=[],
            local_contests=[],
            data_frozen_at=None,
            note=COMELEC_NOTE,
        )

    ckey = cache_key({"tool": "comelec_er", "code": code})
    cache = CACHES["comelec_return"]
    if ckey in cache:
        return cache[ckey]

    # Single-flight. Without it, concurrent cold calls for one precinct race
    # between the cache check and the write, and every one of them fetches.
    async with _return_lock(ckey):
        if ckey in cache:
            return cache[ckey]
        return await _get_election_return_uncached(code, ckey)


async def _get_election_return_uncached(code: str, ckey: str) -> dict:
    cache = CACHES["comelec_return"]
    frozen_at, frozen_err = await _get_data_frozen_at()

    def _fail(message: str, *, data_status: str | None = None) -> dict:
        return failure_result(
            COMELEC_SOURCE,
            COMELEC_BASE,
            message,
            license=COMELEC_LICENSE,
            data_status=data_status,
            precinct_code=code,
            information=None,
            total_er_received=None,
            national_contests=[],
            local_contests=[],
            data_frozen_at=frozen_at,
            note=COMELEC_NOTE,
        )

    url = f"{COMELEC_BASE}/data/er/{code[:3]}/{code}.json"
    try:
        response = await fetch_with_retry(CLIENT, "GET", url)
        if response.status_code == 403:
            if _is_access_denied_body(response.text):
                return _fail(
                    f"No COMELEC election return found for precinct {code!r}.",
                    data_status=DATA_STATUS_INVALID_REQUEST,
                )
            # A 403 with no AccessDenied marker is an infrastructure block
            # (a WAF or CDN page), not the archive's own "unknown code"
            # answer. Treating it as invalid_request would tell an agent a
            # real precinct does not exist.
            return _fail(f"COMELEC results archive returned status 403: {response.text[:200]!r}")
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        log_stderr(f"COMELEC election return fetch error: {exc}")
        return _fail(f"COMELEC results archive unavailable ({type(exc).__name__}: {exc}).")

    information = payload.get("information") if isinstance(payload, dict) else None
    national_raw = payload.get("national") if isinstance(payload, dict) else None
    if not isinstance(information, dict) or not isinstance(national_raw, list):
        return _fail(
            f"COMELEC election return for precinct {code!r} is missing "
            "'information' or 'national'.",
            data_status=DATA_STATUS_INDETERMINATE,
        )

    # `local` follows the same rule as `national`: a value that is present
    # but not a list is drift, not an empty local ballot. A missing key
    # stays a genuine empty list, since some precincts carry no local race.
    local_raw = payload.get("local")
    if local_raw is not None and not isinstance(local_raw, list):
        return _fail(
            f"COMELEC election return for precinct {code!r} has a non-list 'local' value.",
            data_status=DATA_STATUS_INDETERMINATE,
        )
    local_list = local_raw or []

    # A contest row is always an object in the archive. A scalar or a list in
    # its place is drift, and skipping it would cache an empty ballot as a
    # real result, so the whole return is indeterminate instead.
    bad_rows = [c for c in [*national_raw, *local_list] if not isinstance(c, dict)]
    if bad_rows:
        return _fail(
            f"COMELEC election return for precinct {code!r} has {len(bad_rows)} contest "
            f"row(s) that are not objects, for example {bad_rows[0]!r}.",
            data_status=DATA_STATUS_INDETERMINATE,
        )

    try:
        national_contests = [_parse_contest(c) for c in national_raw]
        local_contests = [_parse_contest(c) for c in local_list]
    except _ContestParseError as exc:
        return _fail(
            f"COMELEC election return for precinct {code!r} has a non-list "
            f"'candidates' value in contest {exc.contest_code!r}.",
            data_status=DATA_STATUS_INDETERMINATE,
        )

    result = {
        "precinct_code": code,
        "information": {_snake_case(k): v for k, v in information.items()},
        "total_er_received": payload.get("totalErReceived"),
        "national_contests": national_contests,
        "local_contests": local_contests,
        "data_frozen_at": frozen_at,
        "source": COMELEC_SOURCE,
        "source_url": COMELEC_BASE,
        "license": COMELEC_LICENSE,
        "data_status": DATA_STATUS_SUCCESS,
        "upstream_error": False,
        "validation_error": False,
        "caveats": [frozen_err] if frozen_err else [],
        "note": COMELEC_NOTE,
        "data_retrieved_at": _now().isoformat(),
    }
    # A frozen_err means the archive freeze time was temporarily
    # unreachable. Caching that degraded data_frozen_at: None would pin it
    # for the full 24h TTL even after latestTime.json recovers seconds
    # later, since the value never changes once it does resolve.
    if frozen_err is None:
        cache[ckey] = result
    return result
