# Tool reference

All 35 tools in `ph-civic-data-mcp` 0.7.0. Every tool is read-only and
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
tools below cover five of them. These four tools reach the rest. Three walk
the tree level by level. One searches it by keyword.

### browse_psa_catalog

Lists one level of the catalog. No argument returns the 27 subjects.

```python
browse_psa_catalog(path: str | None = None) -> dict
```

Returns: `path`, `parent_path`, `entries` (each with `id`, `title`, `type`
of `folder` or `dataset`, and a `path` to pass back), `folder_count`,
`dataset_count`, `caveats`. Pass a dataset entry's path to
`describe_psa_dataset` next.

On failure: an OpenSTAT outage returns `upstream_error: true`, with the real
error in `caveats`.

### describe_psa_dataset

Reads a dataset's dimensions and value codes before you query it.

```python
describe_psa_dataset(dataset_path: str) -> dict
```

Returns: `dataset_path`, `title`, `dimensions` (each with `code`, `label`,
`values`, `is_time_like`), `total_cells`, `max_cells_per_query`,
`time_dimensions`.

On failure: a malformed `dataset_path` returns `validation_error: true`. An
OpenSTAT outage returns `upstream_error: true`.

### query_psa_dataset

Runs one bounded query against a dataset and returns normalized rows.

```python
query_psa_dataset(
    dataset_path: str,
    selections: dict[str, list[str]],
    max_rows: int = 500,
) -> dict
```

Returns: `dataset_path`, `title`, `rows` (dimension codes, `labels`, and a
numeric or null value per row), `row_count`, `requested_cells`, `truncated`,
`reference_period`, `disclaimer`.

On failure: a missing dimension, `"all"`, `"*"`, or over 1000 cells returns
`validation_error: true` before any request goes out.

### search_psa_catalog

Finds a dataset by keyword, without browsing level by level. New in 0.7.0.

```python
search_psa_catalog(keyword: str, limit: int = 20) -> dict
```

Returns: `keyword`, `matches` (each with `path` and `title`), `match_count`,
`total_available`, `limit` (1 to 100, default 20, capped at 100).

On failure: an empty keyword returns `validation_error: true`. An outage
during the catalog walk returns `upstream_error: true`.

Folder depth varies by subject. `1F/FY` reaches tables in two levels.
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

