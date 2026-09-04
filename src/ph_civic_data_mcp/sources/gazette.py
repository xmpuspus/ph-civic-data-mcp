"""Official Gazette of the Republic of the Philippines: RSS feed of issuances.

Landmine (from the round-3 probe): every path on this host except `/feed/`
and `/feed/?paged=N` returns a Cloudflare "Attention Required" block page,
and a HEAD request is blocked even on `/feed/`. This module sends a GET to
those two URL shapes and nothing else, ever.
"""

from __future__ import annotations

import re
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import (
    DATA_STATUS_EMPTY,
    DATA_STATUS_INDETERMINATE,
    DATA_STATUS_SUCCESS,
    failure_result,
)
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

GAZETTE_FEED_URL = "https://www.officialgazette.gov.ph/feed/"
GAZETTE_SOURCE = "Official Gazette of the Republic of the Philippines RSS feed, government record"
# The feed carries no <copyright> or <license> element, and every other page
# on the host is blocked, so this probe could not read a terms page. RA 8293
# section 176 (no protection for official government records) applies by
# default.
GAZETTE_LICENSE = "Public, Official Gazette government record, RA 8293 section 176 default"

MAX_PAGE = 50
DESCRIPTION_CAP = 500

_TAG_RE = re.compile(r"<[^>]+>")
_DC_NS = {"dc": "http://purl.org/dc/elements/1.1/"}


class GazetteBlockedError(RuntimeError):
    """The host answered with a Cloudflare block page, not the feed."""

    def __init__(self, status_code: int, content_type: str):
        self.status_code = status_code
        self.content_type = content_type
        super().__init__(f"Cloudflare block (status {status_code}, content-type {content_type!r})")


# Single-flight per page, the hdx._search_lock shape: twenty cold calls for
# page 1 must not become twenty GETs against a Cloudflare-fronted host.
_MAX_PAGE_LOCKS = 64
_PAGE_LOCKS: dict[str, asyncio.Lock] = {}


def _page_lock(key: str) -> asyncio.Lock:
    lock = _PAGE_LOCKS.get(key)
    if lock is not None:
        return lock
    if len(_PAGE_LOCKS) >= _MAX_PAGE_LOCKS:
        for stale in [k for k, held in _PAGE_LOCKS.items() if not held.locked()]:
            del _PAGE_LOCKS[stale]
        if len(_PAGE_LOCKS) >= _MAX_PAGE_LOCKS:
            return asyncio.Lock()
    return _PAGE_LOCKS.setdefault(key, asyncio.Lock())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _feed_url(page: int) -> str:
    return GAZETTE_FEED_URL if page == 1 else f"{GAZETTE_FEED_URL}?paged={page}"


def _strip_html(text: str) -> str:
    """Remove markup and cap length, so a description never carries tags."""
    return _TAG_RE.sub("", text or "").strip()[:DESCRIPTION_CAP]


def _parse_pub_date(raw: str) -> str | None:
    """RFC 2822 pubDate to ISO 8601 with an offset. None on a missing or bad date."""
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _looks_like_cloudflare_block(content_type: str, body_text: str) -> bool:
    if "text/html" in content_type.lower():
        return True
    if body_text.lstrip().lower().startswith("<!doctype html>"):
        return True
    return "Attention Required" in body_text


def _parse_item(item: ET.Element) -> dict | None:
    """One <item> into the tool's item shape, or None if unreadable.

    A row with neither a title nor a link carries nothing an agent can act
    on, so it is dropped the same way an HDX dataset with no name is
    dropped: one bad row skips itself, never the whole page.
    """
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    if not title and not link:
        return None
    categories = [c.text.strip() for c in item.findall("category") if c.text and c.text.strip()]
    creator = (item.findtext("dc:creator", namespaces=_DC_NS) or "").strip()
    return {
        "title": title,
        "link": link,
        "pub_date": _parse_pub_date(item.findtext("pubDate") or ""),
        "creator": creator or None,
        "categories": categories,
        "guid": (item.findtext("guid") or "").strip(),
        "description": _strip_html(item.findtext("description") or ""),
    }


def _parse_feed(body: bytes) -> tuple[str, str, list[dict], int]:
    """Parse an RSS 2.0 body. Raises ET.ParseError or ValueError on drift."""
    root = ET.fromstring(body)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("RSS body has no <channel> element")
    feed_title = (channel.findtext("title") or "").strip()
    feed_link = (channel.findtext("link") or "").strip()
    raw_items = channel.findall("item")
    parsed = [_parse_item(item) for item in raw_items]
    items = [item for item in parsed if item is not None]
    skipped = len(raw_items) - len(items)
    return feed_title, feed_link, items, skipped


async def _fetch_feed_page(url: str) -> tuple[str, str, list[dict], int]:
    """GET one feed page. Raises GazetteBlockedError on a Cloudflare block,
    or the underlying exception on any other transport or parse failure."""
    response = await fetch_with_retry(CLIENT, "GET", url)
    content_type = response.headers.get("content-type", "")
    if _looks_like_cloudflare_block(content_type, response.text):
        raise GazetteBlockedError(response.status_code, content_type)
    response.raise_for_status()
    return _parse_feed(response.content)


