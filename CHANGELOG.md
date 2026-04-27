# Changelog

All notable changes to `ph-civic-data-mcp` are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] — 2026-04-27

### Added

- **PSGC backbone** (`sources/psgc.py`):
  - `resolve_ph_location(query)` — fuzzy free-text place name -> canonical PSGC record
    with comma-segment and abbreviation handling (Sta., Sto., Brgy.).
  - `list_admin_units(parent_code, level, limit)` — browse children of any PSGC node.
  - `get_location_hierarchy(psgc_code)` — full chain region -> province -> city/municipality.
- **Infra spending source** (`sources/infra.py`, backed by PhilGEPS open notice listing):
  - `search_infra_projects(keyword, region, province, year, min_cost_php, status, limit)`.
  - `get_infra_project(project_id)`.
  - `summarize_infra_spending(region, year, funding_source)` with category, region,
    agency, and reference-period aggregations and a "Statistical indicators..." disclaimer.
- **Cross-source anomaly flagger** (`sources/cross_source.py::flag_infra_anomalies`):
  - Heuristic indicators only. Three rules: `high_cost_no_progress`, `hazard_overlap`,
    `duplicate_titles_same_agency`. Cross-references PHIVOLCS earthquakes (>=M4.0
    in last 30d) and active PAGASA typhoon footprints.
  - Every flagged item ships with the rule fired, evidence, source_url, and the
    "Statistical indicators derived from public data. Patterns may have legitimate
    explanations." disclaimer.
- **Data freshness catalog** (`server.py::get_data_freshness`) — listing every upstream
  source, its source_url, freshness expectation, cache TTL, and license.
- **Pydantic models** for new domains: `models/location.py` (`PSGCRecord`, `PSGCHierarchy`,
  `PSGCHierarchyLevel`); `models/infra.py` (`InfraProject`, `InfraSpendingSummary`).
- **Caches**: `psgc_resolve` (24h, maxsize 200), `psgc_browse` (24h, 200),
  `infra_projects` (6h, 50).
- **Tests**: `tests/test_psgc.py`, `tests/test_infra.py`, `tests/test_v030_cross_source.py`
  using `httpx.MockTransport` for upstream isolation; `tests/test_v030_live.py` opt-in
  live smoke tests gated by `@pytest.mark.live`.
- **Live probe**: `docs/live_probe_v030.py` captures real JSON outputs for the README.
- **Demo**: `docs/live_demo_v030.py` and `docs/demo_accountability.tape` (Catppuccin
  Mocha, 1600x900, FontSize 18). Tape drives a real `claude -p --mcp-config` call that
  exercises `resolve_ph_location` + `search_infra_projects` + `flag_infra_anomalies`
  in a single agent turn.

### Changed

- **Geo resolver** (`utils/geo.py`) — added async `resolve_to_coords(query)` that
  consults the PSGC source first to canonicalise free-text input ("Sta. Mesa, Manila"
  -> "Manila"), then looks up `CITY_COORDS`. PSGC does not currently publish lat/lng,
  so `CITY_COORDS` remains the authoritative coordinate table; the new path provides
  a network-aware enhancement on top of the existing sync `city_to_coords` fallback.
- **PAGASA forecast tool** (`sources/pagasa.py::get_weather_forecast`) — uses the
  new async resolver primary path, falls back to the sync `city_to_coords` lookup,
  and now emits `source_url` and `license` fields when no coordinates are known.
- **Cross-source `assess_area_risk`** — now emits `source_url`, `license`, and the
  standard disclaimer string for parity with v0.3.0 conventions.
- **Server description** in `pyproject.toml` and `server.json` (kept under the MCP
  Registry's 100-character limit) updated to mention the v0.3.0 capabilities.
- **Tool count**: 17 -> 25 across all sources.

### Fixed

- `_classify_level` in PSGC now prefers the upstream API's `type` field over
  9-digit code structure when present, correctly identifying NCR cities (whose
  codes use the province-code slot) as cities rather than provinces.
- Linter cleanups across the tree: removed unused imports in `psa.py`, `philgeps.py`,
  `infra.py`, and the `tests/` directory; bumped `User-Agent` to `0.3.0`.

### Sources added

- [PSGC API mirror (psgc.gitlab.io)](https://psgc.gitlab.io/api/) — community mirror
  of the PSA Philippine Standard Geographic Code dataset. Public domain.

### Notes

- The DPWH Transparency portal (`transparency.dpwh.gov.ph`,
  `api.transparency.dpwh.gov.ph`) is currently behind a Cloudflare bot challenge
  that returns 403 to every non-browser client regardless of User-Agent. Direct
  integration is deferred; v0.3.0 sources its infra layer from the open PhilGEPS
  listing instead. `sources/infra.py` is the single integration point to swap in
  when DPWH lifts the block.

## [0.2.0] — 2026-04-19

### Added

- Six no-auth scientific and open-data sources: NASA POWER (solar irradiance +
  daily climate), Open-Meteo Air Quality (PM2.5/PM10/NO2/SO2/O3/CO + AQI),
  NASA MODIS via ORNL DAAC (NDVI/EVI vegetation indices), USGS FDSN (PH-bbox
  earthquakes from the global seismic network), NOAA IBTrACS (historical
  tropical cyclone tracks through the PAR), and World Bank Open Data
  (Philippine macro indicators).
- Tool count grew from 11 to 17.
- `docs/demo_correlation.tape` recording a real multi-source correlation turn.

### Changed

- AQICN dropped entirely (PH stations were dark, not useful) and replaced with
  Open-Meteo Air Quality.

## [0.1.x]

- Initial release line. Four Philippine government sources: PHIVOLCS, PAGASA,
  PhilGEPS, PSA. 11 tools.
