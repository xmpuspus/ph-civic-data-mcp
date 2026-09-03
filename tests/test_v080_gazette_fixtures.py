"""Offline tests for the Official Gazette RSS feed.

The fixture is the real `/feed/` body the round-3 probe saved
(tmp/ulw-20260903/r3-fixtures/officialgazette.xml), 10 items. Every other
path on this host, and a HEAD request on any path including `/feed/`,
returns a Cloudflare "Attention Required" block page, so the module must
send only a GET to `/feed/` or `/feed/?paged=<page>`.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import gazette as gazette_module
from ph_civic_data_mcp.utils.cache import CACHES

FEED_XML = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"
\txmlns:content="http://purl.org/rss/1.0/modules/content/"
\txmlns:wfw="http://wellformedweb.org/CommentAPI/"
\txmlns:dc="http://purl.org/dc/elements/1.1/"
\txmlns:atom="http://www.w3.org/2005/Atom"
\txmlns:sy="http://purl.org/rss/1.0/modules/syndication/"
\txmlns:slash="http://purl.org/rss/1.0/modules/slash/"
\t>

<channel>
\t<title>Official Gazette of the Republic of the Philippines</title>
\t<atom:link href="https://www.officialgazette.gov.ph/feed/" rel="self" type="application/rss+xml" />
\t<link>https://www.officialgazette.gov.ph</link>
\t<description>The Official Gazette is the official journal of the Republic of the Philippines.</description>
\t<lastBuildDate>Thu, 03 Sep 2026 22:28:10 +0000</lastBuildDate>
\t<language>en-US</language>
\t<generator>https://wordpress.org/?v=7.1</generator>
\t<item>
\t\t<title>Memorandum Circular No. 134 s. 2026</title>
\t\t<link>https://www.officialgazette.gov.ph/2026/09/04/memorandum-circular-no-134-s-2026/</link>
\t\t<dc:creator><![CDATA[Kelvin Ulangca]]></dc:creator>
\t\t<pubDate>Thu, 03 Sep 2026 22:00:50 +0000</pubDate>
\t\t<category><![CDATA[Executive Issuances]]></category>
\t\t<category><![CDATA[Laws and Issuances]]></category>
\t\t<guid isPermaLink="false">https://www.officialgazette.gov.ph/?p=466744</guid>
\t\t<description><![CDATA[Office of the President Of the Philippines Malacañang MEMORANDUM CIRCULAR NO. 134 &#160;]]></description>
\t</item>
\t<item>
\t\t<title>Memorandum Circular No. 133 s. 2026</title>
\t\t<link>https://www.officialgazette.gov.ph/2026/08/31/memorandum-circular-no-133-s-2026/</link>
\t\t<dc:creator><![CDATA[Joy Dimaapi]]></dc:creator>
\t\t<pubDate>Mon, 31 Aug 2026 10:25:50 +0000</pubDate>
\t\t<category><![CDATA[Executive Issuances]]></category>
\t\t<guid isPermaLink="false">https://www.officialgazette.gov.ph/?p=466712</guid>
\t\t<description><![CDATA[Office of the President Of the Philippines Malacañang MEMORANDUM CIRCULAR NO. 133 &#160;]]></description>
\t</item>
\t<item>
\t\t<title>Proclamation No. 1413 s. 2026</title>
\t\t<link>https://www.officialgazette.gov.ph/2026/08/28/proclamation-no-1413-s-2026/</link>
\t\t<dc:creator><![CDATA[Manuel Zapata III]]></dc:creator>
\t\t<pubDate>Fri, 28 Aug 2026 12:33:31 +0000</pubDate>
\t\t<category><![CDATA[Proclamations]]></category>
\t\t<category><![CDATA[Region 1 holidays]]></category>
\t\t<guid isPermaLink="false">https://www.officialgazette.gov.ph/?p=466735</guid>
\t\t<description><![CDATA[DECLARING WEDNESDAY, 16 SEPTEMBER 2026 AS A SPECIAL DAY IN SAN ILDEFONSO]]></description>
\t</item>
\t<item>
\t\t<title>Proclamation No. 1412 s. 2026</title>
\t\t<link>https://www.officialgazette.gov.ph/2026/08/28/proclamation-no-1412-s-2026/</link>
\t\t<dc:creator><![CDATA[Manuel Zapata III]]></dc:creator>
\t\t<pubDate>Fri, 28 Aug 2026 12:32:31 +0000</pubDate>
\t\t<category><![CDATA[Proclamations]]></category>
\t\t<guid isPermaLink="false">https://www.officialgazette.gov.ph/?p=466733</guid>
\t\t<description><![CDATA[DECLARING MONDAY, 14 SEPTEMBER 2026 A SPECIAL DAY IN CLAVER]]></description>
\t</item>
\t<item>
\t\t<title>Proclamation No. 1411 s. 2026</title>
\t\t<link>https://www.officialgazette.gov.ph/2026/08/28/proclamation-no-1411-s-2026/</link>
\t\t<dc:creator><![CDATA[Manuel Zapata III]]></dc:creator>
\t\t<pubDate>Fri, 28 Aug 2026 12:31:31 +0000</pubDate>
\t\t<category><![CDATA[Proclamations]]></category>
\t\t<guid isPermaLink="false">https://www.officialgazette.gov.ph/?p=466731</guid>
\t\t<description><![CDATA[DECLARING THURSDAY, 10 SEPTEMBER 2026 A SPECIAL DAY IN MACABEBE]]></description>
\t</item>
\t<item>
\t\t<title>Proclamation No. 1410 s. 2026</title>
\t\t<link>https://www.officialgazette.gov.ph/2026/08/28/proclamation-no-1410-s-2026/</link>
\t\t<dc:creator><![CDATA[Manuel Zapata III]]></dc:creator>
\t\t<pubDate>Fri, 28 Aug 2026 12:30:31 +0000</pubDate>
\t\t<category><![CDATA[Proclamations]]></category>
\t\t<guid isPermaLink="false">https://www.officialgazette.gov.ph/?p=466714</guid>
\t\t<description><![CDATA[DECLARING SATURDAY, 05 SEPTEMBER 2026 A SPECIAL DAY IN GENERAL SANTOS]]></description>
\t</item>
\t<item>
\t\t<title>DOE Department Circular No. DC2026-08-0019</title>
\t\t<link>https://www.officialgazette.gov.ph/2026/08/28/doe-department-circular-no-dc2026-08-0019/</link>
\t\t<dc:creator><![CDATA[Patrisha Combalicer]]></dc:creator>
\t\t<pubDate>Fri, 28 Aug 2026 09:00:42 +0000</pubDate>
\t\t<category><![CDATA[Other Issuances]]></category>
\t\t<guid isPermaLink="false">https://www.officialgazette.gov.ph/?p=466707</guid>
\t\t<description><![CDATA[DEPARTMENT CIRCULAR NO. DC2026-08-0019 RECONSTITUTION OF THE REVIEW AND EVALUATION COMMITTEE]]></description>
\t</item>
\t<item>
\t\t<title>Memorandum Circular No. 132 s. 2026</title>
\t\t<link>https://www.officialgazette.gov.ph/2026/08/28/memorandum-circular-no-132-s-2026/</link>
\t\t<dc:creator><![CDATA[Manuel Zapata III]]></dc:creator>
\t\t<pubDate>Fri, 28 Aug 2026 02:00:50 +0000</pubDate>
\t\t<category><![CDATA[Memorandum Circulars]]></category>
\t\t<guid isPermaLink="false">https://www.officialgazette.gov.ph/?p=466704</guid>
\t\t<description><![CDATA[Office of the President Of the Philippines Malacañang MEMORANDUM CIRCULAR NO. 132 &#160;]]></description>
\t</item>
\t<item>
\t\t<title>Proclamation No. 1409 s. 2026</title>
\t\t<link>https://www.officialgazette.gov.ph/2026/08/26/proclamation-no-1409-s-2026/</link>
\t\t<dc:creator><![CDATA[Patrisha Combalicer]]></dc:creator>
\t\t<pubDate>Wed, 26 Aug 2026 12:30:31 +0000</pubDate>
\t\t<category><![CDATA[Proclamations]]></category>
\t\t<guid isPermaLink="false">https://www.officialgazette.gov.ph/?p=466701</guid>
\t\t<description><![CDATA[PROCLAMATION NO. 1409 DECLARING MONDAY, 7 SEPTEMBER 2026 A SPECIAL DAY IN STO. TOMAS]]></description>
\t</item>
\t<item>
\t\t<title>Proclamation No. 1408 s. 2026</title>
\t\t<link>https://www.officialgazette.gov.ph/2026/08/26/proclamation-no-1408-s-2026/</link>
\t\t<dc:creator><![CDATA[Patrisha Combalicer]]></dc:creator>
\t\t<pubDate>Wed, 26 Aug 2026 12:25:31 +0000</pubDate>
\t\t<category><![CDATA[Proclamations]]></category>
\t\t<guid isPermaLink="false">https://www.officialgazette.gov.ph/?p=466698</guid>
\t\t<description><![CDATA[PROCLAMATION NO. 1408 DECLARING THURSDAY, 3 SEPTEMBER 2026 A SPECIAL DAY IN CALACA]]></description>
\t</item>
</channel></rss>
"""

