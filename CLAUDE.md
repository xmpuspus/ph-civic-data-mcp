# CLAUDE.md — ph-civic-data-mcp

> **Purpose:** This file is the single source of truth for building `ph-civic-data-mcp` — a zero-cost, stdio MCP server exposing Philippine government data (PHIVOLCS, PAGASA, PhilGEPS, PSA, EMB) as agent-callable tools. Claude Code must read this file before writing any code, validate against it continuously, and use it to resolve ambiguity.

---

## Validation Log (April 18, 2026)

Every URL, API shape, and technical claim was cross-checked against live sources before this version. Corrections from v1:

| # | What was wrong | What's correct |
|---|---|---|
| 1 | Bulletin URL pattern stated as predictable `{YEAR}_{MMDD}_{HHMM}_B{N}.html` | Pattern is inconsistent — some use 4-digit time, some 6-digit with seconds. `F` suffix (final) appears unpredictably. **Never construct bulletin URLs. Always parse hrefs from the list page.** |
| 2 | PHIVOLCS SSL noted as a minor issue | PHIVOLCS has a broken SSL cert (`unable to get local issuer certificate`). Must use `verify=False` on a dedicated client for this domain only. |
| 3 | PAGASA fallback listed as PANaHON scraping | PANaHON is a React SPA backed by unstable internal ECMWF endpoints. **Use Open-Meteo as fallback instead** — free, no key, global, stable, covers PH perfectly. |
| 4 | PAGASA Excel files implied as an option | **Excel files permanently discontinued August 31, 2025.** Removed all references. |
| 5 | AQICN `demo` token described as "works for development" | **`demo` token only returns data for Shanghai.** It does NOT work for Philippine cities. Token is required. Users get one free in minutes at `aqicn.org/api/`. |
| 6 | PSA API example path `DB/2C/0031C5E.px` | That path was fabricated. Confirmed real paths: population → `DB/DB__1A__PO/`, poverty → `DB/DB__1E__FY/`. Table filenames must be discovered via browse API — never hardcoded. |
| 7 | `mcp dev` as the inspector command | Correct command is `fastmcp dev src/ph_civic_data_mcp/server.py`. |
| 8 | `fastmcp>=2.0.0` in deps | **FastMCP is now at 3.2.4 (Apr 14, 2026).** FastMCP 3.0 shipped Feb 18, 2026 with breaking changes from 2.x. `@mcp.tool()` still works. Constructor no longer accepts `host`/`port`/transport kwargs — those now go in `mcp.run()`. Pin to `fastmcp>=3.0.0,<4.0.0`. **Do NOT use `fastmcp>=2.2.0`** — would install 3.x which breaks packages that assumed 2.x internals.` |
| 9 | PhilGEPS described as CSV downloads | PhilGEPS open data uses `.xlsx` Excel files (not CSV). Requires `openpyxl`. Use streaming download + `openpyxl` read-only mode for large files. |
| 10 | Cache key described vaguely | Explicit implementation provided: `hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest()`. |
| 11 | Return types on tools were Pydantic models | FastMCP serializes better when tools return `dict`. Use Pydantic internally for validation, return `model.model_dump()`. |
| 12 | AQICN URL used `https://` | Official AQICN docs use `http://api.waqi.info/` — not https. |
| 13 | Circular import risk in tool registration | Tools must share a single `mcp` instance. Pattern documented explicitly to avoid this common FastMCP pitfall. |

---

## Project Identity

| Field | Value |
|---|---|
| Package name | `ph-civic-data-mcp` |
| PyPI name | `ph-civic-data-mcp` |
| GitHub repo | `xmpuspus/ph-civic-data-mcp` |
| Transport | stdio only (zero hosting cost) |
| Language | Python 3.11+ |
| Primary framework | FastMCP 3.x (latest: 3.2.4 as of Apr 14, 2026) |
| Dev inspector | `fastmcp dev` |
| License | MIT |
| Author | Xavier Puspus |

---

## What This Is

A Python MCP server installable via `uvx` or `pip` that gives Claude Desktop, Claude Code, Cursor, and any MCP-compatible client direct access to live Philippine government data — earthquakes, weather, typhoons, procurement contracts, population statistics, and air quality.

**Zero prior art on GitHub or PyPI as of April 2026.** Closest: `panukatan/lindol` (R, PHIVOLCS only), `pagasa-parser` org (JS, PAGASA only). Neither is Python, multi-source, or MCP.

---

## Architecture

