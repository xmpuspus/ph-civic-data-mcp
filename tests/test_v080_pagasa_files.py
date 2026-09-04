"""Offline tests for the PAGASA public files source (v0.8.0).

The fixture below is the real nginx autoindex body PAGASA served for
/tamss/weather/weather_advisory/ during the round-3 source probe
(2026-09-03), saved offline so these tests never touch the live host.
"""

from __future__ import annotations

import httpx
import pytest

from ph_civic_data_mcp.sources import pagasa_files as pagasa_files_module
from ph_civic_data_mcp.utils.cache import CACHES

WEATHER_ADVISORY_HTML = """<html>
<head><title>Index of /tamss/weather/weather_advisory/</title></head>
<body bgcolor="white">
<h1>Index of /tamss/weather/weather_advisory/</h1><hr><pre><a href="../">../</a>
<a href="Advisory%231.pdf">Advisory#1.pdf</a>                                     22-Aug-2026 14:49    240K
<a href="Advisory%2310.pdf">Advisory#10.pdf</a>                                    24-Aug-2026 20:57    272K
<a href="Advisory%2349.pdf">Advisory#49.pdf</a>                                    03-Sep-2026 14:44    560K
<a href="Advisory%2350.pdf">Advisory#50.pdf</a>                                    03-Sep-2026 20:41    282K
<a href="Forecast.pdf">Forecast.pdf</a>                                       27-Aug-2026 20:40    160K
</pre><hr></body>
</html>
"""

STORMSURGE_HTML = """<html>
<head><title>Index of /tamss/weather/stormsurge/</title></head>
<body bgcolor="white">
<h1>Index of /tamss/weather/stormsurge/</h1><hr><pre><a href="../">../</a>
<a href="SSW%231.pdf">SSW#1.pdf</a>                                          01-Dec-2019 10:00    120K
<a href="SSW%237.pdf">SSW#7.pdf</a>                                          02-Dec-2019 09:00    118K
</pre><hr></body>
</html>
"""

ZERO_ROWS_HTML = """<html>
<head><title>Index of /tamss/weather/weather_advisory/</title></head>
<body bgcolor="white">
<h1>Index of /tamss/weather/weather_advisory/</h1><hr><pre><a href="../">../</a>
</pre><hr></body>
</html>
"""

# A server with `autoindex_exact_size on` prints raw byte counts instead of
# rounding to K/M/G, so size_bytes must then read as exact, no warning added.
EXACT_SIZE_HTML = """<html>
<head><title>Index of /tamss/weather/weather_advisory/</title></head>
<body bgcolor="white">
<h1>Index of /tamss/weather/weather_advisory/</h1><hr><pre><a href="../">../</a>
<a href="Advisory%2350.pdf">Advisory#50.pdf</a>                                    03-Sep-2026 20:41    288709
</pre><hr></body>
</html>
"""


def _response(
    status: int, text: str, url: str = pagasa_files_module._folder_url("weather_advisory")
):
    return httpx.Response(status, text=text, request=httpx.Request("GET", url))


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["pagasa_files"].clear()
    yield
    CACHES["pagasa_files"].clear()


@pytest.mark.asyncio
async def test_success_path_sorts_newest_file_first(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        return _response(200, WEATHER_ADVISORY_HTML)

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files()

    assert result["data_status"] == "success"
    assert result["file_count"] == 5
    assert result["files"][0]["name"] == "Advisory#50.pdf"
    assert result["files"][0]["last_modified"] == "2026-09-03T20:41:00+08:00"
    assert result["files"][0]["url"].endswith("Advisory%2350.pdf")
    assert result["files"][0]["size_bytes"] == 282 * 1024
    assert result["latest_name"] == "Advisory#50.pdf"
    assert any("approximate" in c for c in result["caveats"]), result["caveats"]


@pytest.mark.asyncio
async def test_limit_cuts_the_returned_list_but_not_file_count(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        return _response(200, WEATHER_ADVISORY_HTML)

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files(limit=2)

    assert result["data_status"] == "success"
    assert len(result["files"]) == 2
    assert result["file_count"] == 5
    assert [f["name"] for f in result["files"]] == ["Advisory#50.pdf", "Advisory#49.pdf"]


@pytest.mark.asyncio
async def test_stormsurge_always_carries_the_stale_warning(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        return _response(200, STORMSURGE_HTML, url=pagasa_files_module._folder_url("stormsurge"))

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files(kind="stormsurge")

    assert result["data_status"] == "success"
    assert result["upstream_error"] is False
    assert any("2019-12-02" in c for c in result["caveats"]), result["caveats"]


@pytest.mark.asyncio
async def test_exact_byte_sizes_do_not_trigger_the_approximate_warning(monkeypatch):
    """A server that prints raw byte counts, not K/M/G, gives an exact
    size_bytes, so no rounding warning belongs in caveats."""

    async def _fake(client, method, url, **kwargs):
        return _response(200, EXACT_SIZE_HTML)

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files()

    assert result["data_status"] == "success"
    assert result["files"][0]["size_bytes"] == 288709
    assert not any("approximate" in c for c in result["caveats"]), result["caveats"]


@pytest.mark.asyncio
async def test_transport_failure_is_unavailable_and_not_cached(monkeypatch):
    async def _boom(client, method, url, **kwargs):
        raise httpx.ConnectError("pubfiles down")

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _boom)

    result = await pagasa_files_module.list_pagasa_advisory_files()

    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert any("ConnectError" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["pagasa_files"]) == 0


@pytest.mark.asyncio
async def test_a_404_is_indeterminate_not_an_empty_list(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        return _response(404, "<html><body>Not Found</body></html>")

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files()

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert result["files"] == []
    assert len(CACHES["pagasa_files"]) == 0


@pytest.mark.asyncio
async def test_zero_anchor_rows_is_indeterminate_not_cached_empty(monkeypatch):
    """A folder this tool serves always has files, so zero parsed rows on a
    real "Index of" page is drift, never a genuine empty folder."""

    async def _fake(client, method, url, **kwargs):
        return _response(200, ZERO_ROWS_HTML)

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files()

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert result["files"] == []
    assert len(CACHES["pagasa_files"]) == 0


@pytest.mark.asyncio
async def test_an_unknown_kind_is_a_validation_error():
    result = await pagasa_files_module.list_pagasa_advisory_files(kind="typhoon_tracks")

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["files"] == []


@pytest.mark.asyncio
async def test_a_limit_out_of_range_is_a_validation_error():
    result = await pagasa_files_module.list_pagasa_advisory_files(limit=0)

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False

    result = await pagasa_files_module.list_pagasa_advisory_files(limit=101)

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