`search_psa_catalog` builds one flattened index of the whole catalog on its
first call after a cold start. That walk can take a few minutes. The index
then caches for 24 hours, so a later search answers from memory.

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
search_psa_catalog("fertility")           -> matches naming the dataset path and title
```

Run live 2026-08-06. The `psa_data_explorer` prompt drives the browse-describe-query
sequence.

## PSA curated statistics

### get_population_stats

Census population for a place. Defaults to the 2024 Census.

```python
get_population_stats(
    region: str | None = None,
    year: int | None = None,
    psgc_code: str | None = None,
) -> dict
```

Returns: `population`, `year`, `census`, `reference_date`, `geography`,
`geography_level`, `psgc_code`, `region`, `parent_region`,
`available_vintages`, `data_status`. `region` reads a PSA label such as
"NCR" or "City of Manila". `psgc_code` reaches a barangay, on the 2024
tables only, and does not combine with `region`.

On failure: an outage sets `upstream_error: true` and `data_status:
"unavailable"`. A bad `region` or `year` sets `validation_error: true` and
`data_status: "invalid_request"`. A code no census table carries sets
`data_status: "empty"`.

### get_poverty_stats

Poverty and subsistence incidence. Latest vintage: 2023 Full Year.

```python
get_poverty_stats(region: str | None = None) -> dict
```

Returns: `poverty_incidence_pct`, `subsistence_incidence_pct`, `region`,
`reference_year`, `source_url`.

On failure: an outage sets `upstream_error: true`. The server never caches
a failure.

### get_inflation_stats

Headline year-on-year CPI inflation, latest published month.

```python
get_inflation_stats(area: str | None = None) -> dict
```

Returns: `headline_inflation_pct`, `area`, `reference_period`. `area` reads
a PSA region label or "Philippines". PSA publishes with a lag, so this is
the latest available month, not necessarily the current one.

On failure: an outage sets `upstream_error: true`. The server never caches
a failure.

### get_labor_stats

Labor Force Survey key rates, latest published quarter.

```python
get_labor_stats(region: str | None = None) -> dict
```

Returns: labor-force participation, employment, unemployment, and
underemployment rates, plus `reference_period`. The series is national
only. A `region` argument adds a note field instead of filtering.

On failure: an outage sets `upstream_error: true`. The server never caches
a failure.

### get_health_indicators

National health indicators from the PSA Health subject.

```python
get_health_indicators(indicator: str | None = None) -> dict
```

Returns: with no argument, the curated headline set (maternal mortality
ratio, total fertility rate). `indicator` fuzzy-matches any table under the
Health subject, browse-discovered, never hardcoded.

On failure: an outage sets `upstream_error: true`. The server never caches
a failure.

Table paths come from the browse API, never a hardcoded value. Poverty
discovery walks the catalog by title, because PSA moved that subtree from
`DB/1E/FY` to `DB/1F/FY` and the old path now returns 404.

Read the vintage from each response's reference field. The OpenSTAT `updated`
timestamp is server wall clock, not data vintage.

## Locations (PSGC)

### resolve_ph_location

Resolves a free-text place name to its canonical PSGC record.

```python
resolve_ph_location(query: str) -> dict
```

Returns: `psgc_code`, `name`, `level` (`region`, `province`, `city`,
`municipality`, or `barangay`), `parent_code`, `region_name`,
`match_score`, `alternatives` for an ambiguous name such as "San Juan".
Handles nicknames (QC, Gensan, CDO, Metro Manila) and bridges "X City" to
"City of X". "Cebu", "Bacolod", "Davao", and "Iloilo" now resolve to the
city. A plain municipality now reads `level: "municipality"`. `region_name`
is now filled at every level, not always null.

On failure: no match returns `{"matched": false, "caveats": [...]}`. A PSGC
outage adds `upstream_error: true` to the same shape.

### list_admin_units

Lists children of a PSGC node, or the regions when no parent is given.

```python
list_admin_units(
    parent_code: str | None = None,
    level: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict] | dict
