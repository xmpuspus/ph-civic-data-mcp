"""Live contract for the Official Gazette RSS feed.

Runs against the real host. A positively identified outage or Cloudflare
block skips. Drift, such as a wrong item count or a broken date, fails,
because that is the drift this file exists to catch.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from ph_civic_data_mcp.sources.gazette import get_official_gazette_feed
from ph_civic_data_mcp.utils.cache import CACHES
from tests.live_helpers import skip_if_outage

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _clear_cache():
    # An offline fixture test in another file can cache a page-1 stub under
    # this same key. Clear it so a live test always hits the real host, not
    # a synthetic result another test file left behind.
    CACHES["gazette_feed"].clear()
    yield


@pytest.mark.asyncio
async def test_page_one_returns_ten_recent_items() -> None:
    result = await get_official_gazette_feed()
    skip_if_outage(result, "Official Gazette")

    assert result["data_status"] == "success", result.get("caveats")
    assert result["upstream_error"] is False
    assert result["source_url"] == "https://www.officialgazette.gov.ph/feed/"
    assert result["item_count"] == 10
    assert len(result["items"]) == 10
    assert "Official Gazette" in (result["feed_title"] or "")

    first = result["items"][0]
    assert first["title"]
    assert first["link"].startswith("https://www.officialgazette.gov.ph/")
    assert first["guid"]
    # A live pubDate must parse to a real, timezone-aware ISO 8601 timestamp.
    parsed = datetime.fromisoformat(first["pub_date"])
    assert parsed.tzinfo is not None


@pytest.mark.asyncio
async def test_page_two_returns_different_items_than_page_one() -> None:
    page1 = await get_official_gazette_feed(page=1)
    skip_if_outage(page1, "Official Gazette page 1")
    page2 = await get_official_gazette_feed(page=2)
    skip_if_outage(page2, "Official Gazette page 2")

    assert page1["data_status"] == "success", page1.get("caveats")
    assert page2["data_status"] in ("success", "empty"), page2.get("caveats")
    page1_guids = {item["guid"] for item in page1["items"]}
    page2_guids = {item["guid"] for item in page2["items"]}
    assert page1_guids.isdisjoint(page2_guids), "paging must not repeat page 1's items"
