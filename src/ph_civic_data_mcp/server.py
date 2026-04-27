"""FastMCP server entrypoint. Single shared `mcp` instance; sources import it lazily."""

from __future__ import annotations

from datetime import datetime, timezone

from fastmcp import FastMCP

from ph_civic_data_mcp import __version__

mcp = FastMCP(
    name="ph-civic-data-mcp",
    instructions="""
    You have access to live Philippine civic data, accountability, weather,
    and Earth-observation sources.

    Philippine government sources:
    - PSGC: Resolve free-text PH place names to canonical PSA Standard Geographic Codes
    - PHIVOLCS: Real-time earthquakes (5-min updates) and volcano alerts
    - PAGASA: 10-day weather forecast and active typhoon tracking
    - PhilGEPS: Government procurement contracts and infra spending notices
    - PSA: Population (2020 Census) and poverty statistics (2023)

    Accountability tools (v0.3.0):
    - search_infra_projects / get_infra_project / summarize_infra_spending —
      Filtered PhilGEPS notices for construction / road / bridge / flood control
    - flag_infra_anomalies — heuristic indicators (high_cost_no_progress,
      hazard_overlap, duplicate_titles_same_agency). Indicators only — patterns
      may have legitimate explanations.

    Open-data + NASA / NOAA / World Bank sources:
    - NASA POWER: Daily solar irradiance + climate (temp, precip, wind) at any lat/lng
    - Open-Meteo Air Quality: PM2.5/PM10/NO2/SO2/O3/CO + AQI (no auth)
    - NASA MODIS via ORNL: NDVI + EVI vegetation indices at any lat/lng
    - USGS FDSN: Philippine-region earthquakes from global network (cross-ref to PHIVOLCS)
    - NOAA IBTrACS: Historical tropical cyclone tracks through Philippine AOR
    - World Bank Open Data: Philippine macro indicators (GDP, poverty, inflation, etc.)

    Always cite the data source and note freshness in responses.
    For emergencies: direct users to ndrrmc.gov.ph and official PHIVOLCS/PAGASA channels.
    """,
)


SOURCE_CATALOG: list[dict] = [
    {
        "source": "PSGC",
        "source_url": "https://psgc.gitlab.io/api/",
        "freshness": "Updated when PSA publishes new PSGC version (annual or quarterly)",
        "cache_ttl_seconds": 86400,
        "license": "Public domain (PSA Philippine Standard Geographic Code)",
    },
    {
        "source": "PHIVOLCS earthquakes",
        "source_url": "https://earthquake.phivolcs.dost.gov.ph/",
        "freshness": "5-minute table refresh; bulletins published per event",
        "cache_ttl_seconds": 300,
        "license": "Public — PHIVOLCS public bulletin pages",
    },
    {
        "source": "PHIVOLCS volcanoes",
        "source_url": "https://wovodat.phivolcs.dost.gov.ph/bulletin/list-of-bulletin",
        "freshness": "Daily bulletins per active volcano",
        "cache_ttl_seconds": 1800,
        "license": "Public — PHIVOLCS public bulletin pages",
    },
    {
        "source": "PAGASA forecast",
        "source_url": "https://tenday.pagasa.dost.gov.ph/api/v1 (Open-Meteo fallback)",
        "freshness": "Issued twice daily; Open-Meteo updates hourly",
        "cache_ttl_seconds": 3600,
        "license": "Open-Meteo CC-BY 4.0 / PAGASA terms",
    },
    {
        "source": "PAGASA typhoons",
        "source_url": "https://bagong.pagasa.dost.gov.ph/",
        "freshness": "Bulletin every 3-6 hours when storms are active",
        "cache_ttl_seconds": 600,
        "license": "Public — PAGASA bulletin pages",
    },
    {
        "source": "PhilGEPS notices / infra",
        "source_url": "https://www.philgeps.gov.ph/",
        "freshness": "Latest ~100 bid notices, refreshed every 6h",
        "cache_ttl_seconds": 21600,
        "license": "Public — PhilGEPS open notice listing",
    },
    {
        "source": "PSA OpenSTAT",
        "source_url": "https://openstat.psa.gov.ph/PXWeb/api/v1/en/",
        "freshness": "Population: 2020 Census. Poverty: 2023.",
        "cache_ttl_seconds": 86400,
        "license": "PSA Open Data terms",
    },
    {
        "source": "NASA POWER",
        "source_url": "https://power.larc.nasa.gov/api/temporal/daily/point",
        "freshness": "Daily, ~3-day latency",
        "cache_ttl_seconds": 86400,
        "license": "Public domain (NASA)",
    },
    {
        "source": "Open-Meteo air quality",
        "source_url": "https://air-quality-api.open-meteo.com/v1/air-quality",
        "freshness": "Hourly",
        "cache_ttl_seconds": 900,
        "license": "Open-Meteo CC-BY 4.0",
    },
    {
        "source": "NASA MODIS NDVI",
        "source_url": "https://modis.ornl.gov/rst/api/v1/",
        "freshness": "16-day composite, ~14-day latency",
        "cache_ttl_seconds": 86400,
        "license": "Public domain (NASA / ORNL)",
    },
    {
        "source": "USGS FDSN",
        "source_url": "https://earthquake.usgs.gov/fdsnws/event/1/",
        "freshness": "Real-time global feed",
        "cache_ttl_seconds": 600,
        "license": "Public domain (USGS)",
    },
    {
        "source": "NOAA IBTrACS",
        "source_url": "https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/v04r00/access/csv/",
        "freshness": "Annual update",
        "cache_ttl_seconds": 86400,
        "license": "Public domain (NOAA)",
    },
    {
        "source": "World Bank Open Data",
        "source_url": "https://api.worldbank.org/v2/",
        "freshness": "Annual; lag varies by indicator",
        "cache_ttl_seconds": 86400,
        "license": "World Bank Open Data CC-BY 4.0",
    },
]


