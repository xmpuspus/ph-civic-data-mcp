"""Live contract for the COMELEC 2025 election results archive.

The archive is frozen (2025-05-16 10:00:09 AM), so a real fetch here is
checking that a fixed public record still answers the same way, not
chasing a moving target. A positively identified outage skips. A changed
tree shape, a moved endpoint, or a different vote count fails, because that
is the drift this file exists to catch.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources import comelec as comelec_module
from ph_civic_data_mcp.sources.comelec import browse_election_results, get_election_return
from tests.live_helpers import skip_if_outage

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_the_region_list_still_has_twenty_regions() -> None:
    result = await browse_election_results()
    skip_if_outage(result, "COMELEC region list")

    assert result["data_status"] == "success"
    assert result["level"] == "root"
    assert result["child_count"] == 20, result["children"]
    for child in result["children"]:
        assert child["code"]
        # Every code this tool hands back must be a code the tool itself
        # accepts on a follow-up call. NCR and CALABARZON carry a letter in
        # the tail ("R0NCR00", "R04A000"), not just digits.
        assert comelec_module._valid_browse_code(child["code"]), child


@pytest.mark.asyncio
async def test_ncr_browses_by_its_letter_coded_region_code() -> None:
    """NCR's code is 'R0NCR00', not the digits-only shape a narrower regex
    would require. A regression here means the region regex broke again."""
    result = await browse_election_results(code="R0NCR00")
    skip_if_outage(result, "COMELEC NCR provinces")

    assert result["data_status"] == "success"
    assert result["level"] == "region"
    assert result["child_count"] > 0


@pytest.mark.asyncio
async def test_the_fixture_precinct_still_returns_the_frozen_tally() -> None:
    result = await get_election_return("28010001")
    skip_if_outage(result, "COMELEC election return")

    assert result["data_status"] == "success"
    assert result["precinct_code"] == "28010001"
    assert result["data_frozen_at"] == "2025-05-16T10:00:09"
    assert result["information"]["machine_id"] == "28010001"
    assert result["information"]["voting_center"] == "POBLACION 1, ADAMS, ILOCOS NORTE"

    senator = next(c for c in result["national_contests"] if c["contest_code"] == "00399000")
    top_candidate = senator["candidates"][0]
    assert top_candidate["name"] == "1. ABALOS, BENHUR (PFP)"
    assert top_candidate["votes"] == 179
