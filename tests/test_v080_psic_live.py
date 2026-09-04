"""Live PSIC contract for v0.8.0.

Runs against the real PSA PSIC search-results page. A positively identified
outage, or the Cloudflare challenge PSA serves through its "unavailable ("
caveat shape, skips. A missing or reshaped table is real drift, not an
outage, and fails the test, because that is the drift this file exists to
catch.

Row count and the two named codes were read live on 2026-09-04 from
https://psa.gov.ph/classification/psic/search-results (1362 <tr> total, one
of them the header row). PSIC revises on the order of years, so the row-count
bound below stays wide rather than pinning the exact figure.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.psic import search_psic_codes
from tests.live_helpers import skip_if_outage

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_the_table_holds_roughly_the_known_row_count():
    result = await search_psic_codes("0111")
    skip_if_outage(result, "PSIC table")
    assert result["data_status"] == "success", result.get("caveats")
    assert 1300 <= result["total_codes"] <= 1450, result["total_codes"]
    assert result["source"] == "PSIC"
    assert result["license"] == (
        "PSA Philippine Standard Industrial Classification (PSIC), CC BY 4.0"
    )


@pytest.mark.asyncio
async def test_a_code_prefix_query_returns_only_that_prefix():
    result = await search_psic_codes("0111")
    skip_if_outage(result, "PSIC code prefix search")
    assert result["match_count"] > 0
    assert all(m["code"].startswith("0111") for m in result["matches"])


@pytest.mark.asyncio
async def test_a_known_subclass_is_reachable_by_description():
    result = await search_psic_codes("groundnuts")
    skip_if_outage(result, "PSIC description search")
    assert result["data_status"] == "success", result.get("caveats")
    assert any(m["code"] == "01112" for m in result["matches"])


@pytest.mark.asyncio
async def test_an_unmatched_query_is_a_genuine_empty_not_an_outage():
    result = await search_psic_codes("zzz-no-such-industry-exists-anywhere")
    skip_if_outage(result, "PSIC no-match search")
    assert result["data_status"] == "empty", result.get("caveats")
    assert result["matches"] == []
    assert result["upstream_error"] is False