@mcp.tool()
async def get_data_freshness() -> dict:
    """Return the catalog of upstream data sources used by this MCP server,
    with their cache TTLs, freshness expectations, and licenses.

    Returns: server_version, asof, sources (list of {source, source_url,
    freshness, cache_ttl_seconds, license}).
    """
    return {
        "server_version": __version__,
        "asof": datetime.now(timezone.utc).isoformat(),
        "sources": SOURCE_CATALOG,
        "note": (
            "Cache TTLs are per-source. Times are server-side wall clock. "
            "Upstream freshness varies independently of our cache window."
        ),
    }


def _register_tools() -> None:
    """Lazy imports so source modules can import the shared `mcp` instance above."""
    from ph_civic_data_mcp.sources import phivolcs  # noqa: F401
    from ph_civic_data_mcp.sources import pagasa  # noqa: F401
    from ph_civic_data_mcp.sources import philgeps  # noqa: F401
    from ph_civic_data_mcp.sources import psa  # noqa: F401
    from ph_civic_data_mcp.sources import psgc  # noqa: F401
    from ph_civic_data_mcp.sources import infra  # noqa: F401
    from ph_civic_data_mcp.sources import cross_source  # noqa: F401
    from ph_civic_data_mcp.sources import nasa_power  # noqa: F401
    from ph_civic_data_mcp.sources import open_meteo_aq  # noqa: F401
    from ph_civic_data_mcp.sources import modis_ndvi  # noqa: F401
    from ph_civic_data_mcp.sources import usgs  # noqa: F401
    from ph_civic_data_mcp.sources import ibtracs  # noqa: F401
    from ph_civic_data_mcp.sources import world_bank  # noqa: F401


def main() -> None:
    _register_tools()
    mcp.run()


if __name__ == "__main__":
    # When run as `python -m ph_civic_data_mcp.server`, Python loads this file twice
    # (once as __main__, once as ph_civic_data_mcp.server) which creates two FastMCP
    # instances. Tool decorators register against the ph_civic_data_mcp.server instance
    # while __main__ runs its own empty instance. Re-route through the proper module
    # so the console script path and `-m` invocation both register tools correctly.
    from ph_civic_data_mcp.server import main as _main

    _main()
