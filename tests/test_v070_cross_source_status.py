"""Offline tests for assess_area_risk's top-level failure status (v0.7.0).

Codex cross-model finding: `_unwrap_list` turns a failed child envelope into
an empty list, so a downed PHIVOLCS or PAGASA sub-call used to read as a
clean "Low" risk answer, with no data_status or upstream_error at the top
level to say otherwise. The fix adds blocks, data_status, and upstream_error,
and nulls earthquake_risk_level when the earthquake or volcano data failed.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources import cross_source as cs

DOWN_CAVEAT = "down"


def _down(**_):
    async def _call(*args, **kwargs):
        return {"results": [], "upstream_error": True, "caveats": [DOWN_CAVEAT]}

    return _call


def _empty(**_):
    async def _call(*args, **kwargs):
        return []

    return _call


@pytest.mark.asyncio
async def test_all_four_children_down_gives_unavailable_and_null_risk(monkeypatch):
    monkeypatch.setattr(cs, "get_latest_earthquakes", _down())
    monkeypatch.setattr(cs, "get_active_typhoons", _down())
    monkeypatch.setattr(cs, "get_weather_alerts", _down())
    monkeypatch.setattr(cs, "get_volcano_status", _down())

    result = await cs.assess_area_risk("Manila")

    assert result["data_status"] == "unavailable"
    assert result["upstream_error"] is True
    assert result["earthquake_risk_level"] is None
    assert result["blocks"] == {
        "earthquakes": "unavailable",
        "typhoons": "unavailable",
        "alerts": "unavailable",
        "volcanoes": "unavailable",
    }
    assert len(result["caveats"]) == 4
    assert all(DOWN_CAVEAT in c for c in result["caveats"])


@pytest.mark.asyncio
async def test_one_degraded_volcano_entry_gives_indeterminate_not_success(monkeypatch):
    """get_volcano_status returns a list on success, but one entry inside it
    can still carry upstream_error true when only its own bulletin fetch
    failed. That entry must not read as a clean volcano block.
    """
    monkeypatch.setattr(cs, "get_latest_earthquakes", _empty())
    monkeypatch.setattr(cs, "get_active_typhoons", _empty())
    monkeypatch.setattr(cs, "get_weather_alerts", _empty())

    async def _degraded_volcano(*args, **kwargs):
        return [
            {
                "name": "Mayon",
                "alert_level": None,
                "upstream_error": True,
                "caveats": ["PHIVOLCS down"],
            }
        ]

    monkeypatch.setattr(cs, "get_volcano_status", _degraded_volcano)

    result = await cs.assess_area_risk("Manila")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert result["blocks"]["volcanoes"] == "indeterminate"
    assert "PHIVOLCS down" in result["caveats"]


@pytest.mark.asyncio
async def test_only_typhoon_child_down_gives_indeterminate_and_keeps_risk_level(monkeypatch):
    monkeypatch.setattr(cs, "get_latest_earthquakes", _empty())
    monkeypatch.setattr(cs, "get_active_typhoons", _down())
    monkeypatch.setattr(cs, "get_weather_alerts", _empty())
    monkeypatch.setattr(cs, "get_volcano_status", _empty())

    result = await cs.assess_area_risk("Manila")

    assert result["data_status"] == "indeterminate"
    assert result["upstream_error"] is True
    assert result["earthquake_risk_level"] == "Low"
    assert result["blocks"] == {
        "earthquakes": "success",
        "typhoons": "unavailable",
        "alerts": "success",
        "volcanoes": "success",
    }
    assert len(result["caveats"]) == 1
    assert DOWN_CAVEAT in result["caveats"][0]