def _result(
    page: int,
    url: str,
    feed_title: str | None,
    feed_link: str | None,
    items: list[dict],
    data_status: str,
    caveats: list[str] | None = None,
) -> dict:
    return {
        "page": page,
        "items": items,
        "item_count": len(items),
        "feed_title": feed_title,
        "feed_link": feed_link,
        "source": GAZETTE_SOURCE,
        "source_url": url,
        "data_status": data_status,
        "upstream_error": False,
        "validation_error": False,
        "caveats": caveats or [],
        "data_retrieved_at": _now().isoformat(),
        "license": GAZETTE_LICENSE,
    }


@mcp.tool(
    title="Official Gazette RSS feed",
    tags={"gazette", "government", "laws", "philippines"},
    annotations={
        "title": "Official Gazette RSS feed",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_official_gazette_feed(page: int = 1) -> dict:
    """Latest issuances from the Official Gazette of the Republic of the Philippines.

    Reads the government's own RSS feed of proclamations, memorandum
    circulars, and other issuances, ten items per page, newest first. Examples:

      get_official_gazette_feed()          # page 1, the newest 10 issuances
      get_official_gazette_feed(page=2)    # the next 10 issuances

    On failure: a page outside 1 to 50 returns data_status "invalid_request"
    with validation_error true and no fetch attempted. A Cloudflare block
    page, the only other response this host sends on a bad call, returns
    data_status "unavailable" with upstream_error true and the real status
    and content type in caveats. An item with neither a title nor a link is
    dropped and counted in caveats. A page 1 response that parses as valid
    RSS but keeps zero issuances, whether the feed sent none or every item
    was dropped, is drift and returns data_status "indeterminate". A page
    above 1 with zero issuances is a genuine empty page and returns
    data_status "empty".

    Args:
        page: Page of the feed, 1 to 50. Page 1 reads /feed/, a page above 1
              reads /feed/?paged=<page>. Default 1.
    """
    if not isinstance(page, int) or isinstance(page, bool) or not (1 <= page <= MAX_PAGE):
        return failure_result(
            GAZETTE_SOURCE,
            GAZETTE_FEED_URL,
            f"page must be an integer from 1 to {MAX_PAGE}, got {page!r}.",
            license=GAZETTE_LICENSE,
            validation_error=True,
            page=page,
            items=[],
            item_count=0,
            feed_title=None,
            feed_link=None,
        )

    ckey = cache_key({"tool": "gazette", "page": page})
    cache = CACHES["gazette_feed"]
    if ckey in cache:
        return cache[ckey]
    async with _page_lock(ckey):
        if ckey in cache:
            return cache[ckey]
        return await _feed_uncached(page, ckey)


async def _feed_uncached(page: int, ckey: str) -> dict:
    cache = CACHES["gazette_feed"]
    url = _feed_url(page)

    try:
        feed_title, feed_link, items, skipped = await _fetch_feed_page(url)
    except GazetteBlockedError as exc:
        log_stderr(f"Official Gazette blocked: {exc}")
        return failure_result(
            GAZETTE_SOURCE,
            url,
            f"Cloudflare blocked this request (status {exc.status_code}, "
            f"content-type {exc.content_type!r}). This is an upstream block, "
            "not a feed with zero issuances.",
            license=GAZETTE_LICENSE,
            page=page,
            items=[],
            item_count=0,
            feed_title=None,
            feed_link=None,
        )
    except (ET.ParseError, ValueError) as exc:
        # A 200 that is not RSS is drift in the feed, not an outage. Labelling
        # it unavailable would let the live drift test skip a schema break.
        log_stderr(f"Official Gazette parse error: {exc}")
        return failure_result(
            GAZETTE_SOURCE,
            url,
            f"Official Gazette body did not parse as RSS ({type(exc).__name__}: {exc}).",
            license=GAZETTE_LICENSE,
            data_status=DATA_STATUS_INDETERMINATE,
            page=page,
            items=[],
            item_count=0,
            feed_title=None,
            feed_link=None,
        )
    except Exception as exc:
        log_stderr(f"Official Gazette fetch error: {exc}")
        return failure_result(
            GAZETTE_SOURCE,
            url,
            f"Official Gazette feed unavailable ({type(exc).__name__}: {exc}).",
            license=GAZETTE_LICENSE,
            page=page,
            items=[],
            item_count=0,
            feed_title=None,
            feed_link=None,
        )

    if not items and page == 1:
        caveat = (
            "Page 1 parsed as valid RSS but kept zero issuances. This "
            "feed always carries recent issuances, so this is drift, not an "
            "absence of posts."
        )
        if skipped:
            caveat += f" All {skipped} <item> element(s) had neither a title nor a link."
        return failure_result(
            GAZETTE_SOURCE,
            url,
            caveat,
            license=GAZETTE_LICENSE,
            data_status=DATA_STATUS_INDETERMINATE,
            page=page,
            items=[],
            item_count=0,
            feed_title=feed_title,
            feed_link=feed_link,
        )

    caveats = []
    if skipped:
        caveats.append(f"Skipped {skipped} item(s) with neither a title nor a link.")
    data_status = DATA_STATUS_EMPTY if not items else DATA_STATUS_SUCCESS
    result = _result(page, url, feed_title, feed_link, items, data_status, caveats)
    cache[ckey] = result
    return result
