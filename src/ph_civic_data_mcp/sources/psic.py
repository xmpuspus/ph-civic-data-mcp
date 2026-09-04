"""PSIC: Philippine Standard Industrial Classification code lookup.

PSA publishes the full PSIC table as one HTML page at the URL below, no key
needed, under CC BY 4.0. The bare `psa.gov.ph/classification/` index page
sits behind a Cloudflare JS challenge, so this module calls the exact data
path only and never the index.

The page holds one fixed table of roughly 1,360 rows. This module fetches it
once, parses it, and caches the parsed rows for 24 hours on success. A query
then matches in memory against the cached rows, never against a fresh fetch.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_EMPTY,
    DATA_STATUS_INDETERMINATE,
    DATA_STATUS_SUCCESS,
    DATA_STATUS_UNAVAILABLE,
    failure_result,
)
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

SOURCE_NAME = "PSIC"
SOURCE_URL = "https://psa.gov.ph/classification/psic/search-results"
PSIC_LICENSE = "PSA Philippine Standard Industrial Classification (PSIC), CC BY 4.0"

MIN_QUERY_LEN = 1
MAX_QUERY_LEN = 100
MIN_LIMIT = 1
MAX_LIMIT = 100

# A fetch that returns a fragment (a WAF probe page, a truncated response)
# still parses to a nonempty row list, which would then cache and answer
# every later query as though PSIC held one industry. The real table has
# roughly 1,360 rows across 21 sections A to U. Below this row floor, or
# missing a whole section, the fetch counts as failed, never as a smaller
# table.
MIN_TABLE_ROWS = 1000

# PSIC 2009 (ISIC Rev.4) groups every division into one of 21 sections
# (A-U). Live-verified 2026-09-04: the search-results page never prints a
# section-level row of its own, every one of its 1360 rows is a subclass,
# so the letter has to come from the 2-digit division prefix of each row's
# code instead. Every division the live page carries (01-99, with gaps)
# fell inside exactly one of these ranges, none left over.
_SECTION_DIVISION_RANGES: tuple[tuple[str, range], ...] = (
    ("A", range(1, 4)),
    ("B", range(5, 10)),
    ("C", range(10, 34)),
    ("D", range(35, 36)),
    ("E", range(36, 40)),
    ("F", range(41, 44)),
    ("G", range(45, 48)),
    ("H", range(49, 54)),
    ("I", range(55, 57)),
    ("J", range(58, 64)),
    ("K", range(64, 67)),
    ("L", range(68, 69)),
    ("M", range(69, 76)),
    ("N", range(77, 83)),
    ("O", range(84, 85)),
    ("P", range(85, 86)),
    ("Q", range(86, 89)),
    ("R", range(90, 94)),
    ("S", range(94, 97)),
    ("T", range(97, 99)),
    ("U", range(99, 100)),
)
_ALL_SECTION_LETTERS = frozenset(letter for letter, _ in _SECTION_DIVISION_RANGES)

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_CODE_QUERY_RE = re.compile(r"^\d+$")

# A PSIC href looks like /classification/psic/class/0111. The level word sits
# as one path segment, so a plain split finds it without a fixed position.
_LEVEL_WORDS = {"section", "division", "group", "class", "subclass"}
_LEVEL_BY_CODE_LENGTH = {1: "section", 2: "division", 3: "group", 4: "class", 5: "subclass"}

# Live probe on 2026-09-04: the code cell text is not the bare code. It reads
# "Subclass 01111", with the level word spelled out ahead of the code, for
# every one of the 1360 rows on this page (the page lists subclasses only).
# That level word is read first, since it is the most direct signal PSA
# gives. The href segment and the code length stay as fallbacks for a row
# that carries no such prefix.
_LEVEL_PREFIX_RE = re.compile(r"^(section|division|group|class|subclass)\s+(.+)$", re.IGNORECASE)


class PsicChallengeError(RuntimeError):
    """PSA served the Cloudflare interstitial instead of the PSIC table."""


class PsicParseError(RuntimeError):
    """The page loaded but the PSIC table is missing, empty, or unreadable."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _level_for(href: str | None, code: str) -> str:
    if href:
        for segment in href.split("/"):
            if segment in _LEVEL_WORDS:
                return segment
    return _LEVEL_BY_CODE_LENGTH.get(len(code), "unknown")


