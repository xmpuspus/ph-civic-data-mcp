"""Offline tests for the PAGASA public files source (v0.8.0).

The fixture below is the real nginx autoindex body PAGASA served for
/tamss/weather/weather_advisory/ during the round-3 source probe
(2026-09-03), saved offline so these tests never touch the live host.
"""

from __future__ import annotations

import asyncio

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


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_limit", ["5", 7.5, True, None])
async def test_a_wrong_typed_limit_is_a_validation_error_not_a_crash(bad_limit):
    # Final Codex pass on v0.8.0: a string limit reached the range check and
    # raised TypeError instead of returning invalid_request.
    result = await pagasa_files_module.list_pagasa_advisory_files("bulletin", bad_limit)

    assert result["data_status"] == "invalid_request"
    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["files"] == []


# --- Finding 1: a parser bug after a 200 body is "indeterminate", never
# "unavailable", and _parse_size never raises. ---------------------------

_HUGE_SIZE_TOKEN = "1" * 400
HUGE_SIZE_HTML = f"""<html>
<head><title>Index of /tamss/weather/weather_advisory/</title></head>
<body bgcolor="white">
<h1>Index of /tamss/weather/weather_advisory/</h1><hr><pre><a href="../">../</a>
<a href="Advisory%2399.pdf">Advisory#99.pdf</a>                                    03-Sep-2026 20:41    {_HUGE_SIZE_TOKEN}
</pre><hr></body>
</html>
"""


def test_parse_size_returns_none_instead_of_raising_on_a_huge_token():
    size_bytes, rounded = pagasa_files_module._parse_size(_HUGE_SIZE_TOKEN)

    assert size_bytes is None
    assert rounded is False


@pytest.mark.asyncio
async def test_a_5xx_status_stays_unavailable_not_indeterminate(monkeypatch):
    """A non-2xx status is a transport failure, not a parse-time bug, so it
    must stay "unavailable" even though the body carries real HTML."""

    async def _fake(client, method, url, **kwargs):
        return _response(503, "<html><body>Service Unavailable</body></html>")

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files()

    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert len(CACHES["pagasa_files"]) == 0


@pytest.mark.asyncio
async def test_a_huge_size_token_does_not_crash_the_whole_row(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        return _response(200, HUGE_SIZE_HTML)

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files()

    assert result["data_status"] == "success"
    assert result["files"][0]["size_bytes"] is None


@pytest.mark.asyncio
async def test_a_parser_bug_after_a_200_body_is_indeterminate_not_unavailable(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        return _response(200, WEATHER_ADVISORY_HTML)

    def _broken_parse(html, folder_url):
        raise AttributeError("boom")

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)
    monkeypatch.setattr(pagasa_files_module, "_parse_autoindex", _broken_parse)

    result = await pagasa_files_module.list_pagasa_advisory_files()

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert any("AttributeError" in c and "boom" in c for c in result["caveats"]), result["caveats"]
    assert len(CACHES["pagasa_files"]) == 0


# --- Finding 2: concurrent cold calls for one kind are single-flighted. --


@pytest.mark.asyncio
async def test_twenty_concurrent_cold_calls_reach_the_fetch_once(monkeypatch):
    call_count = 0

    async def _fake(client, method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return _response(200, WEATHER_ADVISORY_HTML)

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    results = await asyncio.gather(
        *(pagasa_files_module.list_pagasa_advisory_files() for _ in range(20))
    )

    assert call_count == 1
    assert all(r["data_status"] == "success" for r in results)
    assert all(r["latest_name"] == "Advisory#50.pdf" for r in results)


# --- Finding 3: an index row must resolve to a trusted PAGASA PDF. -------

MIXED_TRUST_HTML = """<html>
<head><title>Index of /tamss/weather/bulletin/</title></head>
<body bgcolor="white">
<h1>Index of /tamss/weather/bulletin/</h1><hr><pre><a href="../">../</a>
<a href="TCB%231_good.pdf">TCB#1_good.pdf</a>                                    01-Sep-2026 10:00    100K
<a href="https://evil.example/payload.pdf">payload.pdf</a>                       01-Sep-2026 10:05    100K
<a href="../../../secret.pdf">secret.pdf</a>                                01-Sep-2026 10:10    100K
<a href="notes.txt">notes.txt</a>                                         01-Sep-2026 10:15    5K
</pre><hr></body>
</html>
"""


@pytest.mark.asyncio
async def test_untrusted_hrefs_are_dropped_and_counted_in_one_caveat(monkeypatch):
    async def _fake(client, method, url, **kwargs):
        return _response(200, MIXED_TRUST_HTML, url=pagasa_files_module._folder_url("bulletin"))

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files(kind="bulletin")

    assert result["data_status"] == "success"
    assert result["file_count"] == 1
    assert result["files"][0]["name"] == "TCB#1_good.pdf"
    assert all("evil.example" not in f["url"] for f in result["files"])
    assert any(
        "3 index rows skipped: not a PDF under the PAGASA folder" in c for c in result["caveats"]
    ), result["caveats"]


# --- Finding 4: the stale warning is derived from the newest file's real
# age, for every kind, not hardcoded to stormsurge. -----------------------

BULLETIN_STALE_HTML = """<html>
<head><title>Index of /tamss/weather/bulletin/</title></head>
<body bgcolor="white">
<h1>Index of /tamss/weather/bulletin/</h1><hr><pre><a href="../">../</a>
<a href="TCB%231_old.pdf">TCB#1_old.pdf</a>                                     15-Jan-2020 08:00    100K
</pre><hr></body>
</html>
"""


def _fresh_row_html(kind: str) -> str:
    date_text = pagasa_files_module._now().strftime("%d-%b-%Y %H:%M")
    return (
        f"<html><head><title>Index of /tamss/weather/{kind}/</title></head>"
        f'<body bgcolor="white"><h1>Index of /tamss/weather/{kind}/</h1><hr>'
        f'<pre><a href="../">../</a>\n'
        f'<a href="Fresh.pdf">Fresh.pdf</a>                                     '
        f"{date_text}    50K\n</pre><hr></body></html>"
    )


@pytest.mark.asyncio
async def test_a_stale_bulletin_folder_gets_the_warning_too(monkeypatch):
    """The rule runs for every kind, not only stormsurge: a bulletin folder
    that has gone quiet for over a year is just as stale a source."""

    async def _fake(client, method, url, **kwargs):
        return _response(200, BULLETIN_STALE_HTML, url=pagasa_files_module._folder_url("bulletin"))

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files(kind="bulletin")

    assert result["data_status"] == "success"
    assert any("2020-01-15" in c and "days ago" in c for c in result["caveats"]), result["caveats"]


@pytest.mark.asyncio
async def test_a_freshly_updated_stormsurge_folder_gets_no_stale_warning(monkeypatch):
    """A folder is flagged stale from its real data, not from its kind: a
    stormsurge folder with a fresh file must carry no stale warning."""

    async def _fake(client, method, url, **kwargs):
        return _response(
            200,
            _fresh_row_html("stormsurge"),
            url=pagasa_files_module._folder_url("stormsurge"),
        )

    monkeypatch.setattr(pagasa_files_module, "fetch_with_retry", _fake)

    result = await pagasa_files_module.list_pagasa_advisory_files(kind="stormsurge")

    assert result["data_status"] == "success"
    assert not any("days ago" in c for c in result["caveats"]), result["caveats"]
