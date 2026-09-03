"""Offline tests for compare_areas (v0.7.0).

No live HTTP. compare.get_area_profile is monkeypatched with a fake that
returns canned get_area_profile-shaped dicts keyed by location, so the tests
cover row-building, the comparability guard, and the validation gate on
their own, without touching any real upstream.
"""

from __future__ import annotations

import csv
import io

import pytest

from ph_civic_data_mcp.sources import compare


def _profile(
    *,
    matched: bool = True,
    name: str = "Cebu City",
    psgc_code: str = "072217000",
    level: str = "city",
    population: int | None = 964000,
    population_year: int | None = 2024,
    poverty_incidence_pct: float | None = 8.2,
    poverty_reference_year: str | None = "2023",
    headline_inflation_pct: float | None = 3.1,
    employment_rate_pct: float | None = 95.4,
    infra_notice_count: int | None = 12,
    earthquake_risk_level: str | None = "low",
    caveats: list[str] | None = None,
) -> dict:
    return {
        "query": name,
        "resolved": {
            "matched": matched,
            "name": name if matched else None,
            "psgc_code": psgc_code if matched else None,
            "level": level if matched else None,
        },
        "demographics": {
            "population": population,
            "population_year": population_year,
            "poverty_incidence_pct": poverty_incidence_pct,
            "poverty_reference_year": poverty_reference_year,
        },
        "economy": {
            "headline_inflation_pct": headline_inflation_pct,
            "employment_rate_pct": employment_rate_pct,
        },
        "correlations": {"infra_notice_count": infra_notice_count},
        "hazard": {"earthquake_risk_level": earthquake_risk_level},
        "blocks": {"resolve": "success" if matched else "unavailable"},
        "upstream_error": False,
        "caveats": caveats or [],
        "source": "PSGC + PSA + PhilGEPS + PHIVOLCS + PAGASA",
        "source_url": "https://example.test",
        "license": "Public",
        "disclaimer": compare.PROFILE_DISCLAIMER,
        "data_retrieved_at": "2026-09-04T00:00:00+00:00",
    }


def _install(monkeypatch, by_location: dict[str, dict]):
    async def _fake(location: str) -> dict:
        return by_location[location]

    monkeypatch.setattr(compare, "get_area_profile", _fake)


def _install_never_called(monkeypatch):
    async def _fake(location: str) -> dict:
        raise AssertionError(f"get_area_profile must not be called for {location!r}")

    monkeypatch.setattr(compare, "get_area_profile", _fake)


@pytest.mark.asyncio
async def test_compare_two_locations_returns_rows_and_comparable_true(monkeypatch):
    by_location = {
        "Cebu City": _profile(name="Cebu City"),
        "Davao City": _profile(name="Davao City", psgc_code="112402000"),
    }
    _install(monkeypatch, by_location)

    out = await compare.compare_areas(
        ["Cebu City", "Davao City"], metrics=["population", "poverty_incidence_pct"]
    )

    assert out["data_status"] == "success"
    assert out["upstream_error"] is False
    assert out["validation_error"] is False
    assert out["comparable"] is True
    assert out["metrics"] == ["population", "poverty_incidence_pct"]
    assert len(out["rows"]) == 2
    row = out["rows"][0]
    assert row["location"] == "Cebu City"
    assert row["resolved_name"] == "Cebu City"
    assert row["population"] == 964000
    assert row["poverty_incidence_pct"] == pytest.approx(8.2)
    assert "export" not in out


@pytest.mark.asyncio
async def test_compare_default_metrics_is_the_full_allowlist(monkeypatch):
    by_location = {
        "Cebu City": _profile(name="Cebu City"),
        "Davao City": _profile(name="Davao City"),
    }
    _install(monkeypatch, by_location)

    out = await compare.compare_areas(["Cebu City", "Davao City"])

    assert out["metrics"] == list(compare.COMPARE_METRICS)
    for metric in compare.COMPARE_METRICS:
        assert metric in out["rows"][0]


@pytest.mark.asyncio
async def test_compare_vintage_mismatch_sets_not_comparable(monkeypatch):
    by_location = {
        "Cebu City": _profile(name="Cebu City", population_year=2024),
        "Davao City": _profile(name="Davao City", population_year=2020),
    }
    _install(monkeypatch, by_location)

    out = await compare.compare_areas(["Cebu City", "Davao City"], metrics=["population"])

    assert out["comparable"] is False
    joined = " ".join(out["caveats"])
    assert "population" in joined
    assert "2024" in joined
    assert "2020" in joined


@pytest.mark.asyncio
async def test_compare_level_mismatch_adds_a_caveat(monkeypatch):
    by_location = {
        "Cebu City": _profile(name="Cebu City", level="city"),
        "Region VII": _profile(name="Region VII", level="region"),
    }
    _install(monkeypatch, by_location)

    out = await compare.compare_areas(["Cebu City", "Region VII"], metrics=["population"])

    joined = " ".join(out["caveats"])
    assert "admin level" in joined
    assert "city" in joined and "region" in joined


