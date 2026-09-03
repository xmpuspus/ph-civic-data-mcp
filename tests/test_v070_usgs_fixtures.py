"""Offline tests for USGS earthquakes: the v0.7.0 radius_km filter, plus
success / empty / upstream-failure / schema-drift coverage this module never
had (only a live smoke test in tests/test_v020_sources.py).
"""

from __future__ import annotations

from math import degrees

import httpx
import pytest

from ph_civic_data_mcp.sources import usgs as usgs_module
from ph_civic_data_mcp.utils.cache import CACHES

EARTH_RADIUS_KM = 6371.0
CENTER_LAT, CENTER_LON = 14.5995, 120.9842  # Manila


def _lat_offset_for_km(km: float) -> float:
    """Degrees of latitude that, at the same longitude, sit exactly km away.

    A pure latitude offset is a great-circle arc along one meridian, so this
    is an independent check on _haversine_km: distance = R * radians(offset).
    """
    return degrees(km / EARTH_RADIUS_KM)


def _feature(
    event_id: str, lat: float = CENTER_LAT, lon: float = CENTER_LON, mag: float = 5.0
) -> dict:
    return {
        "id": event_id,
        "properties": {
            "time": 1_700_000_000_000,
            "mag": mag,
            "magType": "mww",
            "place": event_id,
            "felt": None,
            "tsunami": 0,
            "url": f"https://example.test/{event_id}",
        },
        "geometry": {"coordinates": [lon, lat, 10.0]},
    }


def _payload(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _install_fake_fetch(monkeypatch, payload: dict | None = None, *, status: int = 200, exc=None):
    async def _fake(client, method, url, **kwargs):
        if exc is not None:
            raise exc
        return httpx.Response(status, json=payload or {}, request=httpx.Request(method, url))

    monkeypatch.setattr(usgs_module, "fetch_with_retry", _fake)


@pytest.fixture(autouse=True)
def _clear_cache():
    CACHES["usgs_events"].clear()
    yield


@pytest.mark.asyncio
async def test_success_returns_events(monkeypatch):
    payload = _payload([_feature("us1"), _feature("us2")])
    _install_fake_fetch(monkeypatch, payload)

    results = await usgs_module.get_usgs_earthquakes_ph(min_magnitude=1.0, limit=10)

    assert isinstance(results, list)
    assert len(results) == 2
    assert results[0]["source"] == "USGS FDSN"


@pytest.mark.asyncio
async def test_empty_features_returns_empty_list(monkeypatch):
    _install_fake_fetch(monkeypatch, _payload([]))

    results = await usgs_module.get_usgs_earthquakes_ph(min_magnitude=1.0, limit=10)

    assert results == []


@pytest.mark.asyncio
async def test_upstream_failure_returns_envelope(monkeypatch):
    _install_fake_fetch(monkeypatch, exc=httpx.ConnectError("no route"))

    result = await usgs_module.get_usgs_earthquakes_ph(min_magnitude=1.0, limit=10)

    assert result["upstream_error"] is True
    assert result["results"] == []
    assert "USGS FDSN API unavailable" in result["caveats"][0]


@pytest.mark.asyncio
async def test_schema_drift_missing_mag_is_skipped_not_crashed(monkeypatch):
    drifted = {
        "id": "us_drift",
        "properties": {"time": 1_700_000_000_000, "place": "drift"},
        "geometry": {"coordinates": [120.9842, 14.5995, 10.0]},
    }
    payload = _payload([drifted, _feature("us_good")])
    _install_fake_fetch(monkeypatch, payload)

    results = await usgs_module.get_usgs_earthquakes_ph(min_magnitude=1.0, limit=10)

    assert len(results) == 1
    assert results[0]["usgs_event_id"] == "us_good"


@pytest.mark.asyncio
async def test_radius_km_requires_center_lat_and_lon(monkeypatch):
    _install_fake_fetch(monkeypatch, _payload([]))

    result = await usgs_module.get_usgs_earthquakes_ph(radius_km=50.0)

    assert result["validation_error"] is True
    assert result["results"] == []


@pytest.mark.asyncio
async def test_radius_km_must_be_positive(monkeypatch):
    _install_fake_fetch(monkeypatch, _payload([]))

    result = await usgs_module.get_usgs_earthquakes_ph(
        center_lat=CENTER_LAT, center_lon=CENTER_LON, radius_km=0
    )

    assert result["validation_error"] is True


@pytest.mark.asyncio
async def test_radius_km_includes_and_excludes_at_the_boundary(monkeypatch):
    radius_km = 50.0
    at_boundary = CENTER_LAT + _lat_offset_for_km(radius_km)
    just_inside = CENTER_LAT + _lat_offset_for_km(radius_km - 1)
    just_outside = CENTER_LAT + _lat_offset_for_km(radius_km + 1)

    payload = _payload(
        [
            _feature("at_boundary", at_boundary, CENTER_LON),
            _feature("just_inside", just_inside, CENTER_LON),
            _feature("just_outside", just_outside, CENTER_LON),
        ]
    )
    _install_fake_fetch(monkeypatch, payload)

    results = await usgs_module.get_usgs_earthquakes_ph(
        center_lat=CENTER_LAT, center_lon=CENTER_LON, radius_km=radius_km, limit=10
    )

    ids = {e["usgs_event_id"] for e in results}
    assert ids == {"at_boundary", "just_inside"}
    for event in results:
        assert event["distance_km"] <= radius_km


@pytest.mark.asyncio
async def test_radius_km_result_count_is_capped_at_limit(monkeypatch):
    radius_km = 100.0
    features = [
        _feature(f"near{i}", CENTER_LAT + _lat_offset_for_km(i), CENTER_LON) for i in range(10)
    ]
    _install_fake_fetch(monkeypatch, _payload(features))

    results = await usgs_module.get_usgs_earthquakes_ph(
        center_lat=CENTER_LAT, center_lon=CENTER_LON, radius_km=radius_km, limit=3
    )

    assert len(results) == 3


@pytest.mark.asyncio
async def test_a_non_object_json_body_returns_the_failure_envelope(monkeypatch):
    """Codex cross-model finding: a 200 whose body is a bare list crashed on
    `.get()` instead of returning the failure envelope."""
    _install_fake_fetch(monkeypatch, payload=["not", "a", "feature", "collection"])

    result = await usgs_module.get_usgs_earthquakes_ph()

    assert isinstance(result, dict)
    assert result["upstream_error"] is True
    assert result["results"] == []
    assert any("unexpected payload shape" in c for c in result["caveats"]), result["caveats"]
    assert not CACHES["usgs_events"], "a malformed body must never be cached"
