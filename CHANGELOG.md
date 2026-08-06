# Changelog

All notable changes to `ph-civic-data-mcp` are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-08-06

Opens the whole PSA OpenSTAT catalog, repairs two production defects, and
modernizes the toolchain. 29 -> 32 tools, 2 -> 3 prompts. No existing tool
name, parameter name, or successful response field changed.

### Fixed

- **PSA poverty discovery.** PSA moved Full-Year Poverty Statistics out of
  `DB/1E/FY`, which now returns 404. `get_poverty_stats` returned a
  discovery-failed `caveats` entry from early July, and five consecutive
  weekly live-drift runs failed on it. Discovery now walks the live catalog by
  title (root -> the Poverty subject -> the Full Year folder -> the table
  leaf), so a future renumber costs nothing. A live check on 2026-08-06
  returns 10.9 percent national poverty incidence for 2023 from
  `DB/1F/FY/0011F3DF010.px`.
- **A plain import registered 1 tool of 29.** Only `main()` called
  `_register_tools()`, so `fastmcp inspect`, `fastmcp dev inspector`, and any
  library import saw just `get_data_freshness`. The shared `FastMCP` instance
  moves to `_mcp.py` and `server.py` registers at import time. `inspect` now
  reports 32 tools, 3 prompts, 2 resources. `_register_tools()` stays public
  and idempotent, because release-smoke calls it on already published wheels.
- **`_browse` cached an OpenSTAT error as an empty list** for 24 hours, which
  contradicted the v0.5.0 no-error-cache contract. It now raises
  `PSAUpstreamError`, caches successes only, and treats an empty listing as a
  moved path. Every PSA tool catches it and returns `upstream_error` with the
  real error text.
- **`python -m build` never worked in a clean checkout.** `build` was not a
  dependency. The documented flow is now `uv build` plus `uvx twine check`,
  and CI runs both.
- **`ruff format --check` failed** on `docs/live_demo_v050.py`.
- **World Bank failures** now carry `upstream_error` and the real error text
  rather than a bare exception class name.

### Added

- **`browse_psa_catalog(path)`** walks the OpenSTAT hierarchy one level at a
  time. No argument lists the 27 subjects; an entry's `path` goes deeper.
- **`describe_psa_dataset(dataset_path)`** returns a `.px` dataset's
  dimensions, value codes, labels, time dimensions, and full-cube cell count.
- **`query_psa_dataset(dataset_path, selections, max_rows)`** runs one bounded
  query and returns normalized rows with codes, labels, and numeric or null
  values.
- **`psa_data_explorer` prompt** drives browse -> describe -> query and tells
  an agent to read the vintage from the table's own time dimension.
- **A token-bucket rate limiter** on every OpenSTAT request, holding the
  10-per-10-seconds cap PSA publishes. A bucket, not a sliding window: a cold
  `get_area_profile` makes about 11 OpenSTAT calls in one `asyncio.gather`, and
  a strict window stalled that whole fan-out for a full 10 seconds. The bucket
  keeps the same sustained rate and lets the burst through, so the same cold
  profile costs about 1 second. A 429 now backs off past the window and honors
  `Retry-After`.
- **MCP metadata on every tool:** a human-readable title, domain tags, and
  annotations. All 32 are read-only and idempotent; the 31 that call an
  upstream declare `openWorldHint`. The three new tools also declare output
  schemas and timeouts.
- **Server metadata:** `version` from `__version__` and `website_url`.
- **`docs/tool-reference.md`** holds the full 32-tool reference.

### Security

The three catalog tools take a generic path and a generic query, so they carry
explicit limits:

- The tool rebuilds every path under the configured OpenSTAT base. It rejects
  a scheme, a host, a query string, a fragment, `..`, an empty segment, a
  control character, and an unexpected character before any request.
- Every dimension needs an explicit list of value codes. PXWeb expands an
  unnamed dimension to all of its values, and PSA answers the resulting
  full-cube request with an HTTP 403 from its WAF.
- The tool refuses `"all"` and `"*"` for the same reason.
- The tool computes the cell product before the POST, and caps it at 1000.
- The tool clamps `max_rows` to 1..5000.
- A caller mistake returns `validation_error: true`, distinct from
  `upstream_error: true`. Neither is cached.

`pip-audit` reported 14 known vulnerabilities across 9 transitive packages on
the v0.5.0 lockfile. The lockfile now resolves clean: `click` 8.3.2 -> 8.4.2,
`cryptography` 46.0.7 -> 50.0.0, `idna` 3.11 -> 3.18, `pyjwt` 2.12.1 -> 2.13.0,
`python-multipart` 0.0.26 -> 0.0.32, `soupsieve` 2.8.3 -> 2.9.1, `certifi`
2026.2.25 -> 2026.7.22, `joserfc` 1.6.4 -> 1.7.4, `mcp` 1.27.0 -> 1.29.0, and
`pydantic-settings` 2.13.1 -> 2.14.2. No direct dependency floor moved, and the
suite passes on Python 3.11 through 3.14 after the bump.

### Changed

- FastMCP 3.2.4 -> 3.4.6, inside the existing `>=3.0.0,<4.0.0` pin. No other
  dependency floor moved.
- Python 3.13 and 3.14 gain classifiers and CI legs. The minimum stays 3.11.
- GitHub Actions pins move off Node20-era versions: `checkout` v4 -> v7,
  `setup-python` v5 -> v7, `setup-uv` v3 -> v9.
