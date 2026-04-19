# ph-civic-data-mcp

> The first MCP server for Philippine government data — earthquakes, weather, typhoons, procurement, population, and air quality — in your AI agent.

[![PyPI](https://img.shields.io/pypi/v/ph-civic-data-mcp.svg)](https://pypi.org/project/ph-civic-data-mcp/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

`ph-civic-data-mcp` is a zero-cost, `stdio`-transport MCP server that exposes live data from **PHIVOLCS**, **PAGASA**, **PhilGEPS**, **PSA**, and **AQICN/EMB** as tools that Claude Desktop, Claude Code, Cursor, or any MCP-compatible client can call directly.

![demo](docs/demo.gif)

## Why this exists

Philippine civic-data portals publish open data, but each in its own schema — scraped HTML tables, PXWeb JSON, undocumented APIs. Nothing ties them together for an AI agent. This server does.

Zero prior art on GitHub or PyPI as of April 2026. Closest: [`panukatan/lindol`](https://github.com/panukatan/lindol) (R, PHIVOLCS only), [`pagasa-parser`](https://github.com/pagasa-parser) (JS, PAGASA only).

## Install

```bash
uvx ph-civic-data-mcp
```

Or via pip:

```bash
pip install ph-civic-data-mcp
```

## Setup

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "ph-civic-data": {
      "command": "uvx",
      "args": ["ph-civic-data-mcp"],
      "env": {
        "AQICN_TOKEN": "your_free_token_from_aqicn.org",
        "PAGASA_API_TOKEN": "optional_pagasa_token"
      }
    }
  }
}
```

### Claude Code

Add to `.claude/settings.json`:

```json
{
  "mcpServers": {
    "ph-civic-data": {
      "command": "uvx",
      "args": ["ph-civic-data-mcp"],
      "env": {
        "AQICN_TOKEN": "your_token"
      }
    }
  }
}
```

Or install via the Claude Code CLI:

```bash
claude mcp add ph-civic-data -- uvx ph-civic-data-mcp
```

### Cursor, Zed, other MCP clients

Any client that supports the stdio MCP transport works. Point the command at `uvx ph-civic-data-mcp` and pass `AQICN_TOKEN` as env.

## What you can ask

After setup, ask your agent:

- _"What earthquakes happened in the Philippines in the last 24 hours?"_
- _"Is Taal volcano active right now?"_
- _"What's the 3-day weather forecast for Quezon City?"_
- _"Are there active typhoons in the Philippines right now?"_
- _"Search PhilGEPS for flood control contracts."_
- _"What is the population of Region VII based on the PSA?"_
- _"What is the poverty incidence in the Bicol Region?"_
- _"What is the air quality in Manila right now?"_
- _"Give me a multi-hazard risk profile for Leyte."_

## Data sources

| Source | Data | Update frequency | Auth |
|---|---|---|---|
| PHIVOLCS | Earthquakes, bulletins, volcano alerts | 5 min (earthquakes), 30 min (volcanoes) | None |
| PAGASA | 10-day weather, active typhoons, alerts | Hourly | Optional `PAGASA_API_TOKEN` |
| Open-Meteo | Weather fallback when PAGASA token absent | Hourly | None |
| PhilGEPS | Government procurement notices (latest ~100) | 6 h (cached) | None |
| PSA OpenSTAT | Population (2020 Census), poverty (2023) | Periodic | None |
| AQICN | Real-time air quality for PH cities | 15 min | **Required** `AQICN_TOKEN` (free) |

## All tools

| Tool | Description | Key params |
|---|---|---|
| `get_latest_earthquakes` | Recent PH earthquakes | `min_magnitude`, `limit`, `region` |
| `get_earthquake_bulletin` | Full PHIVOLCS bulletin for one event | `bulletin_url` |
| `get_volcano_status` | Alert level per monitored PH volcano | `volcano_name` |
| `get_weather_forecast` | 1–10 day forecast (PAGASA or Open-Meteo) | `location`, `days` |
| `get_active_typhoons` | Active tropical cyclones in/near PAR | — |
| `get_weather_alerts` | Active PAGASA warnings | `region` |
| `search_procurement` | Keyword search on PhilGEPS notices | `keyword`, `agency`, `region`, `date_from/to`, `limit` |
| `get_procurement_summary` | Aggregate procurement stats | `agency`, `region`, `year` |
| `get_population_stats` | 2020 Census population | `region` |
| `get_poverty_stats` | 2023 Full-Year poverty incidence | `region` |
| `get_air_quality` | Real-time AQI + pollutants | `city` |
| `assess_area_risk` | Multi-hazard profile (parallel PHIVOLCS + PAGASA + AQICN) | `location` |

## Environment variables

| Variable | Required | Notes |
|---|---|---|
| `AQICN_TOKEN` | **Yes** for `get_air_quality` | Free: https://aqicn.org/data-platform/token/ (1,000 req/min, instant) |
| `PAGASA_API_TOKEN` | Optional | Requires formal PAGASA request. Without it, weather auto-falls-back to Open-Meteo. |

Note: the AQICN `demo` token **only returns data for Shanghai** and will not work for Philippine cities. You must register for a real token (free, <1 minute).

## Data freshness warnings

- **Population:** 2020 Census. No later national data exists yet.
- **Poverty:** 2023 Full-Year poverty statistics (latest PSA release).
- **Procurement:** PhilGEPS open data does not expose filterable search externally. This server scrapes the latest ~100 bid notices and filters client-side. Cached 6h.
- **Emergencies:** for real-time disaster response, always check [ndrrmc.gov.ph](https://ndrrmc.gov.ph) and official PHIVOLCS/PAGASA channels. This server is for research, not life-safety decisions.

## Architecture

- Python 3.11+, `fastmcp>=3.0.0,<4.0.0`
- Two HTTP clients: standard + `PHIVOLCS_CLIENT` with `verify=False` (PHIVOLCS has a broken SSL cert chain). SSL verification is **never** disabled globally.
- In-memory TTL caches per source; no disk writes.
- stdio transport only (zero hosting cost).
- PSA table paths are discovered via the PXWeb browse API, never hardcoded.

## Development

```bash
git clone https://github.com/xmpuspus/ph-civic-data-mcp
cd ph-civic-data-mcp
uv sync --extra dev

# MCP Inspector
fastmcp dev src/ph_civic_data_mcp/server.py

# Tests (run against live APIs)
uv run pytest tests/ -v

# Build
uv run python -m build
uv run twine check dist/*
```

## Limitations

- **PAGASA token is gated.** Non-government users may be denied. Open-Meteo fallback removes this as a hard dependency.
- **AQICN token is required.** Free but must be requested.
- **PhilGEPS is not real-time.** Public portal exposes no filterable API; this server operates on the latest ~100 notices with client-side filtering.
- **Emergencies:** direct users to official channels; this is a research tool.

## Roadmap (v0.2.0)

- `get_active_disasters` / `get_situational_report` via NDRRMC monitoring dashboard
- `assess_hazard(lat, lng)` via HazardHunterPH ArcGIS REST API — per-coordinate flood/earthquake/landslide risk

## Prior art

- [panukatan/lindol](https://github.com/panukatan/lindol) — R package for PHIVOLCS earthquakes
- [pagasa-parser](https://github.com/pagasa-parser) — JS org for PAGASA data parsing

Neither is Python, multi-source, or MCP. This project credits both.

## License

MIT. Xavier Puspus. Not affiliated with PHIVOLCS, PAGASA, PhilGEPS, PSA, or EMB.

## Contributing

Issues and PRs welcome at [github.com/xmpuspus/ph-civic-data-mcp](https://github.com/xmpuspus/ph-civic-data-mcp).