```
ph-civic-data-mcp/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── src/
│   └── ph_civic_data_mcp/
│       ├── __init__.py
│       ├── server.py            ← FastMCP instance + main()
│       ├── sources/
│       │   ├── __init__.py
│       │   ├── phivolcs.py      ← earthquake + volcano (SSL-disabled client)
│       │   ├── pagasa.py        ← weather (Open-Meteo fallback) + typhoon
│       │   ├── philgeps.py      ← procurement (xlsx streaming download)
│       │   ├── psa.py           ← population stats (PXWeb browse+query)
│       │   └── emb.py           ← air quality (AQICN — token required)
│       ├── models/
│       │   ├── __init__.py      ← Pydantic models for internal validation
│       │   ├── earthquake.py
│       │   ├── weather.py
│       │   ├── procurement.py
│       │   ├── population.py
│       │   └── air_quality.py
│       └── utils/
│           ├── __init__.py
│           ├── cache.py         ← TTL caches + cache_key()
│           ├── http.py          ← two clients: standard + PHIVOLCS (verify=False)
│           └── geo.py           ← PH region/province/municipality name normalizer
├── tests/
│   ├── __init__.py
│   ├── test_phivolcs.py
│   ├── test_pagasa.py
│   ├── test_philgeps.py
│   ├── test_psa.py
│   └── test_emb.py
└── smithery.yaml
```

---

## Dependency Stack

```toml
[project]
name = "ph-civic-data-mcp"
version = "0.1.0"
description = "MCP server for Philippine government data — earthquakes, weather, procurement, population, air quality"
requires-python = ">=3.11"
dependencies = [
    # FastMCP 3.x — current stable as of April 2026 is 3.2.4
    # Pin major version: 3.0 had breaking changes from 2.x (constructor kwargs moved to run())
    # @mcp.tool() decorator still works unchanged from 2.x
    "fastmcp>=3.0.0,<4.0.0",
    "httpx>=0.27.0",
    "beautifulsoup4>=4.12.0",
    "lxml>=5.0.0",
    "pydantic>=2.0.0",
    "python-dateutil>=2.9.0",
    "cachetools>=5.3.0",
    "openpyxl>=3.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[project.scripts]
ph-civic-data-mcp = "ph_civic_data_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## Environment Variables

| Variable | Status | Notes |
|---|---|---|
| `AQICN_TOKEN` | **Required for air quality** | Free token at `aqicn.org/api/`. Without it, `get_air_quality` raises a clear error with the registration URL. |
| `PAGASA_API_TOKEN` | Optional | From PAGASA — requires formal request. Without it, weather uses Open-Meteo fallback automatically. |

---

## Data Sources — Canonical Reference

### 1. PHIVOLCS Earthquakes

- **List page:** `https://earthquake.phivolcs.dost.gov.ph/`
- **SSL:** Site has broken cert. Use dedicated `PHIVOLCS_CLIENT` with `verify=False`. Never disable SSL on the global client.
- **HTML structure:** Table with columns: Date/Time | Latitude | Longitude | Depth (km) | Magnitude | Location. Parse with BeautifulSoup + lxml parser.
- **Bulletin hrefs:** Each row links to a bulletin HTML page. Extract href directly from the `<a>` tag. Do NOT construct URLs.
- **Confirmed real filenames from April 2026:**
  - `2026_0406_0857_B2F.html` — final, second bulletin
  - `2026_0406_072716_B1F.html` — 6-digit time in filename
  - `2026_0406_0703_B1.html` — no F suffix
  - `2026_0325_1714_B2.html` — non-final style
  - Pattern: `{YEAR}_{MMDD}_{HHMM[SS]}_B{N}[F].html`
- **404 handling:** ~2016 bulletins 404. Catch `httpx.HTTPStatusError(status=404)`, skip with warning.
- **No API key needed**

### 2. PAGASA Weather

**Primary (token-gated):** `https://tenday.pagasa.dost.gov.ph/api/v1/`

Confirmed endpoints:
```
GET /tenday/current          # Current weather per municipality/province/region
GET /tenday/full             # Full 10-day forecast
GET /tenday/issuance         # Latest forecast issuance datetime
GET /seasonal/province       # 6-month seasonal per province
GET /seasonal/region         # 6-month seasonal grouped by region
GET /seasonal/issuance       # Latest seasonal issuance
GET /location                # PSGC codes + municipality/province/region list
```

Query params: `municipality=`, `province=`, `region=`, `page=`
Auth header: `Authorization: Bearer {PAGASA_API_TOKEN}`

Token availability: Requires formal request to PAGASA. OSS projects may be denied. **Open-Meteo fallback makes this non-blocking.**

**Fallback (no token): Open-Meteo**

```python
# Free, no key, covers PH, returns forecast in metric units
url = (
    "https://api.open-meteo.com/v1/forecast"
    f"?latitude={lat}&longitude={lng}"
    "&daily=temperature_2m_max,temperature_2m_min,"
    "precipitation_sum,windspeed_10m_max,winddirection_10m_dominant"
    "&timezone=Asia%2FManila"
    f"&forecast_days={days}"
)
# Set data_source = "open_meteo" in response
```