@pytest.mark.asyncio
async def test_compare_one_unresolved_location_is_indeterminate(monkeypatch):
    by_location = {
        "Cebu City": _profile(name="Cebu City"),
        "Qwxzv Nonesuch": _profile(
            matched=False, caveats=["'Qwxzv Nonesuch' did not resolve to a PSGC record"]
        ),
    }
    _install(monkeypatch, by_location)

    out = await compare.compare_areas(["Cebu City", "Qwxzv Nonesuch"], metrics=["population"])

    assert out["data_status"] == "indeterminate"
    assert out["upstream_error"] is True
    unresolved_row = out["rows"][1]
    assert unresolved_row["resolved_name"] is None
    assert any("Qwxzv Nonesuch" in c for c in out["caveats"])


@pytest.mark.asyncio
async def test_compare_upstream_exception_leaves_a_row_with_no_resolved_name(monkeypatch):
    async def _fake(location: str) -> dict:
        if location == "Cebu City":
            return _profile(name="Cebu City")
        raise RuntimeError("boom")

    monkeypatch.setattr(compare, "get_area_profile", _fake)

    out = await compare.compare_areas(["Cebu City", "Down Site"], metrics=["population"])

    assert out["data_status"] == "indeterminate"
    assert out["upstream_error"] is True
    down_row = out["rows"][1]
    assert down_row["resolved_name"] is None
    assert down_row["population"] is None
    assert out["blocks"]["Down Site"] == {"profile": "unavailable"}
    assert any("RuntimeError" in c and "Down Site" in c for c in out["caveats"])


@pytest.mark.asyncio
async def test_compare_rejects_too_few_locations(monkeypatch):
    _install_never_called(monkeypatch)
    out = await compare.compare_areas(["Cebu City"])
    assert out["validation_error"] is True
    assert out["upstream_error"] is False
    assert out["data_status"] == "invalid_request"


@pytest.mark.asyncio
async def test_compare_rejects_too_many_locations(monkeypatch):
    _install_never_called(monkeypatch)
    out = await compare.compare_areas(["A", "B", "C", "D", "E", "F"])
    assert out["validation_error"] is True


@pytest.mark.asyncio
async def test_compare_rejects_empty_location_string(monkeypatch):
    _install_never_called(monkeypatch)
    out = await compare.compare_areas(["Cebu City", "   "])
    assert out["validation_error"] is True


@pytest.mark.asyncio
async def test_compare_rejects_unknown_metric(monkeypatch):
    _install_never_called(monkeypatch)
    out = await compare.compare_areas(["Cebu City", "Davao City"], metrics=["not_a_metric"])
    assert out["validation_error"] is True


@pytest.mark.asyncio
async def test_compare_rejects_bad_format(monkeypatch):
    _install_never_called(monkeypatch)
    out = await compare.compare_areas(["Cebu City", "Davao City"], format="xml")
    assert out["validation_error"] is True


@pytest.mark.asyncio
async def test_compare_csv_export_round_trips(monkeypatch):
    by_location = {
        "Cebu City": _profile(name="Cebu City"),
        "Davao City": _profile(name="Davao City"),
    }
    _install(monkeypatch, by_location)

    out = await compare.compare_areas(
        ["Cebu City", "Davao City"], metrics=["population"], format="csv"
    )

    assert "export" in out
    reader = csv.reader(io.StringIO(out["export"]))
    lines = list(reader)
    assert lines[0] == ["location", "resolved_name", "psgc_code", "level", "population"]
    assert len(lines) == 3
    assert lines[1][0] == "Cebu City"
    assert lines[2][0] == "Davao City"


@pytest.mark.asyncio
async def test_compare_a_failed_block_inside_a_resolved_profile_is_not_success(monkeypatch):
    """Codex cross-model finding: `matched: true` with a dead population block
    read as data_status success and comparable true around two null rows."""
    cebu = _profile(name="Cebu City", population=None, population_year=None)
    cebu["blocks"] = {"resolve": "success", "population": "unavailable"}
    cebu["upstream_error"] = True
    davao = _profile(
        name="Davao City", psgc_code="112402000", population=None, population_year=None
    )
    davao["blocks"] = {"resolve": "success", "population": "unavailable"}
    davao["upstream_error"] = True
    _install(monkeypatch, {"Cebu City": cebu, "Davao City": davao})

    result = await compare.compare_areas(["Cebu City", "Davao City"], metrics=["population"])

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert result["comparable"] is False
    assert any("failed upstream" in c for c in result["caveats"]), result["caveats"]
    assert all(row["population"] is None for row in result["rows"])


@pytest.mark.asyncio
async def test_compare_a_healthy_block_the_caller_did_not_ask_for_does_not_degrade(monkeypatch):
    """A dead hazard block must not degrade a population-only comparison."""
    cebu = _profile(name="Cebu City")
    cebu["blocks"] = {"resolve": "success", "population": "success", "hazard": "unavailable"}
    davao = _profile(name="Davao City", psgc_code="112402000")
    davao["blocks"] = {"resolve": "success", "population": "success", "hazard": "unavailable"}
    _install(monkeypatch, {"Cebu City": cebu, "Davao City": davao})

    result = await compare.compare_areas(["Cebu City", "Davao City"], metrics=["population"])

    assert result["data_status"] == "success"
    assert result["comparable"] is True
