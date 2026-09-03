# ph-civic-data-mcp

<!-- mcp-name: io.github.xmpuspus/ph-civic-data-mcp -->

> Philippine civic data as agent-callable tools. The full PSA OpenSTAT
> statistical catalog, plus PSGC location codes, infra-spending accountability,
> earthquakes, weather, typhoons, procurement, poverty, solar radiation, air
> quality, satellite vegetation, and macro indicators. 32 tools, no API keys.

[![PyPI](https://img.shields.io/pypi/v/ph-civic-data-mcp.svg)](https://pypi.org/project/ph-civic-data-mcp/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Glama AAA](https://glama.ai/mcp/servers/xmpuspus/ph-civic-data-mcp/badges/score.svg)](https://glama.ai/mcp/servers/xmpuspus/ph-civic-data-mcp)
[![MCP Registry](https://img.shields.io/badge/MCP%20Registry-io.github.xmpuspus%2Fph--civic--data--mcp-blue)](https://registry.modelcontextprotocol.io/v0.1/servers?search=ph-civic-data-mcp)

**One-click install:**
[![Add to Cursor](https://img.shields.io/badge/Add%20to-Cursor-000000?logo=cursor)](cursor://anysphere.cursor-deeplink/mcp/install?name=ph-civic-data&config=eyJjb21tYW5kIjogInV2eCIsICJhcmdzIjogWyJwaC1jaXZpYy1kYXRhLW1jcCJdfQ==)
[![Add to VS Code](https://img.shields.io/badge/Add%20to-VS%20Code-007ACC?logo=visualstudiocode)](https://insiders.vscode.dev/redirect/mcp/install?name=ph-civic-data&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22ph-civic-data-mcp%22%5D%7D)
[![Install via Smithery](https://img.shields.io/badge/Install%20via-Smithery-blueviolet)](https://smithery.ai/server/ph-civic-data-mcp)
[![Add via Claude Code](https://img.shields.io/badge/Add%20via-Claude%20Code-D97757?logo=anthropic)](https://code.claude.com/docs/en/mcp)

Philippine civic-data portals publish plenty of open data, each in its own
shape: scraped HTML tables, PXWeb JSON, undocumented APIs. Nothing ties them
together for an agent. This server does, over stdio, with zero hosting cost and
no API key needed. Version 0.6.0 opens the entire PSA OpenSTAT catalog, about
2,900 statistical tables, behind three tools with hard safety limits.

All data comes from public records. Heuristic indicators are statistical only.
A specific allegation needs independent investigation and a second source.

## Install

```bash
uvx ph-civic-data-mcp
```

Add it to any stdio MCP client:

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

- **Claude Desktop:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Claude Code:** `claude mcp add ph-civic-data -- uvx ph-civic-data-mcp`
- **Cursor, Zed, VS Code:** the badges above, or the same JSON
- **Docker:** `docker build -t ph-civic-data-mcp .` then run with `-i` (non-root)

![setup](docs/demo_setup.gif)

That recording is `vhs docs/demo_setup.tape`. It spawns Claude Code with
`--mcp-config` pointed at this server, and Claude fans out to
`get_weather_forecast` and `get_population_stats`, then correlates them. The
temperatures and the population are what the live sources returned while it
recorded. Since 0.6.1 the population turn answers from the 2024 Census of
Population (NCR: 14,001,751 as of 1 July 2024).

## Start here

| Question | Call |
|---|---|
| "Profile Tacloban for me" | `get_area_profile("Tacloban")` |
| "Is it safe there right now?" | `assess_area_risk("Leyte")` |
| "Find me PSA data on school enrollment" | `browse_psa_catalog()` then `describe_psa_dataset` then `query_psa_dataset` |
| "What is the PSGC code for QC?" | `resolve_ph_location("QC")` |
| "How many people live in Tacloban?" | `resolve_ph_location("Tacloban")` then `get_population_stats(psgc_code=...)` |
| "Flood control contracts in Pampanga" | `search_infra_projects(province="Pampanga", keyword="flood control")` |
| "What version am I running?" | `get_data_freshness()` |

`get_area_profile` is the one to reach for first on a place-based question. It
resolves the name to a PSGC code once, then composes demographics, economy,
procurement, hazards, and the 3-day outlook in a single turn, with infra
notices already normalized per 100k residents.

## 32 tools across 12 public sources

**PSA OpenSTAT, the whole catalog (new in 0.6.0).** `browse_psa_catalog`,
`describe_psa_dataset`, `query_psa_dataset`. See the worked example below.

**PSA curated statistics.** Population (2024 Census of Population by default,
down to barangay level by PSGC code, with 2010, 2015 and 2020 by year),
poverty (2023 Full Year), CPI inflation, Labor Force Survey rates, health
indicators.

**Locations.** PSGC resolution from free text, admin-unit browsing, full
hierarchies. Nicknames and ambiguous names both work.

**Hazards.** PHIVOLCS earthquakes and bulletins, volcano alert levels, USGS
cross-reference, historical typhoon tracks from NOAA IBTrACS.

**Weather.** PAGASA forecast with an automatic Open-Meteo fallback, active
typhoons, weather alerts.

**Procurement and accountability.** PhilGEPS notices, the infra subset,
spending summaries, and heuristic anomaly indicators for further review.

**Science and open data.** NASA POWER solar and climate, Open-Meteo air
quality, NASA MODIS vegetation indices, World Bank macro indicators.

**Composites.** `get_area_profile` and `assess_area_risk`.

Full signatures, arguments, and limits: **[docs/tool-reference.md](docs/tool-reference.md)**.

## Browse, describe, query: a worked example

OpenSTAT holds about 2,900 tables. Rather than guess a table id, walk to it.

```text
browse_psa_catalog()                 -> 27 subjects, one of them {"id": "1F", "title": "Poverty"}
browse_psa_catalog("1F")             -> {"id": "FY", "title": "Full Year Poverty Statistics"}
browse_psa_catalog("1F/FY")          -> 27 datasets
describe_psa_dataset("1F/FY/0241F3DF013.px")
  -> Major Island Group (6 values), Among Families/Population (2), Year (3)
  -> total_cells: 36
query_psa_dataset("1F/FY/0241F3DF013.px", {
    "Major Island Group": ["0", "2", "5"],
    "Among Families/Population": ["0"],
    "Year": ["2"],
})
  -> PHILIPPINES 10.9, NCR 1.1, Mindanao 17.6, reference_period "2023"
```

Run live on 2026-08-06. The `psa_data_explorer` prompt drives this sequence for
an agent.

Four limits make a generic query tool safe to hand an agent:

1. The tool rebuilds every path under the OpenSTAT base. A scheme, a host, a
   query string, a fragment, `..`, or an odd character never reaches the wire.
2. Every dimension needs explicit value codes. PXWeb expands an unnamed
   dimension to all of its values, and PSA answers that with an HTTP 403.
3. The tool refuses `"all"` and `"*"` for the same reason.
4. The tool computes the cell product before the request, and caps it at 1000.

## An outage returns an envelope, never an empty list

A list tool returns a real list on success. On upstream failure it returns an
envelope instead:

```json
{ "results": [], "upstream_error": true, "caveats": ["ConnectError: ..."] }
```

Read that as "the source was unreachable", never as "no earthquakes" or "no
notices". Failures never enter a cache, so a retry is meaningful, and a
`caveats` entry carries the real error rather than an exception class name.

The three OpenSTAT catalog tools and `get_population_stats` add
`validation_error: true` for a rejected argument. Fix the argument the message
names. A retry cannot help. Single-value tools carry `data_status`, one of
`success`, `empty`, `unavailable`, `indeterminate` or `invalid_request`.
`get_area_profile` reports one status per block in `blocks` and folds every
failed block into `caveats`, so a null never sits beside an empty `caveats`
list.

Every response carries `source` and `data_retrieved_at`.

## Data sources and freshness

| Source | Data | Refresh | Auth |
|---|---|---|---|
| PSA OpenSTAT | ~2,900 statistical tables; population, poverty, CPI, LFS, health | Per-table vintage | None |
| PSGC | Philippine Standard Geographic Code via psgc.gitlab.io | On PSA publication | None |
| PHIVOLCS | Earthquakes, bulletins, volcano alerts | 5 min / 30 min | None |
| PAGASA | 10-day weather, typhoons, alerts | Hourly | Optional `PAGASA_API_TOKEN` |
| Open-Meteo | Weather fallback, and air quality | Hourly | None |
| PhilGEPS | Procurement notices (latest ~100) | 6 h cache | None |
| NASA POWER | Daily solar irradiance, temperature, precipitation, wind | Daily | None |
| NASA MODIS (ORNL) | NDVI and EVI, 250 m, 16-day composites | Weekly | None |
| USGS FDSN | Philippine-region events from the global network | Minutes | None |
| NOAA IBTrACS | Historical cyclone tracks through the PAR | Per storm | None |
| World Bank | Philippine macro indicators | Annual | None |

`PAGASA_API_TOKEN` is the only environment variable, and it is optional. PAGASA
gates it behind a formal request. Without it, forecasts use Open-Meteo. Every
one of the 32 tools works with no token at all.

Three vintages worth stating plainly:

- **Population defaults to the 2024 Census of Population** (reference date
  1 July 2024). PSA moved the census folders on OpenSTAT in 2026, so the
  server discovers them by title on every cold start and names the census,
  reference date and geography level in every result. Pass `year` for 2010,
  2015 or 2020, and `psgc_code` for a city, municipality or barangay.
- **Poverty is 2023 Full Year.** PSA publishes it every three years.
- **Procurement is not real time.** The public portal exposes no filterable
  API, so this server reads the latest ~100 notices and filters locally.

The OpenSTAT `updated` field is server wall clock, not data vintage. Read the
vintage from the table's own time dimension, which every response reports.

## Flagged notices are starting points, never evidence

`flag_infra_anomalies`, `summarize_infra_spending`, and the procurement search
produce starting points for investigation, never evidence of wrongdoing. Every
flagged item ships with a disclaimer, and the server instructs agents to use
defensible language.

`high_cost_no_published_progress` is named for what it actually checks: the
public listing publishes no progress data for any notice, so it is a
cost-threshold transparency flag, not a per-project progress check.

**For an emergency, use [ndrrmc.gov.ph](https://ndrrmc.gov.ph) and the official
PHIVOLCS and PAGASA channels.** This is not a life-safety system but a research
tool.

## Development

```bash
git clone https://github.com/xmpuspus/ph-civic-data-mcp
cd ph-civic-data-mcp
uv sync --locked --extra dev

# Offline tests, exactly what CI runs
uv run pytest tests/ -q -m "not live"

# Live tests against real upstreams; the weekly workflow runs these
uv run pytest tests/ -q -m live

# The MCP Inspector, and a static report of the surface
fastmcp dev inspector src/ph_civic_data_mcp/server.py
fastmcp inspect src/ph_civic_data_mcp/server.py

# Build and validate
uv build
uvx twine check dist/*
```

CI runs the offline suite on Python 3.11, 3.12, 3.13, and 3.14, plus Ruff lint,
Ruff format, a build, and a fresh-process check that a bare import exposes all
32 tools.

Architecture notes: Python 3.11+, `fastmcp>=3.0.0,<4.0.0`, stdio only,
in-memory TTL caches and no disk writes, and two HTTP clients. The second one
exists because PHIVOLCS serves a broken certificate chain. This server never
disables TLS checks globally or for any other host, and that client never
follows a redirect on its own. Every hop is checked against the PHIVOLCS host
allowlist first.

## Related projects

Other Philippine civic-data MCP servers cover a single dataset each: PSGC
administrative geography, holidays, DHSUD license-to-sell, DepEd schools. None
of them expose hazard feeds, weather, procurement, or statistical data, and
none compose across sources.

## More

- **[docs/tool-reference.md](docs/tool-reference.md)** for all 32 tools
- **[CHANGELOG.md](CHANGELOG.md)** for release history
- **[docs/SUBMISSIONS.md](docs/SUBMISSIONS.md)** for directory listings
- Issues and pull requests: [github.com/xmpuspus/ph-civic-data-mcp](https://github.com/xmpuspus/ph-civic-data-mcp)

MIT licensed. Built by Xavier Puspus. Not affiliated with PSA, PHIVOLCS,
PAGASA, PhilGEPS, DPWH, NASA, NOAA, or the World Bank.