Hardcode a dict of ~50 major PH cities with coordinates in `utils/geo.py`. For unlisted cities, use a geocoding fallback or return an error.

**Typhoon tracking:** Scrape `https://bagong.pagasa.dost.gov.ph/` active cyclone section. It renders active storm names and signal numbers in structured HTML, not Excel.

**PAGASA Excel files:** Permanently discontinued August 31, 2025. Do not reference them anywhere in the codebase.

### 3. PhilGEPS Procurement

- **Portal:** `https://open.philgeps.gov.ph/`
- **Format:** Bulk `.xlsx` Excel downloads — no streaming REST API
- **Strategy:**
  1. Scrape the portal's download page to discover current `.xlsx` download URLs (they change)
  2. Download with `httpx` in stream mode (files are 20–100MB)
  3. Parse with `openpyxl` using `read_only=True` for memory efficiency
  4. Filter in-memory, return top N matching records
  5. Cache parsed results for 6 hours

```python
# Streaming download pattern
async with PHIVOLCS_CLIENT.stream("GET", url) as response:
    with open(tmp_path, "wb") as f:
        async for chunk in response.aiter_bytes(chunk_size=65536):
            f.write(chunk)
# Then parse with openpyxl read_only
wb = openpyxl.load_workbook(tmp_path, read_only=True, data_only=True)
```

- **Available data:** Award notices, bid notices, procuring entity, contract amount, procurement mode, region, published date
- **No API key needed**

### 4. PSA OpenSTAT (PXWeb API)

- **Base URL:** `https://openstat.psa.gov.ph/PXWeb/api/v1/en/`
- **No API key needed**
- **Discovery pattern (Claude Code must follow this — do not hardcode table paths):**

```python
# Step 1: List top-level databases
GET /PXWeb/api/v1/en/DB/
# Returns: [{"id": "DB__1A__PO", "text": "Population"}, ...]

# Step 2: List tables in a domain
GET /PXWeb/api/v1/en/DB/DB__1A__PO/
# Returns: [{"id": "1001A6DTPG0.px", "text": "Total population by geographic location..."}, ...]

# Step 3: Get table metadata (variables, codes)
GET /PXWeb/api/v1/en/DB/DB__1A__PO/1001A6DTPG0.px
# Returns variable definitions, codes for regions/years

# Step 4: POST a query to get data
POST /PXWeb/api/v1/en/DB/DB__1A__PO/1001A6DTPG0.px
Content-Type: application/json
{
  "query": [
    {"code": "Geographic Location", "selection": {"filter": "item", "values": ["0"]}}
  ],
  "response": {"format": "json"}
}
```

- **Confirmed domain paths:**
  - Population → `DB/DB__1A__PO/`
  - Poverty → `DB/DB__1E__FY/`
- **Caveat:** Population data is 2020 Census. Poverty data last updated 2023. Always include `reference_year` and a `reference_note` field in responses.

### 5. AQICN Air Quality

- **API URL:** `http://api.waqi.info/feed/{city_slug}/?token={AQICN_TOKEN}`
  - Use `http://` not `https://` (per AQICN docs)
  - City slugs: lowercase, hyphenated — `manila`, `quezon-city`, `cebu-city`, `davao-city`, `makati`, `taguig`, `pasig`, `marikina`, `mandaluyong`
- **`demo` token:** Only returns Shanghai data. Not useful here. **Token is required.**
- **Free token:** `https://aqicn.org/data-platform/token/` — instant, 1,000 req/min
- **Response shape:**
  ```json
  {
    "status": "ok",
    "data": {
      "aqi": 82,
      "city": {"name": "Manila", "geo": [14.5995, 120.9842]},
      "dominentpol": "pm25",
      "time": {"s": "2026-04-18 10:00:00", "tz": "+08:00"},
      "iaqi": {
        "pm25": {"v": 82.0},
        "pm10": {"v": 33.0},
        "no2": {"v": 20.6},
        "so2": {"v": 3.6},
        "o3": {"v": 29.3},
        "co": {"v": 9.1}
      }
    }
  }
  ```
- **If token missing:** Raise `ValueError("AQICN_TOKEN not set. Get a free token at https://aqicn.org/data-platform/token/")`

### 6. NDRRMC (v0.2.0 — do not implement in v0.1.0)

- **Dashboard:** `https://monitoring-dashboard.ndrrmc.gov.ph/`
- XHR inspection reveals a structured JSON API backing the dashboard
- Would add: `get_active_disasters()`, `get_situational_report(event_id)`
- Flag in README as planned for v0.2.0

### 7. HazardHunterPH (v0.2.0 — do not implement in v0.1.0)

- **URL:** `https://hazardhunter.georisk.gov.ph/`
- ArcGIS REST API backend (inspectable via DevTools)
- Would add: `assess_hazard(lat, lng)` → flood/earthquake/landslide risk per coordinate
- Flag in README as planned for v0.2.0

