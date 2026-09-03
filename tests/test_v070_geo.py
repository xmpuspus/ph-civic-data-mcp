"""Offline test for the shared `haversine_km` helper.

v0.7.0 moved this function out of usgs.py and phivolcs.py, where it lived
as two identical private copies, into utils/geo.py as a public function.
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.utils.geo import haversine_km

MANILA = (14.5995, 120.9842)
CEBU_CITY = (10.3157, 123.8854)


def test_haversine_km_manila_to_cebu_city_is_about_572km():
    distance = haversine_km(*MANILA, *CEBU_CITY)
    assert distance == pytest.approx(572, abs=5)


def test_haversine_km_same_point_is_zero():
    assert haversine_km(*MANILA, *MANILA) == pytest.approx(0.0, abs=1e-9)
