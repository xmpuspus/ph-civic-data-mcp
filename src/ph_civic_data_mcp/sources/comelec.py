"""COMELEC 2025 election results archive — precinct-level vote tallies.

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
COMELEC_LICENSE = "Public — COMELEC 2025 election results archive"
COMELEC_NOTE = (
    "Official COMELEC 2025 election returns as published on the results "
    "archive. Retrieval only, no interpretation."
)

# "0" is the root. A region code is "R" plus 6 more characters. Most are
# digits ("R001000"), but live-checked 2026-09-04: 6 of the 20 root
# entries are not ("R04A000" CALABARZON, "R04B000" MIMAROPA, "R00LAV0",
# "R00NIR0", "R0BARMM", "R0CAR00", "R0NCR00" — NCR itself). A digits-only
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


def _parse_children(items: list[dict]) -> list[dict]:
    children: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "")
        if not code:
            continue
        children.append(
            {
                "code": code,
                "name": item.get("name"),
                "category_code": item.get("categoryCode"),
                "master_code": item.get("masterCode"),
            }
        )
    return children


async def _fetch_tree(code: str) -> list[dict] | None:
    """Children of `code` in the local geography tree, or its precincts.

    Returns None when the archive answers 403 at every path this code can
    take, meaning the code is unknown. Raises on a transport failure or a
    status other than 200 or 403, since that is an outage, not an answer.
    """
    url = f"{COMELEC_BASE}/data/regions/local/{code}.json"
    response = await fetch_with_retry(CLIENT, "GET", url)
    if response.status_code == 200:
        return _unwrap_regions(response.json())
    if response.status_code != 403:
        response.raise_for_status()

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
    the same shape after the lookup. A real outage gives upstream_error
    true and data_status "unavailable", with the real error in caveats.

    Args:
        code: "0" for the region list, a region code such as "R001000", or
              a 7-digit province, city, or barangay code from a previous
              call.

    Returns: code, level ("root", "region", "province",
    "city_municipality", or "barangay"), children (each with code, name,
    category_code, master_code), child_count, truncated, data_frozen_at,
    source, source_url, license, data_status, note, data_retrieved_at.
    """
    code = (code or "0").strip()
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

    all_children = _parse_children(raw_items)
    truncated = len(all_children) > MAX_CHILDREN
    children = all_children[:MAX_CHILDREN]

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


def _parse_contest(raw: dict) -> dict:
    wrapper = raw.get("candidates")
    inner = wrapper.get("candidates") if isinstance(wrapper, dict) else None
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
    archive does not recognize gives the same shape. A body missing
    'information', or where 'national' is not a list, gives data_status
    "indeterminate" with upstream_error true. A real outage gives
    data_status "unavailable" with upstream_error true and the real error
    in caveats.

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
            return _fail(
                f"No COMELEC election return found for precinct {code!r}.",
                data_status=DATA_STATUS_INVALID_REQUEST,
            )
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

    local_raw = payload.get("local")
    local_list = local_raw if isinstance(local_raw, list) else []

    result = {
        "precinct_code": code,
        "information": {_snake_case(k): v for k, v in information.items()},
        "total_er_received": payload.get("totalErReceived"),
        "national_contests": [_parse_contest(c) for c in national_raw if isinstance(c, dict)],
        "local_contests": [_parse_contest(c) for c in local_list if isinstance(c, dict)],
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