---

## HTTP Client

```python
# utils/http.py
import httpx

# Standard client for all sources except PHIVOLCS
CLIENT = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, connect=10.0),
    headers={
        "User-Agent": "ph-civic-data-mcp/0.1.0 (+https://github.com/xmpuspus/ph-civic-data-mcp; civic data research)",
        "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    },
    follow_redirects=True,
)

# PHIVOLCS-specific client — SSL verification disabled due to broken cert chain
# Do NOT use this client for any other source
PHIVOLCS_CLIENT = httpx.AsyncClient(
    verify=False,
    timeout=httpx.Timeout(30.0, connect=10.0),
    headers={
        "User-Agent": "ph-civic-data-mcp/0.1.0 (+https://github.com/xmpuspus/ph-civic-data-mcp; civic data research)",
    },
    follow_redirects=True,
)

MAX_RETRIES = 3
RETRY_STATUSES = {429, 503, 504}
RETRY_DELAYS = [1, 2, 4]  # seconds, exponential
```

---

## Cache Implementation

```python
# utils/cache.py
import hashlib
import json
from cachetools import TTLCache

CACHES: dict[str, TTLCache] = {
    "phivolcs_earthquakes": TTLCache(maxsize=10, ttl=300),      # 5 min
    "phivolcs_volcanoes":   TTLCache(maxsize=10, ttl=1800),     # 30 min
    "pagasa_forecast":      TTLCache(maxsize=100, ttl=3600),    # 1 hour
    "pagasa_typhoons":      TTLCache(maxsize=5, ttl=600),       # 10 min
    "philgeps_data":        TTLCache(maxsize=50, ttl=21600),    # 6 hours
    "psa_population":       TTLCache(maxsize=50, ttl=86400),    # 24 hours
    "emb_air_quality":      TTLCache(maxsize=20, ttl=900),      # 15 min
}

def cache_key(params: dict) -> str:
    """Deterministic, collision-resistant cache key from a parameter dict."""
    payload = json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(payload.encode()).hexdigest()
```

---

## Server Entrypoint — Tool Registration Pattern

**FastMCP 3.x (current):** `@mcp.tool()` decorator is unchanged. However, the `FastMCP()` constructor no longer accepts transport kwargs (`host`, `port`, `log_level`, etc.) — those now go in `mcp.run()`. For stdio (our case) `mcp.run()` with no args is correct.

FastMCP requires all tools to be registered on the **same `mcp` instance**. Avoid the circular import pitfall with this pattern:

```python
# src/ph_civic_data_mcp/server.py
from fastmcp import FastMCP

# FastMCP 3.x: constructor only takes name, instructions, dependencies, etc.
# Transport kwargs like host/port are NOT accepted here (moved to mcp.run())
mcp = FastMCP(
    name="ph-civic-data-mcp",
    instructions="""
    You have access to live Philippine government data.
    
    Sources:
    - PHIVOLCS: Real-time earthquakes (5-min updates) and volcano alerts
    - PAGASA: 10-day weather forecast and active typhoon tracking
    - PhilGEPS: Government procurement contracts (cached 6h, not real-time)
    - PSA: Population (2020 Census) and poverty statistics (2023)
    - AQICN/EMB: Air quality for major Philippine cities (15-min updates)
    
    Always cite the data source and note freshness in responses.
    For emergencies: direct users to ndrrmc.gov.ph and official PHIVOLCS/PAGASA channels.
    """
)

# Lazy imports — source modules import `mcp` from here
# This avoids circular imports while keeping a single instance
def _register_tools():
    from ph_civic_data_mcp.sources import phivolcs  # noqa: F401
    from ph_civic_data_mcp.sources import pagasa    # noqa: F401
    from ph_civic_data_mcp.sources import philgeps  # noqa: F401
    from ph_civic_data_mcp.sources import psa       # noqa: F401
    from ph_civic_data_mcp.sources import emb       # noqa: F401

def main():
    _register_tools()
    mcp.run()  # stdio transport by default — no args needed

if __name__ == "__main__":
    main()
```

```python
# src/ph_civic_data_mcp/sources/phivolcs.py
from ph_civic_data_mcp.server import mcp  # import shared instance

@mcp.tool()
async def get_latest_earthquakes(...):
    ...
```

---

## MCP Tools — Frozen Specification

### PHIVOLCS