def _parse_psic_table(html: str) -> list[dict]:
    """Parse the `psicdata` table into `{code, level, description}` rows.

    Raises PsicChallengeError on a Cloudflare challenge body, and
    PsicParseError when the table is missing or its body carries no
    readable row. Both are HTML drift, never "the table is genuinely empty".
    """
    if "Just a moment" in html:
        raise PsicChallengeError("unavailable (Cloudflare challenge page, not the PSIC table)")

    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", id="psicdata")
    if table is None:
        raise PsicParseError(
            'no <table id="psicdata"> on the PSIC search-results page (HTML drift?)'
        )

    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else []
    if not rows:
        raise PsicParseError("PSIC table body has zero rows (HTML drift?)")

    entries: list[dict] = []
    for row in rows:
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        desc_cell, code_cell = cells[0], cells[1]
        raw_code = code_cell.get_text(" ", strip=True)
        if not raw_code:
            continue
        link = desc_cell.find("a")
        description = (
            link.get_text(" ", strip=True) if link else desc_cell.get_text(" ", strip=True)
        )
        href = link.get("href") if link else None

        prefix_match = _LEVEL_PREFIX_RE.match(raw_code)
        if prefix_match:
            level, code = prefix_match.group(1).lower(), prefix_match.group(2).strip()
        else:
            code, level = raw_code, _level_for(href, raw_code)
        if not code:
            continue
        entries.append({"code": code, "level": level, "description": description})

    if not entries:
        raise PsicParseError("PSIC table rows carried no readable code (HTML drift?)")

    return entries


def _section_for(entry: dict) -> str | None:
    """The PSIC section letter (A-U) an entry's code falls under.

    A `section`-level row's own code is already the letter. Every other
    level's code is numeric, and its first two digits are the division, so
    the letter comes from `_SECTION_DIVISION_RANGES` instead. Returns None
    for a code that carries no readable division.
    """
    code = entry["code"]
    if entry["level"] == "section" and len(code) == 1 and code.isalpha():
        return code.upper()
    division_text = code[:2]
    if not division_text.isdigit():
        return None
    division = int(division_text)
    for letter, span in _SECTION_DIVISION_RANGES:
        if division in span:
            return letter
    return None


def _check_table_complete(entries: list[dict]) -> None:
    """Raise PsicParseError when the parsed rows are a fragment of the table.

    A row count below MIN_TABLE_ROWS, or a PSIC section with no row at all,
    means the fetch caught a partial page rather than the full table. The
    caller must treat this the same as a missing table: indeterminate, and
    never cached.
    """
    sections_seen = {s for s in (_section_for(e) for e in entries) if s}
    missing = sorted(_ALL_SECTION_LETTERS - sections_seen)
    if len(entries) >= MIN_TABLE_ROWS and not missing:
        return
    missing_text = ", ".join(missing) if missing else "none"
    raise PsicParseError(
        f"PSIC table looks incomplete: {len(entries)} rows parsed (want at least "
        f"{MIN_TABLE_ROWS}), missing section(s) {missing_text} (HTML drift or a "
        "partial fetch?)"
    )


_FETCH_LOCK = asyncio.Lock()


async def _psic_table() -> list[dict]:
    """Fetch, parse, and cache the full PSIC table. Never caches a failure."""
    key = cache_key({"endpoint": "psic_table"})
    cache = CACHES["psic_table"]
    if key in cache:
        return cache[key]

    async with _FETCH_LOCK:
        # Re-check: the holder before us may have filled the cache already.
        if key in cache:
            return cache[key]
        response = await fetch_with_retry(CLIENT, "GET", SOURCE_URL)
        if "Just a moment" in response.text:
            raise PsicChallengeError("unavailable (Cloudflare challenge page, not the PSIC table)")
        response.raise_for_status()
        entries = _parse_psic_table(response.text)
        _check_table_complete(entries)
        cache[key] = entries
        return entries


def _token_match(needle: str, haystack: str) -> bool:
    """Match on whole tokens only, never on a fragment. Mirrors psa._token_match."""
    if not needle:
        return False
    want = [tok for tok in _TOKEN_SPLIT.split(needle) if tok]
    have = [tok for tok in _TOKEN_SPLIT.split(haystack) if tok]
    if not want or len(want) > len(have):
        return False
    return any(have[i : i + len(want)] == want for i in range(len(have) - len(want) + 1))