PAGE2_XML = FEED_XML.replace("Memorandum Circular No. 134 s. 2026", "Proclamation No. 1407 s. 2026")

CLOUDFLARE_BLOCK_HTML = (
    "<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title>"
    "</head><body>Sorry, you have been blocked</body></html>"
)


def _install_fake_fetch(
    monkeypatch, *, body="", content_type="application/rss+xml", status=200, exc=None
):
    seen_methods: list[str] = []

    async def _fake(client, method, url, **kwargs):
        seen_methods.append(method)
        return httpx.Response(
            status,
            content=body.encode("utf-8"),
            headers={"content-type": content_type},
            request=httpx.Request(method, url),
        )

    async def _fake_raise(client, method, url, **kwargs):
        seen_methods.append(method)
        raise exc

    monkeypatch.setattr(gazette_module, "fetch_with_retry", _fake_raise if exc else _fake)
    return seen_methods


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["gazette_feed"].clear()
    yield


@pytest.mark.asyncio
async def test_success_returns_ten_items_with_iso_dates(monkeypatch):
    _install_fake_fetch(monkeypatch, body=FEED_XML)

    result = await gazette_module.get_official_gazette_feed()

    assert result["data_status"] == "success"
    assert result["item_count"] == 10
    assert len(result["items"]) == 10
    first = result["items"][0]
    assert first["title"] == "Memorandum Circular No. 134 s. 2026"
    assert first["pub_date"] == "2026-09-03T22:00:50+00:00"
    assert first["creator"] == "Kelvin Ulangca"
    assert "Executive Issuances" in first["categories"]
    assert first["guid"] == "https://www.officialgazette.gov.ph/?p=466744"
    assert result["feed_title"] == "Official Gazette of the Republic of the Philippines"


