# ph-civic-data-mcp

<!-- mcp-name: io.github.xmpuspus/ph-civic-data-mcp -->

> Philippine civic data as agent-callable tools. The full PSA OpenSTAT
> statistical catalog, PSGC location codes, infra-spending accountability,
> earthquakes, weather, typhoons, procurement, poverty, solar radiation, air
> quality, satellite vegetation, and macro indicators. Population figures
> reach barangay level. 41 tools, no API keys.

[![PyPI](https://img.shields.io/pypi/v/ph-civic-data-mcp.svg)](https://pypi.org/project/ph-civic-data-mcp/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Glama AAA](https://glama.ai/mcp/servers/xmpuspus/ph-civic-data-mcp/badges/score.svg)](https://glama.ai/mcp/servers/xmpuspus/ph-civic-data-mcp)
[![MCP Registry](https://img.shields.io/badge/MCP%20Registry-io.github.xmpuspus%2Fph--civic--data--mcp-blue)](https://registry.modelcontextprotocol.io/v0.1/servers?search=ph-civic-data-mcp)

Philippine civic-data portals publish open data in different shapes: scraped
HTML tables, PXWeb JSON, and undocumented APIs. Nothing ties them together for
an agent to use. This server does, over stdio, with zero hosting cost and no
API key needed. It answers questions such as how many people live in a
barangay, whether a place sits near an active fault or volcano, what a city
spent on flood control, and how one place compares against another.

All data comes from public records. Heuristic indicators are statistical
only. A specific allegation needs independent investigation and a second
source.

## Install

Every client below runs the same package, `uvx ph-civic-data-mcp`, over
stdio.

[![Add to Cursor](https://img.shields.io/badge/Add%20to-Cursor-000000?logo=cursor)](cursor://anysphere.cursor-deeplink/mcp/install?name=ph-civic-data&config=eyJjb21tYW5kIjogInV2eCIsICJhcmdzIjogWyJwaC1jaXZpYy1kYXRhLW1jcCJdfQ==)
[![Add to VS Code](https://img.shields.io/badge/Add%20to-VS%20Code-007ACC?logo=visualstudiocode)](https://insiders.vscode.dev/redirect/mcp/install?name=ph-civic-data&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22ph-civic-data-mcp%22%5D%7D)
[![Install via Smithery](https://img.shields.io/badge/Install%20via-Smithery-blueviolet)](https://smithery.ai/server/ph-civic-data-mcp)
[![Add via Claude Code](https://img.shields.io/badge/Add%20via-Claude%20Code-D97757?logo=anthropic)](https://code.claude.com/docs/en/mcp)

**Claude Desktop.** Add this to `claude_desktop_config.json`, which sits at
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS:

```json
{
  "mcpServers": {
    "ph-civic-data": {
      "command": "uvx",
      "args": ["ph-civic-data-mcp"]
    }
  }
}
```

**Claude Code.**

```bash
claude mcp add ph-civic-data -- uvx ph-civic-data-mcp
```

**Codex.** Confirmed live on 2026-09-03.

```bash
codex mcp add ph-civic-data -- uvx ph-civic-data-mcp
```

**Cursor.** Add this to `mcp.json`:

```json
{
  "mcpServers": {
    "ph-civic-data": {
      "command": "uvx",
      "args": ["ph-civic-data-mcp"]
    }
  }
}
```

**VS Code.** Add this to `.vscode/mcp.json`:

```json
{
  "servers": {
    "ph-civic-data": {
      "type": "stdio",
      "command": "uvx",
      "args": ["ph-civic-data-mcp"]
    }
  }
}
```

**Docker.** Build once, then run with `-i` for stdio:

```bash
docker build -t ph-civic-data-mcp .
```

The image runs as a non-root user and ships a healthcheck.

![setup](docs/demo_setup.gif)

That recording is `vhs docs/demo_setup.tape`. It spawns Claude Code with
`--mcp-config` pointed at this server, and Claude fans out to
`get_weather_forecast` and `get_population_stats`, then correlates them. The
temperatures and the population are what the live sources returned while it
recorded. The population turn answers from the 2024 Census of Population
(NCR: 14,001,751 as of 1 July 2024).

## What can I ask?

`ph-civic-data-mcp` exposes 41 tools across 19 public sources. Start with
`get_area_profile` for any place-based question. It resolves the name to a
PSGC code once, then composes demographics, economy, procurement, hazards,
and the 3-day outlook in a single turn, with infra notices already normalized
per 100,000 residents.

### Get a place at a glance

- "Give me a profile of Tacloban." `get_area_profile`
- "What is the PSGC code for QC?" `resolve_ph_location`
- "How many people live in Zamboanga City?" `get_population_stats`
- "Is it safe in Albay right now?" `assess_area_risk`

### Compare two or more places

- "Compare Cebu City and Davao City on population and poverty." `compare_areas`
- "How does Zamboanga's employment rate compare to Cagayan de Oro's?" `compare_areas`
- "Export a five-city comparison as a CSV file." `compare_areas`

### Check hazards near a place

- "Any earthquakes near Legazpi in the last day?" `get_latest_earthquakes`
- "Read the full PHIVOLCS bulletin for that quake." `get_earthquake_bulletin`
- "What is Mayon's current alert level?" `get_volcano_status`
- "Is a typhoon active in the Philippine area right now?" `get_active_typhoons`
- "Cross-check that quake against the USGS global feed." `get_usgs_earthquakes_ph`
- "What is the river flood outlook for Cagayan de Oro this week?" `get_flood_forecast`

### Search procurement and spending

- "Search PhilGEPS for flood control projects in Pampanga." `search_infra_projects`
- "Summarize infra spending in Bicol for 2025." `summarize_infra_spending`
- "Flag PhilGEPS notices in Cebu that warrant a closer look." `flag_infra_anomalies`
- "Pull the full notice for one flagged project." `get_infra_project`

### Query the PSA statistical catalog

- "Find PSA tables that mention fertility." `search_psa_catalog`
- "Walk me through the poverty subject on OpenSTAT." `browse_psa_catalog`
- "What dimensions does this poverty table have?" `describe_psa_dataset`
- "Pull poverty incidence by island group for 2023." `query_psa_dataset`
- "What is the current inflation rate?" `get_inflation_stats`
- "What PSIC code covers rice farming?" `search_psic_codes`

`query_psa_dataset` needs an explicit value code for every dimension, refuses
`"all"` and `"*"`, and caps a query at 1000 cells. PSA answers a full-cube
request with an HTTP 403, so `describe_psa_dataset` first is the only way in.

### Check weather and environment

- "What is the 5-day forecast for Iloilo?" `get_weather_forecast`
- "Any weather alerts active in Bicol?" `get_weather_alerts`
- "How much solar radiation does Palawan get?" `get_solar_and_climate`
- "What is today's air quality in Manila?" `get_air_quality`
- "How has Mindanao's vegetation changed this year?" `get_vegetation_index`
- "List the latest PAGASA weather advisories." `list_pagasa_advisory_files`

### Read the 2025 election results

- "Walk the election tree down to precincts in Adams, Ilocos Norte." `browse_election_results`
- "Show me the vote tally for precinct 28010001." `get_election_return`

The archive froze on 2025-05-16, so these two read a fixed public record.
The tools retrieve and never interpret.

### Track new laws and find open datasets

- "What did the Official Gazette publish this week?" `get_official_gazette_feed`
- "Find Philippine flood datasets on HDX." `search_hdx_datasets`

Every HDX dataset carries its own license. Read `license_id` before reuse.

Full signatures, arguments, and limits for all 41 tools:
**[docs/tool-reference.md](docs/tool-reference.md)**.

## Get a full profile for one place

`get_area_profile("Tacloban")` returns the resolved identity, then reports
Tacloban's own population next to the national figure, not the region's.

```json
{
 "resolved": {
  "name": "City of Tacloban",
  "psgc_code": "083747000",
  "level": "city"
 },
 "demographics": {
  "population": 259353,
  "population_year": 2024,
  "population_census": "2024 Census of Population",
  "population_reference": "PSA 2024 Census of Population, reference date 2024-07-01.",
  "population_geography_level": "highly_urbanized_city",
  "population_psgc_code": "0831600000",
  "poverty_incidence_pct": 20.6,
  "poverty_reference_year": 2023
 },
 "national_reference": {
  "population": 112729484,
  "population_year": 2024,
  "poverty_incidence_pct": 10.9,
  "poverty_year": 2023,
  "population_share_pct": 0.23,
  "poverty_gap_pct_points": 9.7
 },
 "blocks": {
  "resolve": "success",
  "population": "success",
  "poverty": "success",
  "hazard": "success",
  "weather": "success",
  "national_population": "success",
  "national_poverty": "success",
  "infra": "success"
 },
 "upstream_error": false,
 "caveats": []
}
```

Captured live on 2026-09-03. Tacloban's own population, 259,353, replaces the
Region VIII figure of about 4.6 million that an earlier version reported.
Every demographic field names its own census, reference date, and geography
level, so an agent never has to guess which population a number belongs to.

## An outage returns an envelope, never an empty list

A list tool returns a real list on success. On upstream failure it returns an
envelope instead:

```json
{ "results": [], "upstream_error": true, "caveats": ["ConnectError: ..."] }
```

Read that as "the source was unreachable," never as "no earthquakes" or "no
notices." Failures never enter a cache, so a retry is meaningful, and a
`caveats` entry carries the real error rather than an exception class name.

Every single-value tool sets `data_status` to one of five values:

| `data_status` | Meaning |
|---|---|
| `success` | The source returned a value, with its provenance. |
| `empty` | The source answered but has no row for this request. |
| `unavailable` | The source failed to respond, or sent an unreadable body. |
| `indeterminate` | The source answered, but the server cannot trust the result. |
| `invalid_request` | The caller sent a bad argument. Fix the argument named in `caveats`. |

`upstream_error` and `validation_error` derive from `data_status`, so a
caller can branch on either field. `get_area_profile` reports one status per
block in `blocks` and folds every failed block into `caveats`, so a null
figure never sits beside an empty `caveats` list.

Every response carries `source` and `data_retrieved_at`.

## Sources and freshness

The table below comes straight from `SOURCE_CATALOG` in `server.py`, through
`scripts/render_source_matrix.py`, so it cannot drift from what the server
actually reports.

<!-- source-matrix:start -->
| Source | What it gives | Freshness | Cache TTL | License |
|---|---|---|---|---|
| PSGC | Place codes and names, region down to barangay | Updated when PSA publishes new PSGC version (annual or quarterly) | 24 h | Public domain (PSA Philippine Standard Geographic Code) |
| PHIVOLCS earthquakes | Earthquake events and full bulletins | 5-minute table refresh; bulletins published per event | 5 min | Public, PHIVOLCS public bulletin pages |
| PHIVOLCS volcanoes | Alert level and bulletin per monitored volcano | Daily bulletins per active volcano | 30 min | Public, PHIVOLCS public bulletin pages |
| PAGASA forecast | 10-day weather forecast, with an Open-Meteo fallback | Issued twice daily; Open-Meteo updates hourly | 1 h | Open-Meteo CC-BY 4.0 / PAGASA terms |
| PAGASA typhoons | Active typhoon bulletins and weather alerts | Bulletin every 3-6 hours when storms are active | 10 min | Public, PAGASA bulletin pages |
| PhilGEPS notices / infra | Procurement notices, the infra subset, spending summaries | Latest ~100 bid notices, refreshed every 6h | 6 h | Public, PhilGEPS open notice listing |
| PSA OpenSTAT | Population, poverty, CPI, labor, health, and the full statistical catalog | Per-table vintage. Population: 2024 Census of Population (reference date 2024-07-01), with 2010, 2015 and 2020 by year. Poverty: 2023. CPI/inflation: latest published month (lagged). Labor Force Survey: latest published quarter. Health (1D): per-indicator. | 24 h | PSA Open Data terms |
| Area profile (auto-stitch) | One place profile composed live from every source below | Composed live from PSGC + PSA + PhilGEPS + PHIVOLCS + PAGASA; each block carries its own reference period | 1 h | Public, PSA OpenSTAT, PSGC, PhilGEPS, PHIVOLCS, PAGASA |
| NASA POWER | Daily solar irradiance and climate at any point | Daily, ~3-day latency | 24 h | Public domain (NASA) |
| Open-Meteo air quality | PM2.5, PM10, NO2, SO2, O3, CO, and AQI | Hourly | 15 min | Open-Meteo CC-BY 4.0 |
| Open-Meteo flood forecast | Daily river discharge forecast (GloFAS model) for the nearest river cell | Daily GloFAS model run | 1 h | Open-Meteo CC-BY 4.0 |
| NASA MODIS NDVI | NDVI and EVI vegetation indices at any point | 16-day composite, ~14-day latency | 24 h | Public domain (NASA / ORNL) |
| USGS FDSN | Philippine-region earthquakes, cross-checked against PHIVOLCS | Real-time global feed | 10 min | Public domain (USGS) |
| NOAA IBTrACS | Historical tropical cyclone tracks through the Philippine AOR | Annual update | 24 h | Public domain (NOAA) |
| World Bank Open Data | Philippine macroeconomic indicators | Annual; lag varies by indicator | 24 h | World Bank Open Data CC-BY 4.0 |
| HDX | Humanitarian dataset search, with a per-dataset license | Per-dataset metadata_modified; the catalog is searched fresh each query | 6 h | HDX (Humanitarian Data Exchange) CKAN API, per-dataset license |
| Official Gazette RSS | Proclamations, memorandum circulars, and other government issuances | New issuances posted the same day; feed rebuilds on every request | 20 min | Public, Official Gazette government record, RA 8293 section 176 default |
| PAGASA public files | Raw advisory, bulletin, and storm surge PDF file listing | weather_advisory updates about every 6 hours; bulletin only while a cyclone is active; stormsurge has not published since 2019-12-02 | 15 min | PAGASA public files (pubfiles.pagasa.dost.gov.ph), government record |
| PSIC | Industrial classification code lookup, by code prefix or description | PSIC revisions change on the order of years | 24 h | PSA Philippine Standard Industrial Classification (PSIC), CC BY 4.0 |
| COMELEC 2025 election results | Precinct-level vote tallies, region down to barangay | Archive frozen 2025-05-16 10:00:09 AM; a fixed public record, not a live feed | 24 h | Public, COMELEC 2025 election results archive |
<!-- source-matrix:end -->

`PAGASA_API_TOKEN` is the only environment variable, and it is optional.
PAGASA gates it behind a formal request. Without it, forecasts use
Open-Meteo. Every one of the 41 tools works with no token at all.

Three vintages worth stating plainly:

- **Population reads the 2024 Census of Population by default**, down to
  barangay level by `psgc_code`. PSA moved the census folders on OpenSTAT in
  2026, so the server discovers them by title on every cold start, and names
  the census, reference date, and geography level in every result. Pass
  `year` for 2010, 2015, or 2020, and `psgc_code` for a city, municipality,
  or barangay.
- **Poverty is 2023 Full Year.** PSA publishes it every three years.
- **Procurement is not real time.** The public portal exposes no filterable
  API, so this server reads the latest ~100 notices and filters locally. A
  per-100,000 rate needs at least 500 notices in the sample, so
  `get_area_profile` withholds that figure below the threshold and names the
  reason in `caveats`.

The OpenSTAT `updated` field is server wall clock, not data vintage. Read the
vintage from the table's own time dimension, which every response reports.

## A flagged notice is a starting point, never evidence

`flag_infra_anomalies`, `summarize_infra_spending`, and the procurement
search produce starting points for investigation, never evidence of
wrongdoing. Every flagged item ships with a disclaimer, and the server
instructs agents to use defensible language.

`high_cost_no_published_progress` is named for what it actually checks: the
public listing publishes no progress data for any notice, so it is a
cost-threshold transparency flag, not a per-project progress check.

**For an emergency, use [ndrrmc.gov.ph](https://ndrrmc.gov.ph) and the
official PHIVOLCS and PAGASA channels.** This is not a life-safety system but
a research tool.

## Development

```bash
git clone https://github.com/xmpuspus/ph-civic-data-mcp
cd ph-civic-data-mcp
uv sync --extra dev

# Offline tests, exactly what CI runs
uv run pytest -m "not live"

# Live tests against real upstreams; the weekly workflow runs these every Monday
uv run pytest -m live

# Lint and format check
uv run ruff check .
uv run ruff format --check .

# Build and validate
uv build
uvx twine check dist/*
```

CI runs the offline suite on Python 3.11, 3.12, 3.13, and 3.14, plus Ruff
lint, Ruff format, a build, and a fresh-process check that a bare import
exposes all 41 tools. CI action refs are pinned to a commit SHA, not a
floating tag.

The `docker build` step above produces a non-root image with a healthcheck.
The server pins `fastmcp>=4.0.0,<5.0.0`, currently 4.0.2 on MCP SDK 2.1.1.

## More

- **[docs/tool-reference.md](docs/tool-reference.md)** for all 41 tools
- **[CHANGELOG.md](CHANGELOG.md)** for release history
- **[docs/SUBMISSIONS.md](docs/SUBMISSIONS.md)** for directory listings
- **[docs/fastmcp-4-evaluation.md](docs/fastmcp-4-evaluation.md)** for the
  FastMCP 4 upgrade decision
- Issues and pull requests: [github.com/xmpuspus/ph-civic-data-mcp](https://github.com/xmpuspus/ph-civic-data-mcp)

MIT licensed. Built by Xavier Puspus. Not affiliated with PSA, PHIVOLCS,
PAGASA, PhilGEPS, DPWH, NASA, NOAA, or the World Bank.