```python
@mcp.tool()
async def get_latest_earthquakes(
    min_magnitude: float = 1.0,
    limit: int = 20,
    region: str | None = None,
) -> list[dict]:
    """
    Get the latest earthquake events from PHIVOLCS.
    
    Args:
        min_magnitude: Minimum magnitude to include (default 1.0)
        limit: Max events to return (default 20, max 100)
        region: Filter by PH region/province/city name
                e.g. "Cebu", "Davao del Sur", "Metro Manila"
    
    Each result: datetime_pst, latitude, longitude, depth_km,
    magnitude, location, intensity, bulletin_url, source, data_retrieved_at.
    """

@mcp.tool()
async def get_earthquake_bulletin(bulletin_url: str) -> dict:
    """
    Get the full bulletin for a PHIVOLCS earthquake event.
    
    Args:
        bulletin_url: Full URL from get_latest_earthquakes bulletin_url field.
    
    Returns: magnitude, depth_km, location, datetime_pst,
    intensity_reports [{municipality, intensity}], full_text, url.
    """

@mcp.tool()
async def get_volcano_status(volcano_name: str | None = None) -> list[dict]:
    """
    Get current alert level for Philippine volcanoes.
    
    Args:
        volcano_name: e.g. "Mayon", "Taal", "Kanlaon", "Bulusan".
                      None returns all monitored volcanoes.
    
    Each result: name, alert_level (0-5), status_description,
    last_updated, bulletin_url.
    """
```

### PAGASA

```python
@mcp.tool()
async def get_weather_forecast(location: str, days: int = 3) -> dict:
    """
    Get weather forecast for a Philippine location.
    Uses PAGASA TenDay API if PAGASA_API_TOKEN is set,
    Open-Meteo otherwise (free, automatic fallback).
    
    Args:
        location: Municipality, city, or province name
        days: Forecast days (1-10, default 3)
    
    Returns: location, forecast_issued, data_source, days list.
    Each day: date, temp_min_c, temp_max_c, rainfall_mm,
    wind_speed_kph, wind_direction, weather_description.
    """

@mcp.tool()
async def get_active_typhoons() -> list[dict]:
    """
    Get active tropical cyclones in/near the Philippine Area of
    Responsibility (PAR). Returns empty list if none active.
    
    Each result: local_name, international_name, category,
    max_winds_kph, within_par, signal_numbers {province: level},
    bulletin_datetime.
    """

@mcp.tool()
async def get_weather_alerts(region: str | None = None) -> list[dict]:
    """
    Get active PAGASA weather alerts and advisories.
    
    Args:
        region: e.g. "NCR", "Region VII", "CALABARZON". None = all.
    
    Each result: alert_type, severity, description,
    affected_areas, issued_datetime, valid_until.
    """
```

### PhilGEPS

```python
@mcp.tool()
async def search_procurement(
    keyword: str,
    agency: str | None = None,
    region: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Search PH government procurement from PhilGEPS open data.
    Data is cached 6 hours — not real-time. First call may take
    10-30 seconds to download and parse the source Excel file.
    
    Args:
        keyword: Title, description, or commodity search term
        agency: Procuring entity (partial match, case-insensitive)
        region: PH region filter
        date_from / date_to: YYYY-MM-DD format
        limit: Max results (default 20, max 100)
    
    Each result: reference_number, title, agency, region,
    mode_of_procurement, approved_budget, currency, status,
    date_published, award_date, source.
    """

@mcp.tool()
async def get_procurement_summary(
    agency: str | None = None,
    region: str | None = None,
    year: int | None = None,
) -> dict:
    """
    Aggregate procurement statistics from PhilGEPS data.
    
    Returns: total_value_php, total_count, by_mode, by_region,
    top_agencies, reference_period.
    """
```

### PSA

```python
@mcp.tool()
async def get_population_stats(
    region: str | None = None,
    year: int | None = None,
) -> dict:
    """
    Philippine population from PSA OpenSTAT.
    Latest data: 2020 Census. Not real-time.
    
    Args:
        region: e.g. "NCR", "Region VII", "Cordillera Administrative Region"
                None = national total
        year: Reference year (2020 is latest available)
    
    Returns: region, year, population, growth_rate_pct,
    density_per_sqkm, source, reference_note.
    """

@mcp.tool()
async def get_poverty_stats(region: str | None = None) -> dict:
    """
    Poverty incidence from PSA. Latest data: 2023.
    Updated every 3 years.
    
    Args:
        region: PH region (None = national)
    
    Returns: region, poverty_incidence_pct, subsistence_incidence_pct,
    reference_year, source.
    """
```

### EMB / Air Quality

```python
@mcp.tool()
async def get_air_quality(city: str) -> dict:
    """
    Real-time air quality for a Philippine city via AQICN.
    Requires AQICN_TOKEN environment variable (free at aqicn.org/api/).
    
    Args:
        city: City name — Manila, Quezon City, Cebu City, Davao City,
              Makati, Taguig, Pasig, Marikina, Mandaluyong.
    
    Returns: city, station_name, aqi, aqi_category, pm25, pm10,
    no2, so2, o3, co, dominant_pollutant, health_advisory,
    measured_at, source.
    
    PH EMB AQI scale: 0-50 Good, 51-100 Fair, 101-150 Unhealthy
    for Sensitive Groups, 151-200 Unhealthy, 201-300 Very Unhealthy,
    301+ Hazardous.
    """
```

