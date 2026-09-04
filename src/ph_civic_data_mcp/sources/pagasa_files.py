"""PAGASA public files — nginx directory listings of advisory, bulletin, and
storm surge PDFs under pubfiles.pagasa.dost.gov.ph/tamss/weather/<kind>/.

Landmines (from the round-3 source probe):
- The real folders sit at /tamss/weather/<kind>/, never at the pubfiles root.
  The root paths named in an earlier task lead all 404.
- bulletin holds IWS#2_pilandok.pdf alongside the TCB#<n>_<storm>.pdf files,
  so a parser must never assume a TCB# prefix.
- stormsurge has not published since 2019-12-02. Every stormsurge response
  carries a warning, so an agent never reads a stale file as a live one.
- This tool returns file URLs only, never PDF bytes.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_INDETERMINATE,
    DATA_STATUS_SUCCESS,
    failure_result,
)
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

PAGASA_FILES_LICENSE = "Public — PAGASA public file listing"

PAGASA_FILES_BASE = "https://pubfiles.pagasa.dost.gov.ph/tamss/weather"

KIND_FOLDERS = ("weather_advisory", "bulletin", "stormsurge")

MANILA_TZ = timezone(timedelta(hours=8))

STORMSURGE_STALE_CAVEAT = (
    "The stormsurge folder has not published a new file since 2019-12-02. "
    "Nothing in it is a live storm surge warning."
)

# nginx rounds a listed size to the nearest K, M, or G once a file passes
# roughly 1000 bytes. The real Advisory#50.pdf is 288,709 bytes, and the
# index shows it as "282K". size_bytes then reads 288,768: close, not exact.
SIZE_APPROXIMATE_CAVEAT = (
    "This index rounds file sizes to the nearest K, M, or G. size_bytes is "
    "an approximate value here, not the exact file size."
)

_SIZE_MULTIPLIER = {"K": 1024, "M": 1024**2, "G": 1024**3}

# The tail text after each <a> in an nginx autoindex row holds a
# "DD-Mon-YYYY HH:MM" timestamp, then a size token. The size token reads
# "240K", "-" for none, or a bare byte count on a plain-size server.
_ROW_TAIL_RE = re.compile(r"^(\d{1,2}-[A-Za-z]{3}-\d{4}\s+\d{1,2}:\d{2})\s+(\S+)$")


class PagasaFilesIndeterminateError(RuntimeError):
    """The folder response did not read as a real directory listing.

    Covers a 404, a body with no "Index of" heading, and a page that parses
    as an index but lists zero files. A folder this tool serves always has
    files, so zero is drift, never a real empty folder.
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _folder_url(kind: str) -> str:
    return f"{PAGASA_FILES_BASE}/{kind}/"


def _parse_size(token: str) -> tuple[int | None, bool]:
    """Read one size token. The bool is True when nginx rounded it to a
    K/M/G unit, so the caller knows size_bytes is then an estimate."""
    token = token.strip()
    if not token or token == "-":
        return None, False
    match = re.match(r"^([\d.]+)([KMG])?$", token)
    if not match:
        return None, False
    value = float(match.group(1))
    suffix = match.group(2)
    multiplier = _SIZE_MULTIPLIER.get(suffix or "", 1)
    return int(value * multiplier), suffix is not None


def _parse_autoindex(html: str, folder_url: str) -> tuple[list[dict], bool] | None:
    """Parse an nginx autoindex page into file rows, newest first.

    Returns None when the body carries no "Index of" heading. That means an
    error page, a redirect target, or another unrelated 200 response, not a
    real directory listing. A caller treats None the same as a 404. The
    second value in the tuple is True when any row's size was rounded to a
    K/M/G unit, so its size_bytes is an estimate, not an exact byte count.
    """
    soup = BeautifulSoup(html, "lxml")
    heading = soup.find("h1")
    if heading is None or "index of" not in heading.get_text(" ", strip=True).lower():
        return None

    pre = soup.find("pre")
    anchors = pre.find_all("a", href=True) if pre else soup.find_all("a", href=True)

    files: list[dict] = []
    size_is_approximate = False
    for anchor in anchors:
        href = anchor["href"]
        if href in ("../", "./") or href.endswith("/"):
            continue  # the parent-directory link, or a subfolder this tool does not list

        tail = anchor.next_sibling
        tail_text = str(tail).strip() if tail else ""
        match = _ROW_TAIL_RE.match(tail_text)
        if match is None:
            continue  # a row this parser cannot read cleanly is skipped, not guessed

        date_text, size_text = match.group(1), match.group(2)
        try:
            local_dt = date_parser.parse(date_text)
        except (ValueError, OverflowError):
            continue
        last_modified = local_dt.replace(tzinfo=MANILA_TZ).isoformat()
        size_bytes, size_rounded = _parse_size(size_text)
        size_is_approximate = size_is_approximate or size_rounded

        files.append(
            {
                "name": anchor.get_text(strip=True),
                "url": urljoin(folder_url, href),
                "last_modified": last_modified,
                "size_bytes": size_bytes,
            }
        )

    files.sort(key=lambda entry: entry["last_modified"], reverse=True)
    return files, size_is_approximate