@pytest.mark.asyncio
async def test_page_above_one_requests_the_paged_url(monkeypatch):
    seen: list[str] = []

    async def _fake(client, method, url, **kwargs):
        seen.append(url)
        return httpx.Response(
            200,
            content=PAGE2_XML.encode("utf-8"),
            headers={"content-type": "application/rss+xml"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(gazette_module, "fetch_with_retry", _fake)

    result = await gazette_module.get_official_gazette_feed(page=2)

    assert result["data_status"] == "success"
    assert seen == ["https://www.officialgazette.gov.ph/feed/?paged=2"]
    assert result["items"][0]["title"] == "Proclamation No. 1407 s. 2026"


@pytest.mark.asyncio
async def test_cloudflare_html_block_is_unavailable(monkeypatch):
    _install_fake_fetch(
        monkeypatch, body=CLOUDFLARE_BLOCK_HTML, content_type="text/html", status=403
    )

    result = await gazette_module.get_official_gazette_feed()

    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert result["items"] == []
    assert any("403" in c and "text/html" in c for c in result["caveats"]), result["caveats"]
    assert not CACHES["gazette_feed"], "a Cloudflare block must never be cached"


@pytest.mark.asyncio
async def test_transport_failure_is_unavailable(monkeypatch):
    _install_fake_fetch(monkeypatch, exc=httpx.ConnectError("no route"))

    result = await gazette_module.get_official_gazette_feed()

    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert result["items"] == []
    assert not CACHES["gazette_feed"]


@pytest.mark.asyncio
async def test_zero_items_on_page_one_is_indeterminate_not_cached(monkeypatch):
    empty_feed = (
        "<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'>"
        "<channel><title>Official Gazette</title><link>https://www.officialgazette.gov.ph</link>"
        "</channel></rss>"
    )
    _install_fake_fetch(monkeypatch, body=empty_feed)

    result = await gazette_module.get_official_gazette_feed(page=1)

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert result["items"] == []
    assert not CACHES["gazette_feed"], "zero items on page 1 is drift, never cached"


@pytest.mark.asyncio
async def test_zero_items_on_page_two_is_a_genuine_empty(monkeypatch):
    empty_feed = (
        "<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'>"
        "<channel><title>Official Gazette</title><link>https://www.officialgazette.gov.ph</link>"
        "</channel></rss>"
    )
    _install_fake_fetch(monkeypatch, body=empty_feed)

    result = await gazette_module.get_official_gazette_feed(page=2)

    assert result["data_status"] == "empty"
    assert result["upstream_error"] is False
    assert result["items"] == []
    assert CACHES["gazette_feed"], "a genuine empty page above 1 may cache"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_page", [0, -1, 51, 1000])
async def test_page_outside_1_to_50_is_invalid_request(monkeypatch, bad_page):
    async def _must_not_fetch(client, method, url, **kwargs):
        raise AssertionError("fetch must not be attempted for a bad page")

    monkeypatch.setattr(gazette_module, "fetch_with_retry", _must_not_fetch)

    result = await gazette_module.get_official_gazette_feed(page=bad_page)

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["items"] == []


@pytest.mark.asyncio
async def test_the_module_never_sends_a_head_request(monkeypatch):
    seen = _install_fake_fetch(monkeypatch, body=FEED_XML)

    result = await gazette_module.get_official_gazette_feed()

    assert seen == ["GET"], f"expected only GET, saw {seen}"
    assert result["data_status"] == "success"


@pytest.mark.asyncio
async def test_description_is_stripped_of_html_and_capped_at_500(monkeypatch):
    long_desc = "<p>" + ("x" * 600) + "</p>"
    feed = (
        "<?xml version='1.0' encoding='UTF-8'?><rss version='2.0'>"
        "<channel><title>Official Gazette</title><link>https://www.officialgazette.gov.ph</link>"
        "<item><title>Test</title><link>https://example.test/1</link>"
        f"<description><![CDATA[{long_desc}]]></description>"
        "<guid>https://example.test/?p=1</guid></item>"
        "</channel></rss>"
    )
    _install_fake_fetch(monkeypatch, body=feed)

    result = await gazette_module.get_official_gazette_feed()

    description = result["items"][0]["description"]
    assert "<p>" not in description
    assert len(description) == 500
