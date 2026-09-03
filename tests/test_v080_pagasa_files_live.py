"""Live test for PAGASA public files (v0.8.0).

Runs against the real pubfiles.pagasa.dost.gov.ph directory listings. A
positively identified outage skips. A folder that stops listing PDF files,
or a stormsurge folder that drops its stale warning, is drift and must fail.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.pagasa_files import list_pagasa_advisory_files
from tests.live_helpers import skip_if_outage

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_weather_advisory_folder_lists_real_pdf_files():
    result = await list_pagasa_advisory_files(kind="weather_advisory", limit=5)
    skip_if_outage(result, "PAGASA weather_advisory files")

    assert result["data_status"] == "success"
    assert result["file_count"] > 0
    assert result["files"], result["caveats"]
    first = result["files"][0]
    assert first["name"].endswith(".pdf")
    assert first["url"].startswith(
        "https://pubfiles.pagasa.dost.gov.ph/tamss/weather/weather_advisory/"
    )
    assert first["last_modified"].endswith("+08:00")


@pytest.mark.asyncio
async def test_bulletin_folder_lists_files_with_no_forced_tcb_prefix():
    """The bulletin folder can hold a name like IWS#2_pilandok.pdf beside the
    TCB#<n> files, so this checks real names come back, not a fixed prefix."""
    result = await list_pagasa_advisory_files(kind="bulletin", limit=10)
    skip_if_outage(result, "PAGASA bulletin files")

    assert result["data_status"] == "success"
    assert result["file_count"] > 0
    assert all(f["name"].endswith(".pdf") for f in result["files"])


@pytest.mark.asyncio
async def test_stormsurge_folder_always_warns_it_is_stale():
    result = await list_pagasa_advisory_files(kind="stormsurge")
    skip_if_outage(result, "PAGASA stormsurge files")

    assert result["data_status"] == "success"
    assert any("2019-12-02" in c for c in result["caveats"]), result["caveats"]
