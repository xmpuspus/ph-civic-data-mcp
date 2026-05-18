# Changelog

All notable changes to `ph-civic-data-mcp` are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] — 2026-05-18

Additive minor on the shipped public package. Expands the PSA OpenSTAT layer
into the live economy and adds the cross-source auto-stitch context tool. No
existing tool changed; the 25 v0.3.1 tools are retained. Tool count 25 -> 29.

### Added

- **PSA economy tools** (`sources/psa.py`), all via the existing
  browse-discovery convention (stable subject path fixed, `.px` leaf
  discovered by text predicate — never a hardcoded table id):
  - `get_inflation_stats(area)` — headline year-on-year CPI inflation
    (2018-based), national or by region. Among predicate-matching tables it
    selects the one whose time dimension reaches the most recent year, so the
    current series is returned instead of a backcasted era table. Reports the
    exact published reference period (PSA publishes monthly with a lag) and
    treats the literal `".."` missing-value sentinel as null.
  - `get_labor_stats(region)` — Labor Force Survey key rates (labor-force
    participation, employment, unemployment, underemployment). National only;
    a `region` argument is recorded as an explicit caveat.
  - `get_health_indicators(indicator)` — national health indicators
    (maternal mortality ratio, total fertility rate); full available set is
    browse-discovered.
- **Auto-stitch context layer** (`sources/autostitch.py::get_area_profile`):
  resolves a place to its PSGC code once, then composes demographics,
  economy, procurement, multi-hazard risk, and weather in a single agent turn
  via `asyncio.gather` (the proven `cross_source.py` pattern). Adds derived
  cross-source normalization (infra notices per 100k population). Each block
  carries its own reference period; failed upstreams are named in `caveats`
  and the rest of the profile still returns; ships the public-data disclaimer.
- **Models** (`models/psa.py`): `InflationStats`, `LaborStats`,
  `HealthIndicator` (internal validation; returned as `model_dump`).
- **Caches** (`utils/cache.py`): `psa_prices`, `psa_labor`, `psa_health` (24h),
  `psa_browse` (24h), `area_profile` (1h).
- **Tests**: `tests/test_psa_expansion.py` (8), `tests/test_autostitch.py` (4),
  live integration style mirroring `tests/test_phivolcs.py`.
- **SOURCE_CATALOG** rows for the PSA economy expansion and the area-profile
  composition; PSA freshness note rewritten as per-table vintage.
- **Demos** (real recordings): `docs/live_demo_v040.py` +
  `docs/demo_v040_sources.tape` -> `docs/demo_v040.gif` (per-source Rich tour
  over real MCP stdio); `docs/demo_v040_hero.tape` -> `docs/demo_v040_hero.gif`
  (one real `claude -p` turn comparing Eastern vs Central Visayas via two
  `get_area_profile` calls and correlating them into a flagged-for-review read).
  README gains a dedicated `## Changelog` section.

### Changed

- `server.py` registers the new `autostitch` module; `SOURCE_CATALOG` PSA row
  no longer claims a blanket vintage.
- `server.json` / `pyproject.toml` descriptions updated for v0.4.0 (server.json
  kept under the MCP Registry 100-character limit); version bumped to 0.4.0 in
  `__init__.py`, `pyproject.toml`, `server.json`, `manifest.json`, and the
  HTTP `User-Agent`.

### Fixed

- `tests/test_v030_live.py::test_live_data_freshness` asserted a hardcoded
  `server_version == "0.3.0"` and had been red since v0.3.1; repinned to the
  package `__version__` so it tracks releases.
- `get_inflation_stats` / `get_labor_stats` error envelopes now include their
  value keys as `null` so success and error responses share one shape.

### Notes

- PSA PXWeb verified live 2026-05-18 across all 23 subject databases: the API
  path is open and zero-key (the `/database` website is Cloudflare-walled);
  full-cube `filter:"all"` requests are WAF-403'd, so every query selects
  explicit item codes and stays well under the per-query cell cap; the
  response `updated` field is server generation time, not data recency.
- One pre-existing, out-of-scope test failure remains:
  `test_modis_ndvi_returns_composites` depends on NASA/ORNL MODIS
  availability and intermittently returns zero composites. It is unrelated to
  this change and fails identically on `main`.