### Cross-Source Intelligence

```python
@mcp.tool()
async def assess_area_risk(location: str) -> dict:
    """
    Multi-hazard risk assessment combining PHIVOLCS + PAGASA + AQICN.
    Makes 3 parallel upstream calls. Expect 5-10 second response time.
    
    Args:
        location: Municipality, city, or province name
    
    Returns: location, earthquake_risk_level (Low/Moderate/High/Very High,
    based on recent 30-day activity), recent_earthquakes_30d,
    max_magnitude_30d, typhoon_signal_active, active_typhoon_name,
    air_quality_aqi, air_quality_category, assessment_datetime, caveats.
    
    Note: earthquake_risk_level is derived from recent seismic activity,
    not an official PHIVOLCS hazard assessment. Caveats list notes
    any failed sub-calls or data limitations.
    """
```

---

## Pydantic Models (Internal Validation Only)

Use these for internal parsing. Return `model.model_dump()` from tools.

```python
from pydantic import BaseModel
from datetime import datetime, date
from typing import Literal

class Earthquake(BaseModel):
    datetime_pst: datetime
    latitude: float
    longitude: float
    depth_km: float
    magnitude: float
    location: str
    intensity: str | None = None
    bulletin_url: str | None = None
    source: Literal["PHIVOLCS"] = "PHIVOLCS"
    data_retrieved_at: datetime

class DailyForecast(BaseModel):
    date: date
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    rainfall_mm: float | None = None
    wind_speed_kph: float | None = None
    wind_direction: str | None = None
    weather_description: str | None = None

class WeatherForecast(BaseModel):
    location: str
    forecast_issued: datetime
    days: list[DailyForecast]
    data_source: Literal["pagasa_api", "open_meteo"]
    data_retrieved_at: datetime

class Typhoon(BaseModel):
    local_name: str
    international_name: str | None = None
    category: str
    max_winds_kph: float | None = None
    within_par: bool
    signal_numbers: dict[str, int]
    bulletin_datetime: datetime

class ProcurementRecord(BaseModel):
    reference_number: str | None = None
    title: str
    agency: str
    region: str | None = None
    mode_of_procurement: str | None = None
    approved_budget: float | None = None
    currency: str = "PHP"
    status: str | None = None
    date_published: date | None = None
    award_date: date | None = None
    source: Literal["PhilGEPS"] = "PhilGEPS"

class PopulationStats(BaseModel):
    region: str
    year: int
    population: int
    growth_rate_pct: float | None = None
    density_per_sqkm: float | None = None
    reference_note: str | None = None
    source: Literal["PSA"] = "PSA"

class PovertyStats(BaseModel):
    region: str
    poverty_incidence_pct: float
    subsistence_incidence_pct: float | None = None
    reference_year: int
    source: Literal["PSA"] = "PSA"

class AirQuality(BaseModel):
    city: str
    station_name: str | None = None
    aqi: int
    aqi_category: str
    pm25: float | None = None
    pm10: float | None = None
    no2: float | None = None
    so2: float | None = None
    o3: float | None = None
    co: float | None = None
    dominant_pollutant: str | None = None
    health_advisory: str | None = None
    measured_at: datetime
    source: str = "AQICN"
    data_retrieved_at: datetime
```

---

## Installation & Config

**Install:**
```bash
uvx ph-civic-data-mcp
```

**Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
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

**Claude Code** (`.claude/settings.json`):
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

**Local dev:**
```bash
uv sync
fastmcp dev src/ph_civic_data_mcp/server.py
```

---

## smithery.yaml

```yaml
startCommand:
  type: stdio
  configSchema:
    type: object
    properties:
      aqicnToken:
        type: string
        description: "AQICN air quality API token. Required for get_air_quality. Free at aqicn.org/api/."
      pagasaApiToken:
        type: string
        description: "PAGASA TenDay API token (optional). Without this, weather uses Open-Meteo."
    required: []
  commandFunction: |-
    (config) => ({
      command: "uvx",
      args: ["ph-civic-data-mcp"],
      env: {
        ...(config.aqicnToken && { AQICN_TOKEN: config.aqicnToken }),
        ...(config.pagasaApiToken && { PAGASA_API_TOKEN: config.pagasaApiToken }),
      }
    })
```

---

## Build Order

Follow exactly. No parallelization. No skipping phases.