async def _fetch_files(kind: str) -> tuple[list[dict], bool]:
    """Fetch and parse one folder's file list, cached 900s on success only.

    Raises PagasaFilesIndeterminateError for a 404 or an unparsable body.
    Any other exception (a transport failure, a 5xx) propagates as-is and
    is never cached, so a transient outage cannot pin a stale answer.
    """
    key = cache_key({"kind": kind})
    cache = CACHES["pagasa_files"]
    if key in cache:
        return cache[key]

    folder_url = _folder_url(kind)
    response = await fetch_with_retry(CLIENT, "GET", folder_url)
    if response.status_code == 404:
        raise PagasaFilesIndeterminateError(f"{folder_url} returned 404")
    response.raise_for_status()

    parsed = _parse_autoindex(response.text, folder_url)
    if parsed is None:
        raise PagasaFilesIndeterminateError(
            f"{folder_url} body carries no 'Index of' heading, not a directory listing"
        )
    files, size_is_approximate = parsed
    if not files:
        raise PagasaFilesIndeterminateError(
            f"{folder_url} parsed zero files. This folder always has some."
        )

    cache[key] = (files, size_is_approximate)
    return files, size_is_approximate


@mcp.tool(
    title="PAGASA public advisory, bulletin, and storm surge files",
    tags={"pagasa", "weather", "typhoon", "philippines", "bulletin", "advisory"},
    annotations={
        "title": "PAGASA public advisory, bulletin, and storm surge files",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def list_pagasa_advisory_files(kind: str = "weather_advisory", limit: int = 20) -> dict:
    """List PAGASA public PDF files from a pubfiles directory listing, newest first.

    Reads the nginx directory index PAGASA publishes at
    pubfiles.pagasa.dost.gov.ph/tamss/weather/<kind>/ and returns each PDF's
    name, URL, last-modified time, and size. This tool returns file URLs
    only, never PDF bytes, so fetch a PDF yourself if you need its
    contents. Examples:

      list_pagasa_advisory_files()                            latest weather advisories
      list_pagasa_advisory_files(kind="bulletin", limit=5)     5 newest cyclone bulletins
      list_pagasa_advisory_files(kind="stormsurge")            storm surge folder, flagged stale

    On failure: an unknown kind, or a limit outside 1 to 100, gives
    validation_error true and data_status "invalid_request". A 404, a body
    with no directory listing, or zero parsed files gives data_status
    "indeterminate". An unreachable host gives data_status "unavailable".
    All three carry files: [] and the real error in caveats.

    Args:
        kind: One of "weather_advisory", "bulletin", "stormsurge".
        limit: Max files to return, newest first (1 to 100, default 20).

    Returns: kind, folder_url, files (each with name, url, last_modified,
    size_bytes), file_count (before the limit cut), latest_name, latest_url,
    latest_modified, data_status, caveats, source, source_url,
    data_retrieved_at. The stormsurge kind always adds a warning to caveats
    that the folder has not published since 2019-12-02. When the index
    rounds a size to K, M, or G, caveats also warns that size_bytes is then
    an estimate, not an exact byte count.
    """
    folder_url = _folder_url(kind)
    if kind not in KIND_FOLDERS:
        return failure_result(
            "PAGASA public files",
            folder_url,
            f"kind must be one of {list(KIND_FOLDERS)}, got {kind!r}.",
            license=PAGASA_FILES_LICENSE,
            validation_error=True,
            kind=kind,
            folder_url=folder_url,
            files=[],
        )
    if not (1 <= limit <= 100):
        return failure_result(
            "PAGASA public files",
            folder_url,
            f"limit must be between 1 and 100, got {limit}.",
            license=PAGASA_FILES_LICENSE,
            validation_error=True,
            kind=kind,
            folder_url=folder_url,
            files=[],
        )

    try:
        files, size_is_approximate = await _fetch_files(kind)
    except PagasaFilesIndeterminateError as exc:
        return failure_result(
            "PAGASA public files",
            folder_url,
            f"{folder_url} did not read as a live directory listing: {exc}.",
            license=PAGASA_FILES_LICENSE,
            data_status=DATA_STATUS_INDETERMINATE,
            kind=kind,
            folder_url=folder_url,
            files=[],
        )
    except Exception as exc:
        log_stderr(f"list_pagasa_advisory_files error: {exc}")
        return failure_result(
            "PAGASA public files",
            folder_url,
            f"{folder_url} unavailable ({type(exc).__name__}: {exc}).",
            license=PAGASA_FILES_LICENSE,
            kind=kind,
            folder_url=folder_url,
            files=[],
        )

    file_count = len(files)
    trimmed = files[:limit]
    latest = files[0] if files else None
    caveats: list[str] = []
    if kind == "stormsurge":
        caveats.append(STORMSURGE_STALE_CAVEAT)
    if size_is_approximate:
        caveats.append(SIZE_APPROXIMATE_CAVEAT)

    return {
        "kind": kind,
        "folder_url": folder_url,
        "files": trimmed,
        "file_count": file_count,
        "latest_name": latest["name"] if latest else None,
        "latest_url": latest["url"] if latest else None,
        "latest_modified": latest["last_modified"] if latest else None,
        "data_status": DATA_STATUS_SUCCESS,
        "upstream_error": False,
        "validation_error": False,
        "caveats": caveats,
        "source": "PAGASA public files",
        "source_url": folder_url,
        "license": PAGASA_FILES_LICENSE,
        "data_retrieved_at": _now().isoformat(),
    }
