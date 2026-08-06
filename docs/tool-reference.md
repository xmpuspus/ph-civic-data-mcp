# Tool reference

All 32 tools in `ph-civic-data-mcp` 0.6.0. Every tool is read-only and
idempotent, and every one that calls an upstream service declares
`openWorldHint`.

Every successful response carries `source` and `data_retrieved_at`. Read
[Response contract](#response-contract) before you branch on a result.

- [PSA OpenSTAT catalog](#psa-openstat-catalog)
- [PSA curated statistics](#psa-curated-statistics)
- [Locations (PSGC)](#locations-psgc)
- [Hazards](#hazards)
- [Weather](#weather)
- [Procurement and infrastructure](#procurement-and-infrastructure)
- [Composites](#composites)
- [Science and open data](#science-and-open-data)
- [Server](#server)
- [Prompts](#prompts)
- [Resources](#resources)
- [Response contract](#response-contract)

## PSA OpenSTAT catalog

PSA OpenSTAT publishes roughly 2,900 tables across 27 subjects. The curated
tools below cover five of them. These three reach the rest.

| Tool | Returns | Params |
|---|---|---|
| `browse_psa_catalog` | Lists one level of the catalog. No argument returns the 27 subjects. Pass an entry's `path` to go deeper. | `path?` |
| `describe_psa_dataset` | Returns a `.px` dataset's dimensions, value codes, labels, time dimensions, and full-cube cell count. | `dataset_path` |
| `query_psa_dataset` | Runs one bounded query and returns normalized rows. | `dataset_path`, `selections`, `max_rows?` |

Folder depth varies by subject. `1F/FY` reaches tables in two levels;
`2A/PPA/2025` needs three. Keep browsing until entries come back as
`"type": "dataset"`.

### Limits on `query_psa_dataset`

| Limit | Value | Why |
|---|---|---|
| Explicit selection per dimension | Required | PXWeb expands an unnamed dimension to every one of its values. |
| `"all"` and `"*"` | Rejected | PSA answers a full-cube request with an HTTP 403 from its WAF. |
| Cells per query | 1000 | Computed before the request goes out. |
| `max_rows` | 1 to 5000, default 500 | Clamped, never an error. |
| Path shape | Relative only | A scheme, host, query string, fragment, `..`, or odd character is rejected before any request. |

A rejected argument returns `validation_error: true` and names what to fix. An
OpenSTAT outage returns `upstream_error: true`. The two never overlap.

PSA writes a missing cell as `..`. Those arrive as `null`, never `0`.

### Worked example

```
browse_psa_catalog()                      -> 27 subjects, including {"id": "1F", "title": "Poverty"}
browse_psa_catalog("1F")                  -> {"id": "FY", "title": "Full Year Poverty Statistics"}
browse_psa_catalog("1F/FY")               -> 27 datasets
describe_psa_dataset("1F/FY/0241F3DF013.px")
  -> dimensions: Major Island Group (6), Among Families/Population (2), Year (3)
  -> total_cells: 36
query_psa_dataset("1F/FY/0241F3DF013.px", {
    "Major Island Group": ["0", "2", "5"],
    "Among Families/Population": ["0"],
    "Year": ["2"],
})
  -> 3 rows, reference_period "2023"
  -> PHILIPPINES 10.9, NCR 1.1, Mindanao 17.6
```

Run live 2026-08-06. The `psa_data_explorer` prompt drives this same sequence.

## PSA curated statistics

| Tool | Returns | Params | Vintage |
|---|---|---|---|
| `get_population_stats` | Population by region | `region?`, `year?` | 2020 Census |
| `get_poverty_stats` | Poverty and subsistence incidence | `region?` | 2023 Full Year |
| `get_inflation_stats` | Headline year-on-year CPI inflation | `area?` | Latest published month |
| `get_labor_stats` | Labor Force Survey key rates | `region?` | Latest published quarter |
| `get_health_indicators` | National health indicators | `indicator?` | Per indicator |

Table paths are discovered through the browse API, never hardcoded. Poverty
discovery walks the catalog by title, because PSA moved that subtree from
`DB/1E/FY` to `DB/1F/FY` and the old path now returns 404.

Read the vintage from each response's reference field. The OpenSTAT `updated`
timestamp is server wall clock, not data vintage.

## Locations (PSGC)

| Tool | Returns | Params |
|---|---|---|
| `resolve_ph_location` | Free-text place name to a canonical PSGC record. Handles QC, Gensan, CDO, Metro Manila, and bridges "X City" to "City of X". Returns `alternatives` for an ambiguous name such as "San Juan". | `query` |
| `list_admin_units` | Children of a PSGC node, or the regions when `parent_code` is None | `parent_code?`, `level?`, `limit?`, `offset?` |
| `get_location_hierarchy` | Region to province to city or municipality for one code | `psgc_code` |

## Hazards

| Tool | Returns | Params |
|---|---|---|
| `get_latest_earthquakes` | Recent PHIVOLCS earthquakes | `min_magnitude?`, `limit?`, `region?` |
| `get_earthquake_bulletin` | Full bulletin for one event | `bulletin_url` |
| `get_volcano_status` | Alert level per monitored volcano | `volcano_name?` |
| `get_usgs_earthquakes_ph` | Philippine-bbox events from the USGS global network | `start_date?`, `end_date?`, `min_magnitude?`, `limit?` |
| `get_historical_typhoons_ph` | Historical cyclone tracks through the Philippine AOR | `year?`, `limit?` |

`get_earthquake_bulletin` only accepts a PHIVOLCS host. PHIVOLCS serves a broken
certificate chain, so it is the one source behind a dedicated client with
verification off. That exception never reaches another host.

## Weather

| Tool | Returns | Params |
|---|---|---|
| `get_weather_forecast` | 1 to 10 day forecast | `location`, `days?` |
| `get_active_typhoons` | Active cyclones in or near the PAR | none |
| `get_weather_alerts` | Active PAGASA warnings | `region?` |

`get_weather_forecast` uses the PAGASA TenDay API when `PAGASA_API_TOKEN` is
set, and Open-Meteo otherwise. The response names which one answered.

## Procurement and infrastructure

| Tool | Returns | Params |
|---|---|---|
| `search_procurement` | Keyword search across all PhilGEPS notices | `keyword`, `agency?`, `region?`, `date_from?`, `date_to?`, `limit?` |
| `get_procurement_summary` | Aggregate procurement statistics | `agency?`, `region?`, `year?` |
| `search_infra_projects` | The infra subset: construction, roads, bridges, flood control | `keyword?`, `region?`, `province?`, `year?`, `min_cost_php?`, `status?`, `limit?` |
| `get_infra_project` | One notice in full | `project_id` |
| `summarize_infra_spending` | Breakdown by category, region, agency | `region?`, `year?`, `funding_source?` |
| `flag_infra_anomalies` | Heuristic indicators for further review | `region?`, `province?`, `min_cost_php?` |

`flag_infra_anomalies` fires three rules: `high_cost_no_published_progress`,
`hazard_overlap`, and `duplicate_titles_same_agency`. The first is named for
what it actually checks. The public listing publishes no progress data for any
notice, so it is a cost-threshold transparency flag, not a per-project progress
check.

Every flagged item carries the disclaimer: statistical indicators derived from
public data, and patterns may have legitimate explanations. Present results as
starting points for investigation, never as evidence of wrongdoing.

The window is the latest ~100 published notices, not a census of projects.

## Composites

| Tool | Returns | Params |
|---|---|---|
| `get_area_profile` | One call: resolved PSGC identity, demographics, economy, procurement, hazards, and the 3-day outlook, with per-100k normalization | `location` |
| `assess_area_risk` | The hazard-only subset, faster | `location` |

Both fan out with `asyncio.gather` and degrade per upstream. A block that fails
carries its own `caveats` entry rather than sinking the whole response.

## Science and open data

| Tool | Returns | Params |
|---|---|---|
| `get_solar_and_climate` | NASA POWER daily irradiance, temperature, precipitation, wind | `latitude`, `longitude`, `start_date?`, `end_date?` |
| `get_air_quality` | PM2.5, PM10, NO2, SO2, O3, CO and AQI for ~70 cities | `location` |
| `get_vegetation_index` | MODIS NDVI and EVI time series | `latitude`, `longitude`, `start_date?`, `end_date?` |
| `get_world_bank_indicator` | Philippine macro indicator by code or alias | `indicator`, `per_page?` |

## Server

| Tool | Returns | Params |
|---|---|---|
| `get_data_freshness` | Server version, live tool count, and the full source catalog with TTLs, freshness, and licenses | none |

This is the health and version probe. `tool_count` is the real registered
count, not a hardcoded number. It is the one tool that touches no upstream, so
it is the one with `openWorldHint: false`.

## Prompts

| Prompt | Arg | Sequence it drives |
|---|---|---|
| `area_briefing` | `location` | A sourced civic briefing built on `get_area_profile` |
| `infra_accountability_scan` | `area` | An infra-spending scan with the required defensible framing |
| `psa_data_explorer` | `topic` | Browse, describe, and query the OpenSTAT catalog with explicit codes |

## Resources

| URI | Contents |
|---|---|
| `data://ph-civic/source-catalog` | Every upstream source with URL, freshness, cache TTL, and license |
| `data://ph-civic/civic-framing` | The framing and disclaimer that applies to every accountability result |

## Response contract

A tool that returns a list returns a real list on success.

On upstream failure it returns an envelope instead:

```json
{
  "results": [],
  "upstream_error": true,
  "caveats": ["ConnectError: ..."],
  "source": "...",
  "source_url": "...",
  "data_retrieved_at": "..."
}
```

Read that as "the source was unreachable". Never as "no earthquakes", "no
typhoons", or "no notices".

Three rules follow from it:

1. The server writes no failure to a cache, so a retry is meaningful.
2. The `caveats` entry carries the real error text, not an exception class name.
3. A parse that yields zero rows on a page that always has rows raises, and the
   server caches nothing.

The three OpenSTAT catalog tools add `validation_error: true` for a rejected
argument. That one is not retryable. Fix the argument that `caveats` names.
