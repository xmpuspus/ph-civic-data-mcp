"""Regression tests for v0.3.1 fixes.

Each test pins a specific behaviour identified in the 2026-05-01 product audit:
- C1: get_weather_alerts no longer fabricates advisories from PAGASA chrome
- C2: flag_infra_anomalies hazard_overlap requires proper-noun keywords
- H1: city_to_coords handles "City of Manila" / "Sta. Mesa, Manila"
- H3: search_infra_projects(province=...) expands via _PROVINCE_AGENCY_HINTS
"""

from __future__ import annotations

import pytest

from ph_civic_data_mcp.sources.cross_source import _proper_noun_tokens
from ph_civic_data_mcp.sources.infra import _province_search_terms
from ph_civic_data_mcp.utils.geo import city_to_coords


# --- H1: city_to_coords handles PSGC name shapes -----------------------------


def test_city_to_coords_handles_city_of_manila_inversion():
    """PSGC returns 'City of Manila' for 'Manila' queries; the bridge must
    not silently fail. Audit C/H1 (2026-05-01)."""
    assert city_to_coords("City of Manila") is not None
    assert city_to_coords("city of manila") is not None
    assert city_to_coords("City of Manila") == city_to_coords("Manila")


def test_city_to_coords_handles_municipality_of_prefix():
    assert city_to_coords("Municipality of Tagaytay") is not None
    assert city_to_coords("Municipality of Tagaytay") == city_to_coords("Tagaytay")


def test_city_to_coords_handles_multi_segment_input():
    """'Sta. Mesa, Manila' is the most natural Manila phrasing and must
    return Manila coordinates via the last comma-segment."""
    assert city_to_coords("Sta. Mesa, Manila") is not None
    assert city_to_coords("Sta. Mesa, Manila") == city_to_coords("Manila")


def test_city_to_coords_returns_none_for_unknown():
    assert city_to_coords("Atlantis") is None
    assert city_to_coords("") is None


# --- C2: hazard_overlap proper-noun gating -----------------------------------


def test_proper_noun_tokens_drops_lowercase_chrome():
    """Words like 'city' that survive only as lowercase must be stoplisted
    so 'Pasig City' titles don't false-match. Audit C2 (2026-05-01)."""
    tokens = _proper_noun_tokens("012 km N 38° E of San Jose De Buan (Samar)")
    # Capitalised proper nouns survive; the rest go.
    assert "samar" in tokens
    # 'jose' is shorter than 5 chars; 'buan' is a real PH locality but ditto.
    assert "city" not in tokens
    assert "philippines" not in tokens


def test_proper_noun_tokens_stoplists_geographic_chrome():
    """Capitalised but generic words still drop via stoplist."""
    tokens = _proper_noun_tokens("Eastern Samar Region")
    # 'eastern' and 'region' are stoplisted; 'samar' survives.
    assert "samar" in tokens
    assert "eastern" not in tokens
    assert "region" not in tokens


def test_proper_noun_tokens_handles_empty():
    assert _proper_noun_tokens("") == []
    assert _proper_noun_tokens("   ") == []


def test_proper_noun_tokens_dedupes():
    tokens = _proper_noun_tokens("Surigao Surigao Surigao")
    assert tokens.count("surigao") == 1


# --- H3: province expansion --------------------------------------------------


def test_province_search_terms_expands_pampanga():
    """'Pampanga' must include 'region iii' so DPWH agency-named notices
    matching only the region tag still pass the province filter.
    Audit H3 (2026-05-01)."""
    terms = _province_search_terms("Pampanga")
    assert "pampanga" in terms
    assert "region iii" in terms


def test_province_search_terms_returns_literal_for_unknown():
    """Unmapped names fall back to a literal lowercase substring match."""
    assert _province_search_terms("Atlantis") == ["atlantis"]


def test_province_search_terms_empty():
    assert _province_search_terms(None) == []
    assert _province_search_terms("") == []


# --- C1: get_weather_alerts no longer fabricates -----------------------------


@pytest.mark.asyncio
async def test_get_weather_alerts_returns_indeterminate_not_a_fabricated_empty(monkeypatch):
    """The tool must never embed PAGASA navigation chrome as a real alert,
    and must never read an unrecognized page as a confirmed "no active
    warnings" all-clear either. Audit C1 (2026-05-01), tightened v0.7.0:
    only the explicit marker proves the negative; anything else is
    indeterminate and uncached, the same rule get_active_typhoons applies."""
    from ph_civic_data_mcp.sources import pagasa
    from ph_civic_data_mcp.utils.cache import CACHES

    # Bypass cache by using a unique region key.
    CACHES["pagasa_alerts"].clear()

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text
            self.status_code = 200

        def raise_for_status(self) -> None:
            pass

    nav_chrome = (
        "<html><body>"
        "<nav>Heavy Rainfall Warning Thunderstorm Watch Flood Advisory Gale Warning</nav>"
        "<header>Research and Development Information</header>"
        "<main>Welcome to PAGASA</main>"
        "</body></html>"
    )

    async def _fake_fetch(*_args, **_kwargs):
        return _FakeResponse(nav_chrome)

    monkeypatch.setattr(pagasa, "fetch_with_retry", _fake_fetch)

    alerts = await pagasa.get_weather_alerts(region="some-unique-region-xyz")
    assert alerts["upstream_error"] is True
    assert alerts["data_status"] == "indeterminate"
    assert len(CACHES["pagasa_alerts"]) == 0


