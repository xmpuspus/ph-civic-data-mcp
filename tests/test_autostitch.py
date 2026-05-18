"""Live auto-stitch integration tests (v0.4.0). Skips gracefully offline.

Mirrors tests/test_phivolcs.py. Verifies get_area_profile composes the
resolved-PSGC spine + PSA + procurement + hazard + weather in one envelope,
carries the per-block reference periods, the derived per-capita correlation,
the public-data disclaimer, and degrades gracefully on an unresolvable place.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.autostitch import get_area_profile


def _has_envelope(profile: dict) -> None:
    assert "PSA" in profile["source"]
    assert profile["source_url"].startswith("http")
    assert profile["license"]
    assert profile["disclaimer"]
    assert profile["data_retrieved_at"]
    assert isinstance(profile["caveats"], list)
    # Civic-tech rule: analytics endpoints must carry the public-data disclaimer.
    assert "legitimate explanations" in profile["disclaimer"].lower()


@pytest.mark.asyncio
async def test_area_profile_resolved_province() -> None:
    profile = await get_area_profile("Leyte")
    _has_envelope(profile)
    resolved = profile["resolved"]
    assert resolved["matched"] is True
    assert resolved["region"]
    assert resolved["psgc_code"]

    demo = profile["demographics"]
    if demo.get("population") is None:
        pytest.skip(f"PSA population unavailable: {profile['caveats']}")
    assert isinstance(demo["population"], int)
    assert demo["population"] > 0

    econ = profile["economy"]
    assert "headline_inflation_pct" in econ
    assert "employment_rate_pct" in econ

    corr = profile["correlations"]
    assert "infra_notices_per_100k_population" in corr
    assert corr["infra_notice_count"] >= 0

    assert "earthquake_risk_level" in profile["hazard"]


@pytest.mark.asyncio
async def test_area_profile_region_query() -> None:
    profile = await get_area_profile("NCR")
    _has_envelope(profile)
    assert profile["resolved"]["matched"] is True
    if profile["demographics"].get("population") is not None:
        assert isinstance(profile["demographics"]["population"], int)


@pytest.mark.asyncio
async def test_area_profile_unresolved_is_graceful() -> None:
    # PSGC fuzzy-resolves loosely (substring/ratio), so the string must contain
    # no PH place substring; "Qwxzv Nonesuch 9000" stays below the 0.6 cutoff.
    profile = await get_area_profile("Qwxzv Nonesuch 9000")
    _has_envelope(profile)
    assert profile["resolved"]["matched"] is False
    assert profile["caveats"]
    # Hazard + weather still run on the raw string even without a PSGC match.
    assert "hazard" in profile
    assert "weather" in profile


@pytest.mark.asyncio
async def test_area_profile_per_capita_is_consistent() -> None:
    profile = await get_area_profile("Cebu City")
    _has_envelope(profile)
    corr = profile["correlations"]
    pop = profile["demographics"].get("population")
    count = corr["infra_notice_count"]
    if pop and corr["infra_notices_per_100k_population"] is not None:
        expected = round(count / pop * 100_000, 2)
        assert corr["infra_notices_per_100k_population"] == expected