```

Returns: a list of PSGC records with `psgc_code`, `name`, `level`,
`parent_code`, `region_name`. `limit` caps at 500. `offset` pages past 500
children, for example Manila's 897 barangays.

On failure: a malformed `parent_code` (non-digit characters, a path
character) returns `validation_error: true` with no upstream call. An
outage returns `{results: [], upstream_error: true, caveats}`.

### get_location_hierarchy

Returns the full chain from region to barangay for one PSGC code.

```python
get_location_hierarchy(psgc_code: str) -> dict
```

Returns: `psgc_code`, `chain` (a list of `{psgc_code, name, level,
source_url}` from region down to the given code).

On failure: a malformed `psgc_code` returns `validation_error: true` with
no upstream call. An outage returns `upstream_error: true`.

## Hazards

### get_latest_earthquakes

Recent PHIVOLCS earthquakes, filtered by size, region, or distance.

```python
get_latest_earthquakes(
    min_magnitude: float = 1.0,
    limit: int = 20,
    region: str | None = None,
    center_lat: float | None = None,
    center_lon: float | None = None,
    radius_km: float | None = None,
) -> list[dict] | dict
```

Returns: a list of events. Give `center_lat`, `center_lon`, and
`radius_km` together to filter to one place. Each matched event then
carries `distance_km`. `radius_km` must be positive.

On failure: giving only some of the three distance arguments returns
`validation_error: true`. An outage returns `{results: [], upstream_error:
true, caveats}`, never an empty list.

### get_earthquake_bulletin

Reads the full bulletin for one PHIVOLCS earthquake event.

```python
get_earthquake_bulletin(bulletin_url: str) -> dict
```

Returns: the full bulletin fields for the event named by `bulletin_url`,
which comes from `get_latest_earthquakes`.

On failure: an empty, malformed, or non-PHIVOLCS URL returns a note entry
with no upstream call.

### get_volcano_status

Reads the current alert level for Philippine volcanoes.

```python
get_volcano_status(volcano_name: str | None = None) -> list[dict] | dict
```

Returns: a list of monitored volcanoes with recent bulletins, or one
volcano's record when `volcano_name` is given (for example "Mayon",
"Taal", "Kanlaon", "Bulusan").

On failure: an unreachable or empty bulletin list returns `{results: [],
upstream_error: true, caveats}`. One volcano's own bulletin fetch failing
sets `upstream_error: true` on that entry, not a null alert level.

### get_usgs_earthquakes_ph

Philippine-bbox earthquakes from the USGS global network.

```python
get_usgs_earthquakes_ph(
    start_date: str | None = None,
    end_date: str | None = None,
    min_magnitude: float = 4.0,
    limit: int = 50,
    center_lat: float | None = None,
    center_lon: float | None = None,
    radius_km: float | None = None,
) -> list[dict] | dict
```

Returns: a list of events inside the PH bounding box, with international
Mww/Mwc size ratings and depth. The same `center_lat`/`center_lon`/
`radius_km` distance filter as `get_latest_earthquakes` adds `distance_km`
per event.

On failure: giving only some of the three distance arguments returns
`validation_error: true`. An outage returns `upstream_error: true`.

### get_historical_typhoons_ph

Historical cyclone tracks that passed through the Philippine AOR.

```python
get_historical_typhoons_ph(year: int | None = None, limit: int = 30) -> list[dict] | dict
```

Returns: peak intensity, minimum pressure, and track period per storm, from
NOAA IBTrACS. No `year` returns the last three years.

On failure: an ERDDAP outage returns `upstream_error: true`.

### get_flood_forecast

Daily river discharge forecast for a Philippine place, from Open-Meteo's
GloFAS flood model.

```python
get_flood_forecast(location: str, forecast_days: int = 7, past_days: int = 0) -> dict
```

Returns: `location`, `latitude`, `longitude`, `days` (a list of `date`,
`river_discharge_m3s`, `river_discharge_max_m3s`, `river_discharge_min_m3s`),
`units`, `forecast_days`, `past_days`, `note` (the reading is a model value
for the nearest river cell, not a gauge). `forecast_days` runs 1 to 30.
`past_days` runs 0 to 30.

On failure: an unresolved `location`, or a `forecast_days` or `past_days`
outside its range, returns `data_status: "invalid_request"`. A PSGC outage
during location lookup, or an Open-Meteo fetch failure, returns
`data_status: "unavailable"`. A response with no readable daily dates
returns `data_status: "indeterminate"` and is never cached.

`get_earthquake_bulletin` only accepts a PHIVOLCS host. PHIVOLCS serves a
broken certificate chain, so it is the one source behind a dedicated client
with verification off. That exception never reaches another host.

## Weather

### get_weather_forecast

Reads a 1 to 10 day forecast for a Philippine location.

```python
get_weather_forecast(location: str, days: int = 3) -> dict
```

Returns: a forecast for `days` (clamped 1 to 10), and `data_source` naming
which upstream answered. Uses the PAGASA TenDay API when
`PAGASA_API_TOKEN` is set, and Open-Meteo otherwise.

On failure: an outage returns `upstream_error: true`.

### get_active_typhoons

Reads active tropical cyclones in or near the PAR.

```python
get_active_typhoons() -> list[dict] | dict
```

Returns: a list of active cyclones, empty when none are active.

On failure: an unreachable PAGASA bulletin page returns `{results: [],
upstream_error: true, caveats}`. This is never mistaken for "no active
typhoons".

### get_weather_alerts

Reads active PAGASA weather warnings and advisories.

```python
get_weather_alerts(region: str | None = None) -> list[dict] | dict
```

Returns: a list of active alerts, or `[]` with a note when the page is
reachable but its state is unclear, and `[]` with an explicit
"no active warnings" signal when PAGASA says so.

On failure: an unreachable page returns `upstream_error: true`.

## Procurement and infrastructure

### search_procurement

Keyword search across all PhilGEPS notices.

```python
search_procurement(
    keyword: str,
    agency: str | None = None,
    region: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict] | dict