@pytest.mark.asyncio
async def test_get_weather_alerts_respects_no_active_warnings(monkeypatch):
    from ph_civic_data_mcp.sources import pagasa
    from ph_civic_data_mcp.utils.cache import CACHES

    CACHES["pagasa_alerts"].clear()

    class _FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text
            self.status_code = 200

        def raise_for_status(self) -> None:
            pass

    page = "<html><body>No Active Warnings</body></html>"

    async def _fake_fetch(*_args, **_kwargs):
        return _FakeResponse(page)

    monkeypatch.setattr(pagasa, "fetch_with_retry", _fake_fetch)
    alerts = await pagasa.get_weather_alerts(region="another-unique-region")
    assert alerts == []


# --- v0.7.0: a PSGC outage during location resolution must read as an -------
# --- upstream outage, never as "no coordinates known for this place" -------


@pytest.mark.asyncio
async def test_resolve_to_coords_raises_geo_resolve_error_on_psgc_outage(monkeypatch):
    """A PSGC transport failure must raise, not collapse to a silent None.

    Codex cross-model finding: `get_weather_forecast("QC")` used to answer
    "No coordinates known" while PSGC was down, even for a known alias.
    """
    from ph_civic_data_mcp.sources import psgc as psgc_module
    from ph_civic_data_mcp.utils.cache import CACHES
    from ph_civic_data_mcp.utils.geo import GeoResolveError, resolve_to_coords

    CACHES["psgc_resolve"].clear()

    async def _broken(query):
        raise ConnectionError("PSGC down")

    monkeypatch.setattr(psgc_module, "resolve_ph_location", _broken)

    with pytest.raises(GeoResolveError, match="PSGC down"):
        await resolve_to_coords("Marawi")


@pytest.mark.asyncio
async def test_get_weather_forecast_reports_upstream_error_on_psgc_outage(monkeypatch):
    """A PSGC outage must surface as upstream_error, not an unknown place."""
    from ph_civic_data_mcp.sources import pagasa
    from ph_civic_data_mcp.sources import psgc as psgc_module
    from ph_civic_data_mcp.utils.cache import CACHES

    monkeypatch.delenv("PAGASA_API_TOKEN", raising=False)
    CACHES["pagasa_forecast"].clear()
    CACHES["psgc_resolve"].clear()

    async def _broken(query):
        raise ConnectionError("PSGC down")

    monkeypatch.setattr(psgc_module, "resolve_ph_location", _broken)

    result = await pagasa.get_weather_forecast("Marawi", days=3)

    assert result["upstream_error"] is True
    assert result["days"] == []
    assert any("PSGC down" in c for c in result["caveats"])


@pytest.mark.asyncio
async def test_get_weather_forecast_manila_skips_psgc_call(monkeypatch):
    """A known city must resolve from CITY_COORDS and never reach PSGC."""
    from ph_civic_data_mcp.sources import pagasa
    from ph_civic_data_mcp.sources import psgc as psgc_module
    from ph_civic_data_mcp.utils.cache import CACHES

    monkeypatch.delenv("PAGASA_API_TOKEN", raising=False)
    CACHES["pagasa_forecast"].clear()

    async def _must_not_call(query):
        raise AssertionError("a known city must not call PSGC")

    monkeypatch.setattr(psgc_module, "resolve_ph_location", _must_not_call)

    async def _fake_forecast(location, lat, lng, days):
        return {
            "location": location,
            "days": [
                {
                    "date": "2026-09-05",
                    "temp_min_c": 24,
                    "temp_max_c": 31,
                    "rainfall_mm": 0,
                    "wind_speed_kph": 10,
                    "wind_direction": "NE",
                    "weather_description": "Partly cloudy",
                }
            ],
            "data_source": "open_meteo",
            "data_retrieved_at": "2026-09-04T00:00:00+00:00",
            "source": "Open-Meteo",
            "source_url": "https://api.open-meteo.com/v1/forecast",
            "license": "Open-Meteo CC-BY 4.0",
        }

    monkeypatch.setattr(pagasa, "_open_meteo_forecast", _fake_forecast)

    result = await pagasa.get_weather_forecast("Manila", days=3)
    assert result["location"] == "Manila"
    assert result["data_source"] == "open_meteo"
    assert len(result["days"]) == 1
