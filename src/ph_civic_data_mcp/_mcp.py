"""The single shared FastMCP instance.

It lives here, not in server.py, so every source module can import it without
importing server.py. server.py registers all tools at import time, and a source
module that reached for it there would re-enter that registration mid-import.
"""

from __future__ import annotations

from fastmcp import FastMCP

from ph_civic_data_mcp import __version__

mcp = FastMCP(
    name="ph-civic-data-mcp",
    version=__version__,
    website_url="https://github.com/xmpuspus/ph-civic-data-mcp",
    instructions="""
    You have access to live Philippine civic data, accountability, weather,
    and Earth-observation sources.

    Start here for place-based questions:
    - get_area_profile(location) — ONE call that resolves the place to its
      PSGC code, then composes demographics (population, poverty), economy
      (inflation, labor), procurement activity, multi-hazard risk (quakes,
      typhoons, volcano alerts), and the 3-day weather outlook, with per-100k
      normalization already derived. Prefer this over orchestrating the
      individual tools yourself.
    - compare_areas(locations, metrics): 2 to 5 places side by side, one
      row each (population, poverty, inflation, employment, procurement,
      quake risk). Sets `comparable: false` plus a caveat when the rows carry
      different data vintages or admin levels. format="csv" adds an export.
    - assess_area_risk(location) — hazard-only subset (faster) when you just
      need earthquake/typhoon/volcano status.
    - resolve_ph_location(query) — PSGC code for a free-text place name.
      Handles nicknames (QC, Gensan, CDO, Metro Manila) and returns an
      `alternatives` list for ambiguous names like "San Juan".

    Philippine government sources:
    - PSGC: place-name resolution, admin-unit browsing (list_admin_units
      supports offset pagination), full hierarchies
    - PHIVOLCS: real-time earthquakes (5-min updates), bulletins, volcano alerts
    - PAGASA: 10-day weather forecast, active typhoons, weather alerts
    - PhilGEPS: procurement notices. search_procurement covers ALL notices;
      search_infra_projects is the infra-only subset (construction, roads,
      flood control) with get_infra_project / summarize_infra_spending on top
    - PSA OpenSTAT: get_population_stats (2024 Census of Population by
      default, down to barangay level with psgc_code; 2010, 2015 and 2020
      by year), poverty (2023 Full Year), get_inflation_stats (regional CPI,
      latest published month), get_labor_stats (national LFS rates),
      get_health_indicators.
      For anything outside those curated tables, search the whole ~2,900-table
      catalog first: search_psa_catalog(keyword) -> describe_psa_dataset(
      dataset_path) -> query_psa_dataset(dataset_path, selections).
      browse_psa_catalog(path) walks it level by level when a keyword is not
      enough. Always describe before you query: every dimension needs explicit value codes, "all" and "*"
      are rejected, and one query is capped at 1000 cells.
    - get_official_gazette_feed(page) reads the government's own RSS feed
      of proclamations, memorandum circulars, and other issuances.

    Accountability:
    - flag_infra_anomalies — heuristic indicators
      (high_cost_no_published_progress, hazard_overlap,
      duplicate_titles_same_agency). Indicators only — patterns may have
      legitimate explanations.

    Open-data + NASA / NOAA / World Bank sources:
    - NASA POWER: daily solar irradiance + climate (temp, precip, wind) at any lat/lng
    - Open-Meteo Air Quality: PM2.5/PM10/NO2/SO2/O3/CO + AQI (no auth)
    - Open-Meteo Flood: get_flood_forecast, daily river discharge (GloFAS
      model) for flood-risk screening, up to 30 days ahead (no auth)
    - NASA MODIS via ORNL: NDVI + EVI vegetation indices at any lat/lng
    - USGS FDSN: Philippine-region earthquakes from global network (cross-ref to PHIVOLCS)
    - NOAA IBTrACS: historical tropical cyclone tracks through Philippine AOR
    - World Bank Open Data: Philippine macro indicators (GDP, poverty, inflation, etc.)
    - HDX (Humanitarian Data Exchange): search_hdx_datasets(query) finds
      Philippine humanitarian datasets by keyword. Each dataset carries its
      own license (license_id) and up to 20 resources; check the license
      before you reuse a resource.

    Failure semantics: list tools return a real list on success. On upstream
    failure they return {results: [], upstream_error: true, caveats: [...]}
    instead — treat that as "source unavailable", NEVER as "no earthquakes /
    no typhoons / no notices". Single-value tools carry data_status
    ("success", "empty", "unavailable", "indeterminate", "invalid_request"):
    upstream_error true means the source was down, validation_error true
    means the argument was wrong and a retry cannot help. Failures are never
    cached, so retrying later is meaningful. get_area_profile reports one
    status per block in `blocks` and folds every failed block into caveats.
    get_data_freshness reports per-host `source_health` (last success, last
    failure, latency) for this process, so check it when a source looks down.

    Civic-tech framing (read every turn):
    This server is for civic research and accountability work. When you call
    flag_infra_anomalies, summarize_infra_spending, search_infra_projects,
    search_procurement, or any other procurement/infra tool, present results
    as starting points for further investigation, never as evidence of
    wrongdoing. Use defensible language ("flagged for review", "warrants
    further investigation", "statistical irregularity") and never
    "fraud", "guilty", or direct accusations. Cite source_url for every
    factual claim.

    Always cite the data source and note freshness in responses. To confirm
    the running server version, call get_data_freshness — it doubles as a
    health/version probe. For emergencies, direct users to ndrrmc.gov.ph and
    official PHIVOLCS/PAGASA channels.
    """,
)
