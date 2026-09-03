"""Live population contract for v0.6.1.

Runs against the real PSA OpenSTAT catalog and the real PSGC mirror. A
positively identified outage skips. A changed schema, a moved folder, a wrong
geography, a wrong vintage label, or a hidden failure fails, because that is
the drift this file exists to catch. Before v0.6.1 no live test asserted on a
population figure, so the weekly run stayed green while the tool returned
nothing for weeks.

Expected figures were read live on 2026-09-03 from
`DB/1A/PO_2024/0191A6DTHP8.px`, `0171A6DTHP6.px`, `0081A6DTPH7.px` and
`DB/1A/PO_2020/0011A6DPHH0.px`. A census is frozen, so the bounds below are
narrow on purpose and a change means PSA republished the table.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.autostitch import get_area_profile
from ph_civic_data_mcp.sources.psa import get_population_stats
from tests.live_helpers import skip_if_outage

pytestmark = pytest.mark.live

PSA_BASE = "https://openstat.psa.gov.ph/PXWeb/api/v1/en/DB/1A/"


def _assert_success_shape(result: dict, label: str) -> None:
    assert result["data_status"] == "success", (label, result.get("caveats"))
    assert result["upstream_error"] is False
    assert result["validation_error"] is False
    assert isinstance(result["population"], int) and result["population"] > 0
    assert result["source"] == "PSA"
    assert result["source_url"].startswith(PSA_BASE), result["source_url"]
    assert result["source_table"] == result["source_url"]
    assert result["census"] and str(result["year"]) in result["census"]
    assert result["reference_date"]
    assert result["geography"] and result["geography_level"]
    assert 2024 in result["available_vintages"]
    assert "/DB/1A/PO/" not in result["source_url"], "PO/ is the projection folder, not a census"


@pytest.mark.asyncio
async def test_national_total_comes_from_the_latest_census() -> None:
    result = await get_population_stats()
    skip_if_outage(result, "PSA population")
    _assert_success_shape(result, "national")
    assert result["year"] == max(result["available_vintages"])
    assert result["geography_level"] == "national"
    # 112,729,484 on 2026-09-03. A future census moves this up, never down.
    assert 112_000_000 <= result["population"] <= 130_000_000, result["population"]
    if result["year"] == 2024:
        assert result["population"] == 112_729_484
        assert result["reference_date"] == "2024-07-01"
        assert result["psgc_code"] == "0000000000"


@pytest.mark.asyncio
async def test_ncr_by_name_carries_its_psgc_code_and_level() -> None:
    result = await get_population_stats(region="NCR")
    skip_if_outage(result, "PSA population")
    _assert_success_shape(result, "NCR")
    assert result["geography_level"] == "region"
    assert "national capital region" in result["geography"].lower()
    if result["year"] == 2024:
        assert result["population"] == 14_001_751
        assert result["psgc_code"] == "1300000000"


@pytest.mark.asyncio
async def test_the_2020_census_is_still_reachable_by_year() -> None:
    result = await get_population_stats(region="NCR", year=2020)
    skip_if_outage(result, "PSA population 2020")
    _assert_success_shape(result, "NCR 2020")
    assert result["year"] == 2020
    assert result["reference_date"] == "2020-05-01"
    assert result["population"] == 13_484_462
    assert "/DB/1A/PO_2020/" in result["source_url"] or "2020" in result["source_url"]


@pytest.mark.asyncio
async def test_a_city_by_nine_digit_psgc_code() -> None:
    """Tacloban, a highly urbanized city, sits in the summary table."""
    result = await get_population_stats(psgc_code="083747000")
    skip_if_outage(result, "PSA population by PSGC code")
    _assert_success_shape(result, "Tacloban")
    assert result["psgc_code"] == "0831600000"
    assert "tacloban" in result["geography"].lower()
    assert result["geography_level"] in ("highly_urbanized_city", "city")
    assert "eastern visayas" in (result["parent_region"] or "").lower()
    if result["year"] == 2024:
        assert result["population"] == 259_353


@pytest.mark.asyncio
async def test_a_barangay_by_ten_digit_psgc_code() -> None:
    """Barangay 1, City of Caloocan, from the NCR barangay-level table."""
    result = await get_population_stats(psgc_code="1380100001")
    skip_if_outage(result, "PSA population barangay")
    _assert_success_shape(result, "Barangay 1 Caloocan")
    assert result["geography_level"] == "barangay"
    assert result["psgc_code"] == "1380100001"
    assert "national capital region" in (result["parent_region"] or "").lower()
    if result["year"] == 2024:
        assert result["population"] == 2356


@pytest.mark.asyncio
async def test_a_caller_mistake_is_never_reported_as_an_outage() -> None:
    result = await get_population_stats(year=2021)
    skip_if_outage(result, "PSA population")
    assert result["validation_error"] is True
    assert result["upstream_error"] is False
    assert result["available_vintages"] and 2024 in result["available_vintages"]


@pytest.mark.asyncio
async def test_area_profile_shows_population_or_names_why_not() -> None:
    """`population: null` beside an empty caveat list is the bug this guards."""
    profile = await get_area_profile("Tacloban")
    assert profile["resolved"]["matched"] is True
    demo = profile["demographics"]
    status = profile["blocks"]["population"]
    if status == "success":
        assert isinstance(demo["population"], int) and demo["population"] > 1_000_000
        assert demo["population_year"] >= 2020
        assert demo["population_census"]
    else:
        assert status == "unavailable"
        assert any("PSA population" in c for c in profile["caveats"]), profile["caveats"]
        assert profile["upstream_error"] is True
        pytest.skip(f"PSA population unavailable during the run: {profile['caveats']}")