## [0.3.1] — 2026-05-01

Correctness pass driven by the 2026-05-01 product audit. No new tools; two flagship
v0.3.0 tools were producing fabricated-feeling output and the PSGC -> coordinate
bridge silently failed for natural Manila phrasings.

### Fixed

- **`get_weather_alerts` no longer fabricates advisories** (`sources/pagasa.py`).
  The previous regex matched alert names ("Heavy Rainfall Warning",
  "Flood Advisory", "Gale Warning") wherever they appeared on the PAGASA
  homepage including the navigation menu, breadcrumbs, and footer. Until we
  have a structural way to isolate the active-warnings section, the tool now
  returns `[]` when the page is reachable but the state is ambiguous, and `[]`
  with the explicit "No Active Warnings" signal when the homepage says so. The
  conservative empty matches `assess_area_risk`'s embedding semantics. For
  real-time advisories, callers should hit `bagong.pagasa.dost.gov.ph`
  directly.
- **`flag_infra_anomalies` hazard_overlap stops firing on stoplist tokens**
  (`sources/cross_source.py`). The previous implementation extracted every
  alpha word >=4 chars from earthquake `location` strings into the hazard
  keyword set, so generic words like "city" and "surigao" matched any project
  title containing them. The new `_proper_noun_tokens` helper requires
  capitalisation in the source string and applies an explicit stoplist of
  geographic chrome (`city`, `region`, `province`, `eastern`, ...). The
  README's accountability demo no longer needs an apologetic caveat about
  matches on `['city']`.
- **`city_to_coords` handles "City of Manila" / "Sta. Mesa, Manila"**
  (`utils/geo.py`). PSGC returns names in the inverted form ("City of Manila",
  "Municipality of Tagaytay") but the previous resolver only stripped a
  trailing ` city` suffix. Now strips `city of ` and `municipality of `
  prefixes and walks comma-segments, so the v0.3.0 `resolve_ph_location` ->
  `get_weather_forecast` chain works for the most natural Manila inputs.
- **`search_infra_projects(province=...)` expands via region/agency hints**
  (`sources/infra.py`). The new `_PROVINCE_AGENCY_HINTS` map widens substring
  match so "Pampanga" also catches DPWH agency names like "DEPARTMENT OF
  PUBLIC WORKS AND HIGHWAYS - REGION III". Covers all 81 PH provinces plus
  NCR.
- **`get_volcano_status` not-found branch** now emits `source_url`, `license`,
  and `caveats` for envelope parity with the rest of v0.3.0
  (`sources/phivolcs.py`).
- **urllib3 `InsecureRequestWarning` suppressed** for the dedicated
  `PHIVOLCS_CLIENT` (`utils/http.py`). The verify-disabled scope is unchanged;
  only the per-request stderr warning is silenced so MCP clients that render
  server stderr don't surface a TLS warning every call.

### Changed

- **Server `instructions` block** now anchors civic-tech framing every turn:
  agents are explicitly instructed to use defensible language ("flagged for
  review", "warrants further investigation"), never accusations, and to cite
  `source_url` for every factual claim.
- **`get_data_freshness` doubles as health/version probe.** Docstring updated
  to document this; response now includes `server_name` and `transport`.
- **README hero** gains one-click install badges (Cursor / VS Code /
  Smithery / Claude Code) above the existing badge row.
- **`manifest.json`** added at repo root for Claude Desktop `.mcpb` packaging.
  Run `npx @anthropic-ai/mcpb pack` to produce a one-double-click installer.
- **GitHub Actions workflows** added: `ci.yml` (lint + tests on push/PR) and
  `release-smoke.yml` (fresh-venv install of the published wheel + tool
  registration + offline regression checks for the v0.3.1 fixes).

### Tests

- `tests/test_v031_fixes.py` — 13 new regression tests pinning each fix.

### Notes

- DPWH Transparency portal status was re-verified on 2026-05-01: still behind
  a Cloudflare bot challenge (`HTTP 403, "Just a moment..."`). PhilGEPS
  remains the source of record for infra-spending tools. The single
  integration point in `sources/infra.py` is unchanged and ready to swap in
  when DPWH lifts the block.

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
