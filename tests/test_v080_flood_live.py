"""Live checks for get_flood_forecast against the real Open-Meteo Flood API.

Skips on a positively identified outage, fails on drift.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.open_meteo_flood import get_flood_forecast
from tests.live_helpers import skip_if_outage

pytestmark = pytest.mark.live


async def test_marikina_three_day_discharge_has_three_dated_rows() -> None:
    result = await get_flood_forecast("Marikina", forecast_days=3)
    skip_if_outage(result, "Open-Meteo Flood")
    assert result["data_status"] == "success", result
    assert result["forecast_days"] == 3
    assert len(result["days"]) == 3, result["days"]
    for day in result["days"]:
        assert len(day["date"]) == 10, day
        value = day["river_discharge_m3s"]
        assert value is None or value >= 0, day
    assert "GloFAS" in result["source"]


async def test_a_bad_day_count_is_a_caller_mistake_not_an_outage() -> None:
    result = await get_flood_forecast("Marikina", forecast_days=0)
    assert result["data_status"] == "invalid_request", result
    assert result.get("validation_error") is True
    assert not result.get("upstream_error")