### Phase 0: Scaffold
1. Create `pyproject.toml` with exact deps above
2. Create all directories and empty `__init__.py` files
3. Implement `utils/http.py` — two clients, retry logic
4. Implement `utils/cache.py` — caches dict + `cache_key()`
5. Implement `utils/geo.py`:
   - Dict of PH region name aliases (`"Metro Manila"` → `"NCR"`, etc.)
   - Dict of ~50 major PH cities with `(lat, lng)` for Open-Meteo
   - `normalize_region(name: str) -> str` function
   - `city_to_coords(city: str) -> tuple[float, float] | None` function
6. Create `server.py` skeleton (FastMCP instance, `_register_tools()`, `main()`)
7. **Verify:** `python -c "from ph_civic_data_mcp.server import mcp; print(mcp.name)"` runs clean

### Phase 1: PHIVOLCS
1. Implement `sources/phivolcs.py` using `PHIVOLCS_CLIENT`
2. Fetch list page → parse table with BS4 + lxml
3. Extract earthquake rows + href links
4. Implement `get_latest_earthquakes` with filter logic
5. Implement `get_earthquake_bulletin` — fetch URL, parse text content
6. Implement `get_volcano_status` — scrape PHIVOLCS volcano section
7. Write `tests/test_phivolcs.py`:
   - Assert ≥ 5 earthquakes returned with no filters
   - Assert `magnitude` is `float`, `latitude` is `float`
   - Assert `bulletin_url` is non-empty string starting with `http`
   - Assert `data_retrieved_at` is present
8. ✅ **Checkpoint:** `fastmcp dev src/ph_civic_data_mcp/server.py` → call `get_latest_earthquakes` → real data returns

### Phase 2: PAGASA
1. Implement `sources/pagasa.py`
2. Implement Open-Meteo path first (guaranteed to work, no token):
   - `city_to_coords()` from `utils/geo.py` for location lookup
   - Map Open-Meteo response fields to `DailyForecast` model
   - Set `data_source = "open_meteo"`
3. Implement PAGASA TenDay path (token-gated):
   - Check `os.environ.get("PAGASA_API_TOKEN")` — use if present
   - Set `data_source = "pagasa_api"`
4. Implement typhoon scraper (`bagasa.pagasa.dost.gov.ph`)
5. Implement weather alerts scraper
6. Write `tests/test_pagasa.py`:
   - Assert forecast for "Manila" returns ≥ 1 day with `temp_max_c` not None
   - Assert Open-Meteo fallback works with no env var
7. ✅ **Checkpoint:** Verify fallback works by unsetting `PAGASA_API_TOKEN`

### Phase 3: PhilGEPS
1. Implement `sources/philgeps.py`
2. Scrape `open.philgeps.gov.ph` to get current download URLs
3. Implement streaming `.xlsx` download + `openpyxl` read-only parse
4. Implement keyword + filter search in-memory
5. Implement both tools
6. Write `tests/test_philgeps.py`:
   - Assert results returned for keyword `"flood control"`
   - Assert cache hit on second call (time it — should be <100ms)
7. ✅ **Checkpoint:** Test with keyword `"road"` — expect ≥ 1 result

### Phase 4: PSA
1. Implement `sources/psa.py`
2. Implement PXWeb browse → discover table list dynamically
3. POST query for population data, parse JSON response
4. Implement `get_population_stats` with region filter
5. Repeat for poverty domain
6. Write `tests/test_psa.py`:
   - Assert NCR population > 10,000,000
   - Assert `reference_note` field present with census year
7. ✅ **Checkpoint:** Call `get_population_stats(region="NCR")` — verify number is plausible

### Phase 5: Air Quality
1. Implement `sources/emb.py`
2. On import: check `AQICN_TOKEN` — if absent, tools still register but raise `ValueError` with helpful message on call
3. Hardcode city → AQICN slug mapping
4. Call AQICN API, parse `iaqi` fields
5. Map AQI to PH EMB category string
6. Write `tests/test_emb.py`:
   - Skip if `AQICN_TOKEN` not set (use `pytest.mark.skipif`)
   - Assert Manila AQI is integer ≥ 0
7. ✅ **Checkpoint:** Call with real token — verify Manila data returned

### Phase 6: Cross-Source
1. Implement `assess_area_risk` in `server.py`
2. Use `asyncio.gather` to call phivolcs + pagasa + emb in parallel
3. Handle partial failures: if one source fails, continue with others, add failure to `caveats`
4. Map recent earthquake count/magnitude to risk level string

### Phase 7: Integration Test
1. `fastmcp dev` — confirm all 11 tools appear
2. Call each tool once — no crashes
3. Verify all responses include `data_retrieved_at`

### Phase 8: Package & Publish
1. `python -m build`
2. `twine check dist/*`
3. `uvx --from dist/ph_civic_data_mcp-0.1.0-py3-none-any.whl ph-civic-data-mcp` — runs cleanly
4. `twine upload dist/*` to PyPI
5. Write `README.md`
6. Write `smithery.yaml`