@mcp.tool(
    title="PSIC industrial classification search",
    tags={"psa", "psic", "classification", "philippines", "industry"},
    annotations={
        "title": "PSIC industrial classification search",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def search_psic_codes(query: str, limit: int = 20) -> dict:
    """Find a PSIC code by description keyword or by code prefix.

    Matches a query of only digits against the PSIC code prefix. Matches any
    other query by whole-word text against the class description,
    case-insensitive. PSA publishes the full table (roughly 1,360 rows) on
    one page, so the first call fetches and caches it for 24 hours. Examples:

      search_psic_codes("rice")             every description with "rice" as a whole word
      search_psic_codes("0111")             every code that starts with "0111"
      search_psic_codes("mining", limit=5)  at most 5 matches

    On failure: data_status is "invalid_request" for an empty, over-long, or
    non-printable query, or for a limit outside 1-100. A Cloudflare
    challenge in place of the table gives "unavailable". A missing, empty, or
    partial PSIC table gives "indeterminate". Neither failure is cached, and
    the real error sits in caveats.

    Args:
        query: Digits for a code-prefix match, or text for a whole-word
               match against the description, case-insensitive. 1 to 100
               printable characters.
        limit: Maximum number of matches to return (1-100).

    Returns: query, matches (each with code, level, description),
    match_count, total_codes, truncated, data_status, source, source_url,
    license, data_retrieved_at, caveats.
    """
    if not isinstance(query, str) or not (MIN_QUERY_LEN <= len(query) <= MAX_QUERY_LEN):
        return failure_result(
            SOURCE_NAME,
            SOURCE_URL,
            f"query must be {MIN_QUERY_LEN} to {MAX_QUERY_LEN} characters, got "
            f"{len(query) if isinstance(query, str) else type(query).__name__}.",
            license=PSIC_LICENSE,
            validation_error=True,
            query=query,
            matches=[],
            match_count=0,
            total_codes=0,
            truncated=False,
        )
    if not query.isprintable() or not query.strip():
        return failure_result(
            SOURCE_NAME,
            SOURCE_URL,
            "query must hold at least one non-space printable character.",
            license=PSIC_LICENSE,
            validation_error=True,
            query=query,
            matches=[],
            match_count=0,
            total_codes=0,
            truncated=False,
        )
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not (MIN_LIMIT <= limit <= MAX_LIMIT)
    ):
        return failure_result(
            SOURCE_NAME,
            SOURCE_URL,
            f"limit must be a whole number from {MIN_LIMIT} to {MAX_LIMIT}, got {limit!r}.",
            license=PSIC_LICENSE,
            validation_error=True,
            query=query,
            matches=[],
            match_count=0,
            total_codes=0,
            truncated=False,
        )

    try:
        table = await _psic_table()
    except PsicChallengeError as exc:
        status = DATA_STATUS_UNAVAILABLE
        message = f"PSIC page {exc}."
    except PsicParseError as exc:
        status = DATA_STATUS_INDETERMINATE
        message = str(exc)
    except Exception as exc:
        status = DATA_STATUS_UNAVAILABLE
        message = f"PSIC page unavailable ({type(exc).__name__}: {exc})."
    else:
        status = None
        message = ""

    if status is not None:
        log_stderr(f"search_psic_codes error: {message}")
        return failure_result(
            SOURCE_NAME,
            SOURCE_URL,
            message,
            license=PSIC_LICENSE,
            data_status=status,
            query=query,
            matches=[],
            match_count=0,
            total_codes=0,
            truncated=False,
        )

    query_norm = query.strip()
    if _CODE_QUERY_RE.match(query_norm):
        hits = [e for e in table if e["code"].startswith(query_norm)]
    else:
        needle = query_norm.lower()
        hits = [e for e in table if _token_match(needle, e["description"].lower())]

    matches = hits[:limit]
    truncated = len(hits) > limit

    caveats: list[str] = []
    if truncated:
        caveats.append(f"Returned {limit} of {len(hits)} matches. Raise limit or narrow the query.")
    if not matches:
        caveats.append(f"No PSIC code or description matched {query_norm!r}.")

    return {
        "query": query,
        "matches": matches,
        "match_count": len(matches),
        "total_codes": len(table),
        "truncated": truncated,
        "data_status": DATA_STATUS_SUCCESS if matches else DATA_STATUS_EMPTY,
        "upstream_error": False,
        "validation_error": False,
        "caveats": caveats,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "license": PSIC_LICENSE,
        "data_retrieved_at": _now().isoformat(),
    }
