"""Offline tests for the v0.7.0 national_reference addition to get_area_profile.

A resolved place now reports its own population and poverty (0a1cc6e), but a
caller still has no way to read that number against the country's without a
second tool call, and no way to see which geography a figure describes. This
file checks the two additive fields: the demographics provenance fields
(geography, geography level, PSGC code, census, reference date, poverty
area), and the new top-level national_reference block.

Every PSA, PSGC, hazard, weather, labor, and infra call is faked in memory,
following the `_profile_mocks` pattern in tests/test_v061_population.py.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources import autostitch as autostitch_module
from ph_civic_data_mcp.utils import envelope

# Every top-level key get_area_profile returned before v0.7.0. national_reference
# is the new addition and is checked separately.
_PRE_EXISTING_TOP_LEVEL_KEYS = [
    "query",
    "resolved",
    "demographics",
    "economy",
    "procurement",
    "hazard",
    "weather",
    "correlations",
    "blocks",
    "upstream_error",
    "caveats",
    "assessment_datetime",
    "source",
    "source_url",
    "license",
    "disclaimer",
    "data_retrieved_at",
]


async def _resolve(location):
    return {
        "matched": True,
        "psgc_code": "0831600000",
        "name": "City of Tacloban",
        "level": "city",
        "alternatives": [],
    }


async def _hierarchy(code):
    return {
        "chain": [
            {"level": "region", "name": "Region VIII"},
            {"level": "province", "name": "Leyte"},
            {"level": "city", "name": "City of Tacloban"},
        ]
    }


async def _ok_dict(*args, **kwargs):
    return {"reference_period": "2026-07", "headline_inflation_pct": 1.2}


async def _ok_list(*args, **kwargs):
    return []


def _install_common_mocks(monkeypatch):
    monkeypatch.setattr(autostitch_module, "resolve_ph_location", _resolve)
    monkeypatch.setattr(autostitch_module, "get_location_hierarchy", _hierarchy)
    monkeypatch.setattr(autostitch_module, "get_inflation_stats", _ok_dict)
    monkeypatch.setattr(autostitch_module, "get_labor_stats", _ok_dict)
    monkeypatch.setattr(autostitch_module, "assess_area_risk", _ok_dict)
    monkeypatch.setattr(autostitch_module, "get_weather_forecast", _ok_dict)
    monkeypatch.setattr(autostitch_module, "search_infra_projects", _ok_list)


def _city_population(psgc_code):
    return {
        "population": 259_353,
        "year": 2024,
        "geography": "City of Tacloban",
        "geography_level": "city",
        "psgc_code": psgc_code,
        "census": "2024 Census of Population",
        "reference_date": "2024-07-01",
        "reference_note": "PSA 2024 Census of Population, reference date 2024-07-01.",
    }


def _national_population(year=2024):
    return {
        "population": 114_000_000,
        "year": year,
        "geography": "Philippines",
        "geography_level": "country",
        "psgc_code": None,
        "census": "2024 Census of Population",
        "reference_date": "2024-07-01",
    }


def _province_poverty(region, year=2023):
    return {"region": region, "poverty_incidence_pct": 22.3, "reference_year": year}


def _national_poverty(year=2023):
    return {"region": "Philippines", "poverty_incidence_pct": 15.5, "reference_year": year}


@pytest.fixture()
def _same_vintage_mocks(monkeypatch):
    _install_common_mocks(monkeypatch)

    async def _population(*, psgc_code=None, region=None, year=None):
        if psgc_code is not None:
            return _city_population(psgc_code)
        return _national_population()

    async def _poverty(*, region=None):
        if region is None:
            return _national_poverty()
        return _province_poverty(region)

    monkeypatch.setattr(autostitch_module, "get_population_stats", _population)
    monkeypatch.setattr(autostitch_module, "get_poverty_stats", _poverty)
    yield


@pytest.mark.asyncio
async def test_demographics_carries_the_five_provenance_fields(_same_vintage_mocks):
    profile = await autostitch_module.get_area_profile("Tacloban")
    demo = profile["demographics"]

    assert demo["population_geography"] == "City of Tacloban"
    assert demo["population_geography_level"] == "city"
    assert demo["population_psgc_code"] == "0831600000"
    assert demo["population_census"] == "2024 Census of Population"
    assert demo["population_reference_date"] == "2024-07-01"


@pytest.mark.asyncio
async def test_poverty_area_names_the_province_a_city_query_borrows(_same_vintage_mocks):
    profile = await autostitch_module.get_area_profile("Tacloban")

    assert profile["demographics"]["poverty_area"] == "Leyte"


@pytest.mark.asyncio
async def test_national_reference_computes_share_and_gap_on_matching_vintages(
    _same_vintage_mocks,
):
    profile = await autostitch_module.get_area_profile("Tacloban")
    ref = profile["national_reference"]

    assert ref["population"] == 114_000_000
    assert ref["population_year"] == 2024
    assert ref["poverty_incidence_pct"] == pytest.approx(15.5)
    assert ref["poverty_year"] == 2023
    assert ref["population_share_pct"] == pytest.approx(round(259_353 / 114_000_000 * 100, 2))
    assert ref["poverty_gap_pct_points"] == pytest.approx(round(22.3 - 15.5, 1))
    assert profile["blocks"]["national_population"] == "success"
    assert profile["blocks"]["national_poverty"] == "success"


@pytest.mark.asyncio
async def test_differing_vintages_withhold_the_share_and_the_gap(monkeypatch):
    _install_common_mocks(monkeypatch)

    async def _population(*, psgc_code=None, region=None, year=None):
        if psgc_code is not None:
            return _city_population(psgc_code)
        return _national_population(year=2020)

    async def _poverty(*, region=None):
        if region is None:
            return _national_poverty(year=2018)
        return _province_poverty(region)

    monkeypatch.setattr(autostitch_module, "get_population_stats", _population)
    monkeypatch.setattr(autostitch_module, "get_poverty_stats", _poverty)

    profile = await autostitch_module.get_area_profile("Tacloban")
    ref = profile["national_reference"]

    assert ref["population_share_pct"] is None
    assert ref["poverty_gap_pct_points"] is None
    assert any(
        "population vintages differ" in c and "2024" in c and "2020" in c
        for c in profile["caveats"]
    )
    assert any(
        "poverty vintages differ" in c and "2023" in c and "2018" in c for c in profile["caveats"]
    )


@pytest.mark.asyncio
async def test_unknown_vintage_withholds_share_and_gap_instead_of_matching_on_none(monkeypatch):
    _install_common_mocks(monkeypatch)

    async def _population(*, psgc_code=None, region=None, year=None):
        if psgc_code is not None:
            return {**_city_population(psgc_code), "population": 100, "year": None}
        return {**_national_population(), "population": 1_000, "year": None}

    async def _poverty(*, region=None):
        if region is None:
            return {**_national_poverty(), "reference_year": None}
        return {**_province_poverty(region), "reference_year": None}

    monkeypatch.setattr(autostitch_module, "get_population_stats", _population)
    monkeypatch.setattr(autostitch_module, "get_poverty_stats", _poverty)

    profile = await autostitch_module.get_area_profile("Tacloban")
    ref = profile["national_reference"]

    assert ref["population_share_pct"] is None
    assert ref["poverty_gap_pct_points"] is None
    assert any("population vintage unknown" in c for c in profile["caveats"])
    assert any("poverty vintage unknown" in c for c in profile["caveats"])


@pytest.mark.asyncio
async def test_a_national_fetch_failure_never_crashes_the_profile(monkeypatch):
    _install_common_mocks(monkeypatch)

    async def _population(*, psgc_code=None, region=None, year=None):
        if psgc_code is not None:
            return _city_population(psgc_code)
        return envelope.failure_result(
            "PSA", "https://openstat", "PSA fetch error: ConnectError: down", population=None
        )

    async def _poverty(*, region=None):
        if region is None:
            return _national_poverty()
        return _province_poverty(region)

    monkeypatch.setattr(autostitch_module, "get_population_stats", _population)
    monkeypatch.setattr(autostitch_module, "get_poverty_stats", _poverty)

    profile = await autostitch_module.get_area_profile("Tacloban")

    assert profile["blocks"]["national_population"] == "unavailable"
    assert any("PSA national population" in c for c in profile["caveats"])
    assert profile["national_reference"]["population"] is None
    assert profile["national_reference"]["population_share_pct"] is None
    assert profile["upstream_error"] is True


@pytest.mark.asyncio
async def test_profile_keeps_every_pre_existing_top_level_key(_same_vintage_mocks):
    profile = await autostitch_module.get_area_profile("Tacloban")

    for key in _PRE_EXISTING_TOP_LEVEL_KEYS:
        assert key in profile, key
    assert "national_reference" in profile


def _infra_of_size(n):
    async def _fake(*args, **kwargs):
        return [{"notice_id": str(i)} for i in range(n)]

    return _fake


@pytest.mark.asyncio
async def test_infra_per_100k_withheld_below_the_sample_threshold(_same_vintage_mocks, monkeypatch):
    monkeypatch.setattr(autostitch_module, "search_infra_projects", _infra_of_size(80))

    profile = await autostitch_module.get_area_profile("Tacloban")
    corr = profile["correlations"]

    assert corr["infra_notices_per_100k_population"] is None
    assert corr["infra_sample_size"] == 80
    assert "500-notice" in corr["infra_coverage_caveat"]
    assert any("500-notice" in c for c in profile["caveats"])


@pytest.mark.asyncio
async def test_infra_per_100k_computed_above_the_sample_threshold(_same_vintage_mocks, monkeypatch):
    monkeypatch.setattr(autostitch_module, "search_infra_projects", _infra_of_size(600))

    profile = await autostitch_module.get_area_profile("Tacloban")
    corr = profile["correlations"]

    assert corr["infra_notices_per_100k_population"] == pytest.approx(
        round(600 / 259_353 * 100_000, 2)
    )
    assert corr["infra_sample_size"] == 600
    assert "infra_coverage_caveat" not in corr


@pytest.mark.asyncio
async def test_population_empty_status_propagates_to_the_block(monkeypatch):
    """Codex cross-model finding: a child returning data_status "empty" set
    neither upstream_error nor validation_error, so `_unwrap` read it as a
    plain success and dropped the "no row" caveat."""
    _install_common_mocks(monkeypatch)

    async def _population(*, psgc_code=None, region=None, year=None):
        return envelope.failure_result(
            "PSA", "https://openstat", "no row", data_status="empty", population=None
        )

    async def _poverty(*, region=None):
        return _national_poverty()

    monkeypatch.setattr(autostitch_module, "get_population_stats", _population)
    monkeypatch.setattr(autostitch_module, "get_poverty_stats", _poverty)

    profile = await autostitch_module.get_area_profile("Tacloban")

    assert profile["blocks"]["population"] == "empty"
    assert any("no row" in c for c in profile["caveats"])


@pytest.mark.asyncio
async def test_infra_is_skipped_when_the_place_does_not_resolve(monkeypatch):
    """Codex cross-model finding: an unmatched place still ran the infra
    search with province=None, region=None, and reported the national
    notice count as this place's own infra_notice_count."""
    _install_common_mocks(monkeypatch)

    async def _no_match(location):
        return {
            "matched": False,
            "name": None,
            "psgc_code": None,
            "level": None,
            "alternatives": [],
        }

    async def _population(*, psgc_code=None, region=None, year=None):
        return _national_population()

    async def _poverty(*, region=None):
        return _national_poverty()

    async def _infra_never_called(*args, **kwargs):
        raise AssertionError("search_infra_projects must not run for an unresolved place")

    monkeypatch.setattr(autostitch_module, "resolve_ph_location", _no_match)
    monkeypatch.setattr(autostitch_module, "get_population_stats", _population)
    monkeypatch.setattr(autostitch_module, "get_poverty_stats", _poverty)
    monkeypatch.setattr(autostitch_module, "search_infra_projects", _infra_never_called)

    profile = await autostitch_module.get_area_profile("Qwxzv Nonesuch")

    assert profile["blocks"]["infra"] == autostitch_module.BLOCK_SKIPPED
    assert profile["procurement"]["infra_notice_count"] is None