```

Returns: a list of matching notices, filtered client-side over the latest
~100 published notices, cached 6 hours.

On failure: an outage returns `{results: [], upstream_error: true,
caveats}`, never cached.

### get_procurement_summary

Reads aggregate procurement statistics over the latest notices.

```python
get_procurement_summary(
    agency: str | None = None,
    region: str | None = None,
    year: int | None = None,
) -> dict
```

Returns: `total_count`, `total_value_php`, `by_mode`, `by_region` (now
read from the agency string), `top_agencies`, `reference_period`,
`rules_evaluated`, `rules_not_computable`.

On failure: an outage returns `upstream_error: true` with `total_count: 0`
and every breakdown empty, named in `rules_not_computable`.

### search_infra_projects

Searches the infra subset: construction, roads, bridges, flood control.

```python
search_infra_projects(
    keyword: str | None = None,
    region: str | None = None,
    province: str | None = None,
    year: int | None = None,
    min_cost_php: float | None = None,
    status: str | None = None,
    limit: int = 25,
) -> list[dict] | dict
```

Returns: a list, each with `project_id`, `title`, `agency`, `cost_php`
(null in most records, unpublished by PhilGEPS), `progress_pct`, `status`,
`lat`/`lng`. `limit` caps at 100.

On failure: an outage returns `upstream_error: true`.

### get_infra_project

Reads the full record for one infrastructure project.

```python
get_infra_project(project_id: str) -> dict
```

Returns: full project fields (`cost_php`, `progress_pct`,
`funding_source`, `contractor`, coordinates, documents) where the upstream
listing exposes them, null otherwise.

On failure: an empty `project_id` returns a note entry with no upstream
call. An outage returns `upstream_error: true`.

### summarize_infra_spending

Aggregates infrastructure procurement statistics by category, region, agency.

```python
summarize_infra_spending(
    region: str | None = None,
    year: int | None = None,
    funding_source: str | None = None,
) -> dict
```

Returns: `total_count`, `total_value_php`, `by_category`, `by_region`,
`top_agencies`, `rules_evaluated`, `rules_not_computable`, `sample_size`,
`sufficient_for_per_capita`, `coverage_caveat`. `sufficient_for_per_capita`
is false, and a per-capita figure is withheld, below a 500-notice sample.
`by_funding_source` is retired. PhilGEPS notices carry no such field.

On failure: an outage returns `upstream_error: true` with every breakdown
empty, named in `rules_not_computable`.

### flag_infra_anomalies

Flags PhilGEPS infra notices for further review, never as accusations.

```python
flag_infra_anomalies(
    region: str | None = None,
    province: str | None = None,
    min_cost_php: float = 50_000_000,
) -> dict
```

Returns: flagged items, each with the rule that fired
(`high_cost_no_published_progress`, `hazard_overlap`,
`duplicate_titles_same_agency`) and the evidence. `high_cost_no_published_progress`
is a cost-threshold flag. The open listing publishes no progress data for
any notice.

On failure: an outage in a cross-referenced source returns `upstream_error:
true` and a note naming which one.

Every flagged item carries the disclaimer: statistical indicators derived from
public data, and patterns may have legitimate explanations. Present results as
starting points for investigation, never as evidence of wrongdoing.

The window is the latest ~100 published notices, not a census of projects.

## Composites

### get_area_profile

One call: resolved PSGC identity, demographics, economy, procurement,
hazards, and the 3-day outlook, with per-100k normalization.

```python
get_area_profile(location: str) -> dict
```

Returns: `resolved`, `demographics` (population and poverty now describe
the resolved place, not its region, with `population_geography`,
`population_geography_level`, `population_psgc_code`,
`population_reference_date`, `poverty_area`), `economy`, `procurement`,
`hazard`, `weather`, `correlations` (`infra_notices_per_100k_population`),
`national_reference` (`population`, `population_year`,
`poverty_incidence_pct`, `poverty_year`, `population_share_pct`,
`poverty_gap_pct_points`, withheld with a note when the place and national
vintages differ), `blocks` (per-block status, now including `resolve`,
`hierarchy`, `national_population`, `national_poverty`), `caveats`.

On failure: a block that fails carries its own note entry rather than
sinking the response. `upstream_error: true` when any block is
unavailable. The first call in a fresh process can take about 15 seconds
under the PSA rate limit. Later calls are cached.

### assess_area_risk

Reads the hazard-only subset of `get_area_profile`, faster.

```python
assess_area_risk(location: str) -> dict
```

Returns: `earthquake_risk_level` (derived from 30-day seismic activity, not
an official PHIVOLCS rating), typhoon signal status, active alerts,
elevated volcano alerts, `caveats` for any failed sub-call.

On failure: a sub-call failure adds a note entry rather than sinking the
response.

### compare_areas

Compares civic indicators for 2 to 5 Philippine places, side by side. New
in 0.7.0.

```python
compare_areas(
    locations: list[str],
    metrics: list[str] | None = None,
    format: str = "json",
) -> dict
```

Returns: `rows` (one per place, with `resolved_name`, `psgc_code`,
`level`, and one column per metric), the effective `metrics` list,
`comparable` (false when vintages or admin levels differ across places,
with a note naming both), `data_status` (`indeterminate` when some places
fail to resolve). `metrics` defaults to all eight of the allowlist:
population, population_year, poverty_incidence_pct, poverty_year,
headline_inflation_pct, employment_rate_pct, infra_notice_count,
earthquake_risk_level. `format="csv"` adds an `export` field, a CSV
string.

On failure: a wrong location count, an unknown metric, or a bad `format`
returns `validation_error: true` with no calls made. A place that never
resolves still gets a row, with `resolved_name: null`.

Both `get_area_profile` and `assess_area_risk` fan out with `asyncio.gather`
and degrade per upstream. Neither sinks the whole response on one failed
source. A returned failure envelope from a sibling tool folds into
`caveats` the same way a raised exception does.

## Science and open data

### get_solar_and_climate

Reads NASA POWER daily irradiance, temperature, precipitation, wind.

```python
get_solar_and_climate(
    latitude: float,
    longitude: float,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict
```

Returns: daily shortwave irradiance, 2m temperature, precipitation, 2m wind
speed for any coordinate. `start_date` defaults to 14 days ago.

On failure: an outage returns `upstream_error: true`.

### get_air_quality

Reads real-time air quality for one of about 80 major PH cities.

```python
get_air_quality(location: str) -> dict
```

Returns: PM2.5, PM10, CO, NO2, SO2, O3, European AQI, US AQI with a
category. Timestamps are now genuine UTC, requested from Open-Meteo as UTC.

On failure: a city outside the coordinate table returns a note entry with
no upstream call. An outage returns `upstream_error: true`.

### get_vegetation_index

Reads NASA MODIS NDVI and EVI time series for any coordinate.

```python
get_vegetation_index(
    latitude: float,
    longitude: float,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict
```

Returns: NDVI (-1 to 1) and EVI values over the requested period, at 250m
resolution, 16-day composite.

On failure: an outage returns `upstream_error: true`.

### get_world_bank_indicator

Reads a Philippine macro indicator by code or alias.

```python
get_world_bank_indicator(indicator: str, per_page: int = 20) -> dict
```

Returns: the latest `per_page` observations for the indicator, most recent
first. Accepts a World Bank code (`NY.GDP.MKTP.CD`) or an alias (`gdp`,
`poverty_ratio`, `inflation`).

On failure: an unresolvable code or alias returns `validation_error: true`
with no upstream call.

### search_hdx_datasets

Finds Philippine datasets on the Humanitarian Data Exchange (HDX) by
keyword. Each dataset carries its own license, so read `license_id` before
reuse. New in 0.8.0.

```python
search_hdx_datasets(query: str, rows: int = 10) -> dict
```

Returns: `query`, `total_count`, `datasets` (each with `name`, `title`,
`organization`, `license_id`, `license_title`, `license_url`,
`last_modified`, `num_resources`, `hdx_url`, and up to 20 `resources` with
`name`, `format`, `url`, `size`, `last_modified`), `rows` (1 to 50, default
10). Results sort by `metadata_modified` descending. A query with no match
returns `data_status: "empty"` with an empty list.

On failure: an empty query, a query over 200 characters, or `rows` outside
1 to 50 returns `validation_error: true`. A body whose `success` field is
not true, or whose `results` is not a list, returns
`data_status: "indeterminate"`. An outage returns `upstream_error: true`.
## Government record

### get_official_gazette_feed

Reads the Official Gazette's own RSS feed of laws and issuances.

```python
get_official_gazette_feed(page: int = 1) -> dict
```

Returns: `page`, `items` (title, link, pub_date, creator, categories, guid,
description), `item_count`, `feed_title`, and `feed_link`, ten issuances per
page, newest first. `page` runs from 1 to 50.

On failure: a `page` outside 1 to 50 returns `validation_error: true`. This
host returns a Cloudflare block page on every other path, and on a HEAD
request even on `/feed/`, so this tool sends only a GET to `/feed/` or
`/feed/?paged=<page>`. A block page returns `upstream_error: true`, never an
empty item list.

## Server

### get_data_freshness

Reads server health, version, and the full data-source catalog.

```python
get_data_freshness() -> dict
```

Returns: `server_version`, `server_name`, `transport`, `tool_count`
(the real registered count), `sources` (URL, freshness, TTL, license per
source), `source_health` (per host: `last_success_at`, `last_failure_at`,
`last_error`, `last_latency_ms`, `success_count`, `failure_count`, process
local, empty on a cold start), `cache_age` (per cache: `size`,
`ttl_seconds`).

On failure: none. This tool touches no upstream. It is the one tool with
`openWorldHint: false`.

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
3. A parse that yields zero rows on a page that always has rows raises. The
   server caches nothing.

The three OpenSTAT catalog tools, plus `compare_areas`, `list_admin_units`, and
`get_location_hierarchy`, add `validation_error: true` for a rejected
argument. That one is not retryable. Fix the argument that `caveats` names.

Single-value tools carry `data_status`, set through the same builder,
`utils/envelope.py::failure_result`:

| `data_status` | `upstream_error` | `validation_error` | Meaning |
|---|---|---|---|
| `success` | false | false | A figure, with its provenance |
| `empty` | false | false | The source answered and has no row for this request |
| `unavailable` | true | false | The source was down or answered with an unreadable body |
| `indeterminate` | true | false | The source answered but the result cannot be trusted |
| `invalid_request` | false | true | A caller mistake, named in `caveats` |

`upstream_error` is true for `unavailable` and `indeterminate` only.
`validation_error` is true for `invalid_request` only. A response never
sets both flags at once.

`get_population_stats` sets every one of these. Every 0.7.0 tool in this
document adopts the same field.