### Phase 9: End-to-End Validation in Claude Desktop

All 9 prompts must return real data:

1. "What earthquakes happened in the Philippines in the last 24 hours?"
2. "Is Taal volcano active right now?"
3. "What's the 3-day weather forecast for Quezon City?"
4. "Are there active typhoons in the Philippines right now?"
5. "Search PhilGEPS for flood control contracts in Pampanga"
6. "What is the population of Region VII based on the PSA?"
7. "What is the poverty incidence in the Bicol Region?"
8. "What is the air quality in Manila right now?"
9. "Give me a multi-hazard risk profile for Leyte"

---

## README.md Structure

1. **Tagline** — "The first MCP server for Philippine government data — earthquakes, weather, typhoons, procurement, population, and air quality in your AI agent."
2. **Why this exists** — 2-3 sentences
3. **Install** — `uvx ph-civic-data-mcp`
4. **Setup** — Claude Desktop JSON config
5. **What you can ask** — 6 example prompts
6. **Data sources** — table: source | data | update frequency | auth
7. **All tools** — table: tool | description | key params
8. **Environment variables** — table with Required/Optional column
9. **Data freshness warnings** — population is 2020, poverty is 2023, procurement not real-time
10. **Development** — `uv sync`, `fastmcp dev`, `pytest`
11. **Limitations** — PAGASA token gate, AQICN token required, PhilGEPS not real-time
12. **v0.2.0 roadmap** — NDRRMC situational reports, HazardHunterPH coordinate assessment
13. **Prior art** — Credit `panukatan/lindol` and `pagasa-parser`
14. **License** — MIT

---

## Non-Negotiable Constraints

- **No `requests`** — async `httpx` only
- **No global SSL bypass** — `verify=False` only on `PHIVOLCS_CLIENT`
- **No disk writes** — in-memory cache only
- **No PAGASA Excel files** — discontinued Aug 2025
- **No `demo` AQICN token** — fails for PH, raise clear error
- **No hardcoded PSA table paths** — use browse API to discover
- **No fabricated data** — empty list + `caveats` on upstream failure
- **No crashes on 404/503** — catch, log to stderr, return graceful result
- **Always include** `source` and `data_retrieved_at` in every tool response
- **Return `dict`** from tools, not Pydantic models (use `.model_dump()`)
- **Tool names are frozen** — do not rename
- **Python 3.11+ syntax** — `str | None`, not `Optional[str]`

---

## Known Risks

| Risk | Mitigation |
|---|---|
| PHIVOLCS HTML structure changes | Tests fail loudly with `DataSourceError`; structure is stable since 2018 |
| PAGASA TenDay token denied | Open-Meteo fallback is non-blocking and covers all PH cities |
| PhilGEPS Excel URL changes | Scrape download page for URL; log exact URL used |
| AQICN `demo` token confusion | Error message includes registration URL |
| PSA PXWeb table IDs change | Browse API discovery pattern handles path changes |
| Open-Meteo rate limits | 10,000 req/day, 5,000/hour, 600/minute on free tier. Non-commercial use only. 15-min cache keeps usage ~96 req/day. **Note: ph-civic-data-mcp is MIT-licensed open-source — this qualifies as non-commercial.** If the project ever generates revenue, must switch to paid tier or self-host. |
| PhilGEPS Excel too large | `openpyxl` read-only streaming mode; chunk processing |

---

## Validation Checklist (per phase)

- [ ] Tool callable via `fastmcp dev` inspector with no errors
- [ ] Response includes `source` and `data_retrieved_at`
- [ ] Network failure returns graceful result, not traceback
- [ ] Cache hit on second identical call (timing check)
- [ ] No hardcoded secrets
- [ ] `verify=False` only on `PHIVOLCS_CLIENT`
- [ ] Test passes against live data source

---

## Publishing Checklist

- [ ] `python -m build` succeeds
- [ ] `twine check dist/*` clean
- [ ] `uvx` install from wheel works
- [ ] `twine upload` to PyPI
- [ ] GitHub public, MIT license, topics: `mcp`, `philippines`, `phivolcs`, `pagasa`, `philgeps`, `civic-tech`
- [ ] Glama auto-indexes (check within 48h)
- [ ] Smithery submission via `smithery.ai`
- [ ] PulseMCP manual at `pulsemcp.com`
- [ ] MCP.so submission
- [ ] PR to `wong2/awesome-mcp-servers`
- [ ] Post to Data Engineering Pilipinas Facebook (~38k members)
- [ ] Post to DEVCON Philippines
- [ ] LinkedIn post

---

*Last validated: April 18, 2026. All corrections from live source verification applied. Built by Xavier Puspus. Not affiliated with PHIVOLCS, PAGASA, PhilGEPS, PSA, or EMB.*