- CI runs four Python versions with `uv sync --locked`, adds a build job with
  `uv lock --check`, `uv build`, `twine check`, and a wheel-contents check,
  and asserts a bare import exposes 32 tools, 3 prompts, and 2 resources.
- release-smoke asserts the same on the published wheel, runs a real stdio
  round trip, and checks the catalog path guards.
- README is now a landing page. The tool table lives in
  `docs/tool-reference.md`, and per-version essays live here.
- `docs/SUBMISSIONS.md` rewritten against a live directory inventory; it
  described 12 tools.
- The `test_world_bank_raw_code` live test skips on an upstream outage rather
  than failing on one. It still asserts shape when data arrives.
- `docs/latent-bugs.md` records eight pre-existing fail-soft paths that two
  independent reviews of this branch surfaced. They predate v0.6.0 and are
  logged rather than bundled into a release.

## [0.5.0] — 2026-06-11

Reliability + agent-UX pass driven by a full 8-dimension product audit of the
shipped v0.4.0. Tool count stays at 29. One behavior change agents will
notice: list tools no longer return a bare `[]` when their upstream is down.

### Changed

- **Failure envelopes on list tools.** `get_latest_earthquakes`,
  `get_volcano_status`, `get_active_typhoons`, `get_weather_alerts`,
  `search_procurement`, `search_infra_projects`, `get_usgs_earthquakes_ph`,
  `get_historical_typhoons_ph`, and `list_admin_units` now return
  `{results: [], upstream_error: true, caveats: [...]}` on upstream failure
  instead of an empty list, so an outage can never be read as "no
  earthquakes / no typhoons / no notices". Success responses keep their
  original list shape. Aggregate tools (`get_procurement_summary`,
  `summarize_infra_spending`) gain `caveats` + `upstream_error` fields.
- **Errors are never cached.** Transient upstream failures previously
  poisoned TTL caches for the full success window (PhilGEPS: 6h of empty
  notices; PSGC: 24h of "no match", which also dropped the PSA blocks from
  `get_area_profile`; PSA `_err` envelopes: 24h of nulls). All error paths
  now bypass the caches.
- **`flag_infra_anomalies` rule renamed**: `high_cost_no_progress` →
  `high_cost_no_published_progress`, with an evidence string that states
  exactly what was checked. The PhilGEPS open listing publishes no progress
  data for any notice, so the old name implied a per-project check that
  never happened.
- `get_data_freshness` reports the real registered tool count (previously a
  `len(SOURCE_CATALOG) * 2` estimate); server instructions rewritten for
  v0.5.0 with tool-choice guidance and the failure-envelope contract.
- Per-volcano bulletin fetches in `get_volcano_status` run in parallel.
- Error caveats include the exception message, not just the class name.

### Added

- **PSGC nickname aliases** in `resolve_ph_location`: QC, Gensan, CDO,
  Zambo, BGC, Metro Manila, MM, CAR, CALABARZON, MIMAROPA, SOCCSKSARGEN,
  Caraga, Bicol, and more.
- **"X City" ↔ "City of X" bridging** in the resolver. PSGC names cities
  "City of Manila" while people write "Manila City"; the fuzzy scorer could
  not bridge that and "Manila City" actually resolved to Danao City (Cebu)
  at score 0.61 in v0.4.0 — despite the tool's own docstring recommending
  that exact query. Both forms now resolve at score 1.0.
- **`alternatives` field** on resolve results: ambiguous names ("San Juan")
  return runner-up candidates with PSGC codes, regions, and match scores.
- **`offset` parameter** on `list_admin_units` — page past the 500 cap
  (Manila has 897 barangays).
- **Volcano alerts in both composites**: `assess_area_risk` and
  `get_area_profile` include `volcano_alerts` (alert level >= 1, national
  scope, explicitly labeled).
- **MCP resources**: `data://ph-civic/source-catalog`,
  `data://ph-civic/civic-framing`.
- **MCP prompts**: `area_briefing(location)`,
  `infra_accountability_scan(area)`.
- **Weekly live-drift CI workflow** (`.github/workflows/live-drift.yml`)
  runs the `live`-marked suite against real upstreams every Monday so
  scraper rot surfaces in Actions, not user reports.
- Offline regression suite `tests/test_v050_fixes.py` (envelopes, negative-
  cache elimination, aliases, alternatives, pagination, allowlist, volcano
  stitch, real tool_count, resources/prompts).

### Fixed

- **SSRF-shaped gap**: `get_earthquake_bulletin` fetched any agent-supplied
  URL through the SSL-relaxed PHIVOLCS client with only a
  `startswith("http")` check. Now allowlisted to `*.phivolcs.dost.gov.ph`.
- Unguarded `date_parser.parse` in `flag_infra_anomalies`' hazard-input
  summary could crash the whole tool on one malformed upstream datetime.
- Seven live-hitting test modules ran inside the "no live HTTP" CI gate
  (`pytest -x` + transient outage = red CI). All are now `live`-marked.
- `manifest.json` and README said "25 tools" (actual: 29).
- README air-quality city count corrected (~70, was "~80").
- Dockerfile runs as a non-root user and is now documented in the README.
- Version is single-sourced from `__init__.py` (hatch dynamic version);
  the HTTP User-Agent derives from it instead of a hardcoded string.

### Removed

- Unused `openpyxl` dependency (a leftover from the original xlsx plan that
  PhilGEPS never shipped).

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
