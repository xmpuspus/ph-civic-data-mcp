"""Adversarial tests for the PHIVOLCS host allowlist (v0.6.1).

`PHIVOLCS_CLIENT` skips certificate checks because PHIVOLCS serves a broken
chain. Before v0.6.1 the WOVODAT volcano path resolved upstream hrefs with a
bare `urljoin` and fetched them on that client with redirects on, so a page
that linked off-host, or a redirect, could steer a TLS-blind fetch anywhere.
Every request here is intercepted; nothing touches the network.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import phivolcs as phivolcs_module
from ph_civic_data_mcp.utils.cache import CACHES
from ph_civic_data_mcp.utils.http import PHIVOLCS_CLIENT

WOVODAT = "https://wovodat.phivolcs.dost.gov.ph"

# (url, why it must be refused)
REFUSED = [
    ("https://evil.example/bulletin/activity-mvo?bid=1&lang=en", "other host"),
    ("//evil.example/bulletin/activity-mvo?bid=1", "scheme-relative"),
    ("http://wovodat.phivolcs.dost.gov.ph/bulletin/activity-mvo?bid=1", "http downgrade"),
    ("https://wovodat.phivolcs.dost.gov.ph@evil.example/x", "userinfo hides the real host"),
    ("https://evil@wovodat.phivolcs.dost.gov.ph/x", "userinfo on the right host"),
    ("https://wovodat.phivolcs.dost.gov.ph.evil.example/x", "suffix confusion"),
    ("https://notphivolcs.dost.gov.ph/x", "sibling domain"),
    ("https://127.0.0.1/bulletin/activity-mvo?bid=1", "loopback"),
    ("https://169.254.169.254/latest/meta-data/", "link-local metadata"),
    ("https://10.0.0.8/x", "private network"),
    ("https://[::1]/x", "ipv6 loopback"),
    ("https://localhost:8443/x", "localhost"),
    ("https://wovodat.phivolcs.dost.gov.ph:8443/x", "non-default port"),
    ("ftp://wovodat.phivolcs.dost.gov.ph/x", "other scheme"),
    ("javascript:alert(1)", "not a URL"),
    ("", "empty"),
    ("https://wovodat.phivolcs.dost.gov.ph:notaport/x", "invalid port"),
]

ACCEPTED = [
    f"{WOVODAT}/bulletin/activity-mvo?bid=123&lang=en",
    f"{WOVODAT}:443/bulletin/list-of-bulletin",
    "https://earthquake.phivolcs.dost.gov.ph/2026_Earthquake_Information/x.html",
    "https://phivolcs.dost.gov.ph/index.php",
    f"{WOVODAT}/bulletin/..%2F..%2Factivity-mvo",
]


@pytest.mark.parametrize("url, why", REFUSED, ids=[w for _, w in REFUSED])
def test_allowlist_refuses(url, why):
    assert phivolcs_module._is_phivolcs_url(url) is False, why


@pytest.mark.parametrize("url", ACCEPTED)
def test_allowlist_accepts_https_phivolcs_hosts(url):
    assert phivolcs_module._is_phivolcs_url(url) is True


def test_the_tls_relaxed_client_never_follows_redirects_on_its_own():
    assert PHIVOLCS_CLIENT.follow_redirects is False


# ---------------------------------------------------------------------------
# Redirects: every hop is re-checked before a connection is made
# ---------------------------------------------------------------------------


def _recorder(routes: dict[str, httpx.Response]):
    calls: list[str] = []

    async def _fake(client, method, url, **kwargs):
        calls.append(str(url))
        assert kwargs.get("follow_redirects") is False
        try:
            return routes[str(url)]
        except KeyError:
            raise AssertionError(f"unexpected fetch of {url}") from None

    return _fake, calls


def _redirect(to: str) -> httpx.Response:
    return httpx.Response(302, headers={"location": to}, request=httpx.Request("GET", WOVODAT))


def _page(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, request=httpx.Request("GET", WOVODAT))


@pytest.mark.asyncio
async def test_a_redirect_off_host_is_refused_before_the_second_request(monkeypatch):
    start = f"{WOVODAT}/bulletin/activity-mvo?bid=1&lang=en"
    fake, calls = _recorder({start: _redirect("https://evil.example/steal")})
    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", fake)

    with pytest.raises(phivolcs_module.PhivolcsHostError):
        await phivolcs_module._fetch_phivolcs(start)
    assert calls == [start]


@pytest.mark.asyncio
async def test_a_redirect_to_http_is_refused(monkeypatch):
    start = f"{WOVODAT}/a"
    fake, calls = _recorder({start: _redirect("http://wovodat.phivolcs.dost.gov.ph/a")})
    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", fake)

    with pytest.raises(phivolcs_module.PhivolcsHostError):
        await phivolcs_module._fetch_phivolcs(start)
    assert calls == [start]


@pytest.mark.asyncio
async def test_a_relative_redirect_on_host_is_followed(monkeypatch):
    start = f"{WOVODAT}/bulletin/activity-mvo?bid=1"
    final = f"{WOVODAT}/bulletin/activity-mvo?bid=1&lang=en"
    fake, calls = _recorder(
        {start: _redirect("/bulletin/activity-mvo?bid=1&lang=en"), final: _page("ALERT LEVEL 1")}
    )
    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", fake)

    response = await phivolcs_module._fetch_phivolcs(start)
    assert response.status_code == 200
    assert calls == [start, final]


@pytest.mark.asyncio
async def test_redirect_loops_are_bounded(monkeypatch):
    start = f"{WOVODAT}/loop"
    fake, calls = _recorder({start: _redirect("/loop")})
    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", fake)

    with pytest.raises(phivolcs_module.PhivolcsHostError):
        await phivolcs_module._fetch_phivolcs(start)
    assert len(calls) == phivolcs_module.MAX_REDIRECT_HOPS + 1


# ---------------------------------------------------------------------------
# The WOVODAT list page: off-host links are dropped, never fetched
# ---------------------------------------------------------------------------

LIST_PAGE = """
<html><body>
<a href="/bulletin/activity-mvo?bid=100&lang=en">Mayon Summary</a>
<a href="https://evil.example/bulletin/activity-tvo?bid=200&lang=en">Taal Summary</a>
<a href="//evil.example/bulletin/activity-kvo?bid=300&lang=en">Kanlaon Summary</a>
<a href="https://wovodat.phivolcs.dost.gov.ph@evil.example/bulletin/activity-bvo?bid=400&lang=en">Bulusan Summary</a>
<a href="http://wovodat.phivolcs.dost.gov.ph/bulletin/activity-pvo?bid=500&lang=en">Pinatubo Summary</a>
</body></html>
"""


@pytest.mark.asyncio
async def test_bulletin_list_drops_every_off_host_link(monkeypatch):
    fake, calls = _recorder({phivolcs_module.WOVODAT_BULLETIN_LIST_URL: _page(LIST_PAGE)})
    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", fake)
    CACHES["phivolcs_volcanoes"].clear()

    bulletins = await phivolcs_module._fetch_volcano_bulletin_list()

    assert set(bulletins) == {"Mayon"}
    assert bulletins["Mayon"]["bulletin_url"] == f"{WOVODAT}/bulletin/activity-mvo?bid=100&lang=en"
    assert calls == [phivolcs_module.WOVODAT_BULLETIN_LIST_URL]
    CACHES["phivolcs_volcanoes"].clear()


@pytest.mark.asyncio
async def test_volcano_status_never_fetches_an_off_host_bulletin(monkeypatch):
    mayon = f"{WOVODAT}/bulletin/activity-mvo?bid=100&lang=en"
    fake, calls = _recorder(
        {
            phivolcs_module.WOVODAT_BULLETIN_LIST_URL: _page(LIST_PAGE),
            mayon: _page("MAYON VOLCANO ... ALERT LEVEL 1 (Low-level unrest)"),
        }
    )
    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", fake)
    CACHES["phivolcs_volcanoes"].clear()

    results = await phivolcs_module.get_volcano_status()

    assert isinstance(results, list)
    assert [r["name"] for r in results] == ["Mayon"]
    assert results[0]["alert_level"] == 1
    assert all(c.startswith(WOVODAT) for c in calls), calls
    CACHES["phivolcs_volcanoes"].clear()


@pytest.mark.asyncio
async def test_volcano_alert_refuses_an_off_host_url_without_a_request(monkeypatch):
    fake, calls = _recorder({})
    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", fake)

    level, status, error = await phivolcs_module._fetch_volcano_alert(
        "https://evil.example/bulletin"
    )

    assert (level, status) == (None, None)
    assert error is not None
    assert calls == []


# ---------------------------------------------------------------------------
# A single bulletin timeout never reads as a confirmed normal reading
# (Codex cross-model finding, reported on two passes over the v0.6.1 diff)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bulletin_timeout_carries_upstream_error_not_a_null_reading(monkeypatch):
    mayon = f"{WOVODAT}/bulletin/activity-mvo?bid=100&lang=en"
    taal = f"{WOVODAT}/bulletin/activity-tvo?bid=200&lang=en"
    two_volcano_page = """
    <html><body>
    <a href="/bulletin/activity-mvo?bid=100&lang=en">Mayon Summary</a>
    <a href="/bulletin/activity-tvo?bid=200&lang=en">Taal Summary</a>
    </body></html>
    """

    async def _fake(client, method, url, **kwargs):
        if url == phivolcs_module.WOVODAT_BULLETIN_LIST_URL:
            return _page(two_volcano_page)
        if url == mayon:
            raise httpx.ReadTimeout("slow")
        if url == taal:
            return _page("TAAL VOLCANO ... ALERT LEVEL 1 (Low-level unrest)")
        raise AssertionError(f"unexpected fetch of {url}")

    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", _fake)
    CACHES["phivolcs_volcanoes"].clear()

    results = await phivolcs_module.get_volcano_status()

    by_name = {r["name"]: r for r in results}
    assert by_name["Mayon"]["alert_level"] is None
    assert by_name["Mayon"]["upstream_error"] is True
    assert any("bulletin fetch failed" in c for c in by_name["Mayon"]["caveats"])
    assert by_name["Taal"]["alert_level"] == 1
    assert "upstream_error" not in by_name["Taal"]
    CACHES["phivolcs_volcanoes"].clear()


@pytest.mark.asyncio
async def test_a_single_volcano_query_reports_its_own_bulletin_outage(monkeypatch):
    one_volcano_page = """
    <html><body>
    <a href="/bulletin/activity-mvo?bid=100&lang=en">Mayon Summary</a>
    </body></html>
    """

    async def _fake(client, method, url, **kwargs):
        if url == phivolcs_module.WOVODAT_BULLETIN_LIST_URL:
            return _page(one_volcano_page)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", _fake)
    CACHES["phivolcs_volcanoes"].clear()

    results = await phivolcs_module.get_volcano_status("Mayon")

    assert len(results) == 1
    assert results[0]["alert_level"] is None
    assert results[0]["upstream_error"] is True
    CACHES["phivolcs_volcanoes"].clear()


# ---------------------------------------------------------------------------
# get_location_hierarchy: a PSGC lookup outage never reads as an unknown code
# (Codex cross-model finding on the v0.6.1 diff)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_barangay_lookup_outage_is_upstream_error_not_unknown_code(monkeypatch):
    from ph_civic_data_mcp.sources import psgc as psgc_module

    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("psgc mirror down")

    monkeypatch.setattr(psgc_module, "fetch_with_retry", _boom)
    for name in ("psgc_browse",):
        CACHES[name].clear()

    result = await psgc_module.get_location_hierarchy("133901001")

    assert result["chain"] == []
    assert result["upstream_error"] is True
    assert any("133901001" in c for c in result["caveats"])
    assert len(CACHES["psgc_browse"]) == 0


@pytest.mark.asyncio
async def test_a_code_endpoint_outage_is_upstream_error_not_unknown_code(monkeypatch):
    from ph_civic_data_mcp.sources import psgc as psgc_module

    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("psgc mirror down")

    monkeypatch.setattr(psgc_module, "fetch_with_retry", _boom)
    CACHES["psgc_browse"].clear()

    # A 9-digit code with a non-zero barangay slot tries the barangay
    # endpoint first, so this exercises _fetch_one directly: a code whose
    # barangay slot is zero skips straight to the level-by-level lookup.
    result = await psgc_module.get_location_hierarchy("130000000")

    assert result["chain"] == []
    assert result["upstream_error"] is True
    assert len(CACHES["psgc_browse"]) == 0


@pytest.mark.asyncio
async def test_a_clean_404_at_every_level_is_still_an_unknown_code(monkeypatch):
    """A real miss must not turn into a false outage."""
    from ph_civic_data_mcp.sources import psgc as psgc_module

    async def _not_found(client, method, url, **kwargs):
        return httpx.Response(404, request=httpx.Request(method, url))

    monkeypatch.setattr(psgc_module, "fetch_with_retry", _not_found)
    CACHES["psgc_browse"].clear()

    result = await psgc_module.get_location_hierarchy("999999999")

    assert result["chain"] == []
    assert "upstream_error" not in result
    assert any("No PSGC record found" in c for c in result["caveats"])


# ---------------------------------------------------------------------------
# get_earthquake_bulletin: an agent-supplied URL that redirects off-host
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_earthquake_bulletin_does_not_follow_a_redirect_off_host(monkeypatch):
    start = "https://earthquake.phivolcs.dost.gov.ph/2026_Earthquake_Information/x.html"
    fake, calls = _recorder({start: _redirect("https://evil.example/x.html")})
    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", fake)
    CACHES["phivolcs_bulletins"].clear()

    result = await phivolcs_module.get_earthquake_bulletin(start)

    assert result["upstream_error"] is True
    assert any("PhivolcsHostError" in c for c in result["caveats"])
    assert calls == [start]
    assert len(CACHES["phivolcs_bulletins"]) == 0


@pytest.mark.parametrize("url", [u for u, _ in REFUSED if u.startswith("http")])
@pytest.mark.asyncio
async def test_earthquake_bulletin_refuses_every_adversarial_url(monkeypatch, url):
    fake, calls = _recorder({})
    monkeypatch.setattr(phivolcs_module, "fetch_with_retry", fake)

    result = await phivolcs_module.get_earthquake_bulletin(url)

    assert any("phivolcs.dost.gov.ph" in c for c in result["caveats"])
    assert calls == []
