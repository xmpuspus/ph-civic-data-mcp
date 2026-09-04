"""FastMCP server entrypoint. Single shared `mcp` instance; sources import it lazily."""

from __future__ import annotations

from datetime import datetime, timezone

from ph_civic_data_mcp import __version__

from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils import health
from ph_civic_data_mcp.utils.cache import CACHES


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
        "freshness": (
            "Per-table vintage. Population: 2024 Census of Population "
            "(reference date 2024-07-01), with 2010, 2015 and 2020 by year. "
            "Poverty: 2023. "
            "CPI/inflation: latest published month (lagged). Labor Force "
            "Survey: latest published quarter. Health (1D): per-indicator."
        ),
        "cache_ttl_seconds": 86400,
        "license": "PSA Open Data terms",
    },
    {
        "source": "Area profile (auto-stitch)",
        "source_url": "https://openstat.psa.gov.ph/PXWeb/api/v1/en/",
        "freshness": (
            "Composed live from PSGC + PSA + PhilGEPS + PHIVOLCS + PAGASA; "
            "each block carries its own reference period"
        ),
        "cache_ttl_seconds": 3600,
        "license": "Public — PSA OpenSTAT, PSGC, PhilGEPS, PHIVOLCS, PAGASA",
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
    {
        "source": "PSIC",
        "source_url": "https://psa.gov.ph/classification/psic/search-results",
        "freshness": "PSIC revisions change on the order of years",
        "cache_ttl_seconds": 86400,
        "license": "PSA Philippine Standard Industrial Classification (PSIC), CC BY 4.0",
    },
]


@mcp.tool(
    title="Server health and data-source catalog",
    tags={"health", "metadata", "version"},
    annotations={
        "title": "Server health and data-source catalog",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
        "destructiveHint": False,
    },
)
async def get_data_freshness() -> dict:
    """Server health and data-source catalog probe.

    Doubles as the version and health endpoint: returns server_version so an
    agent can confirm which release it is talking to. Also returns the full
    upstream-source catalog: cache TTLs, freshness expectations, and
    licenses. Use it to judge whether a stale cached response is fine or a
    re-fetch is needed.

    Examples:
      get_data_freshness()  # the only call, it takes no arguments

    On failure: this tool calls no upstream itself, so it always returns a
    dict. source_health is a process-local, in-memory registry, not a
    database. It starts empty on a cold process and fills as
    fetch_with_retry runs calls, so a fresh restart always reports an empty
    dict here.

    Returns: server_version, server_name, transport, tool_count, asof,
    sources (list of {source, source_url, freshness, cache_ttl_seconds,
    license}), source_health (per-host {last_success_at, last_failure_at,
    last_error, last_latency_ms, success_count, failure_count}), cache_age
    (per-cache {size, ttl_seconds}), note.
    """
    tools = await mcp.list_tools()
    return {
        "server_version": __version__,
        "server_name": "ph-civic-data-mcp",
        "transport": "stdio",
        "tool_count": len(tools),
        "asof": datetime.now(timezone.utc).isoformat(),
        "sources": SOURCE_CATALOG,
        "source_health": health.snapshot(),
        "cache_age": {
            name: {"size": len(cache), "ttl_seconds": cache.ttl} for name, cache in CACHES.items()
        },
        "note": (
            "Cache TTLs are per-source. Times are server-side wall clock. "
            "Upstream freshness varies independently of our cache window. "
            "Call this tool to confirm the running server version when "
            "debugging agent behaviour."
        ),
    }


@mcp.resource(
    "data://ph-civic/source-catalog",
    description=(
        "The full upstream-source catalog: source name, canonical URL, "
        "freshness expectation, cache TTL, and license for every data source "
        "this server composes. Same payload as get_data_freshness.sources."
    ),
    mime_type="application/json",
)
def source_catalog_resource() -> list[dict]:
    return SOURCE_CATALOG


@mcp.resource(
    "data://ph-civic/civic-framing",
    description=(
        "The civic-tech framing and disclaimer that applies to every "
        "accountability / procurement result from this server."
    ),
    mime_type="text/plain",
)
def civic_framing_resource() -> str:
    return (
        "This server is for civic research and accountability work. "
        "Heuristic indicators are statistical only — patterns may have "
        "legitimate explanations. Present procurement/infra results as "
        "starting points for further investigation, never as evidence of "
        "wrongdoing. Use defensible language ('flagged for review', "
        "'warrants further investigation') and never direct accusations. "
        "Cite source_url for every factual claim. All data sourced from "
        "public records (PSGC, PHIVOLCS, PAGASA, PhilGEPS, PSA, and open "
        "scientific feeds)."
    )


@mcp.prompt(
    name="area_briefing",
    description=(
        "Build a sourced civic briefing for one Philippine location using "
        "get_area_profile, with hazard and economy context."
    ),
)
def area_briefing(location: str) -> str:
    return (
        f"Build a civic data briefing for {location}. Call get_area_profile "
        f"with location='{location}' first. Then summarize: resolved PSGC "
        "identity (mention alternatives if the name was ambiguous), "
        "demographics, economy (note each reference period — PSA publishes "
        "with a lag), procurement activity with the per-100k normalization, "
        "hazard status (earthquakes, typhoons, volcano alerts), and the "
        "3-day weather outlook. Cite source_url for every factual claim and "
        "surface every caveat the profile returns. If any block shows "
        "upstream_error, say the source was unavailable rather than "
        "reporting empty data."
    )


@mcp.prompt(
    name="infra_accountability_scan",
    description=(
        "Run an infra-spending accountability scan for a region or province "
        "using search_infra_projects + flag_infra_anomalies, with the "
        "required defensible framing."
    ),
)
def infra_accountability_scan(area: str) -> str:
    return (
        f"Run an infrastructure accountability scan for {area}. Call "
        f"summarize_infra_spending and flag_infra_anomalies for '{area}'. "
        "Report flagged notices as items that warrant further review — "
        "never as evidence of wrongdoing. For each flag, quote the rule "
        "that fired and its evidence string, and note that "
        "high_cost_no_published_progress is a cost-threshold transparency "
        "flag (the public listing publishes no progress data for any "
        "notice). Include the disclaimer the tools return, cite source_url, "
        "and state the notice-window limitation (latest ~100 notices, not a "
        "complete census of projects)."
    )


_TOOLS_REGISTERED = False


@mcp.prompt(
    name="psa_data_explorer",
    title="Explore the PSA OpenSTAT catalog",
    description=(
        "Walk the PSA OpenSTAT statistical catalog safely: browse to a topic, "
        "describe the table, then run one bounded query with explicit codes."
    ),
    tags={"psa", "openstat", "statistics", "philippines"},
)
def psa_data_explorer(topic: str) -> str:
    return (
        f"Find Philippine statistics on '{topic}' in PSA OpenSTAT, then report "
        "the figures. Work in this order and do not skip a step.\n\n"
        "1. Call browse_psa_catalog() with no path to list the subjects. Pick "
        f"the subject whose title fits '{topic}'. Call browse_psa_catalog again "
        "with that entry's `path`, and keep going until entries come back with "
        'type "dataset". Folder depth varies by subject, so do not assume two '
        "levels.\n"
        "2. Call describe_psa_dataset(dataset_path) on the dataset you chose. "
        "Read the dimensions, the value codes, and total_cells.\n"
        "3. Build `selections` with an explicit list of value codes for EVERY "
        'dimension. "all" and "*" are rejected, and an unselected dimension '
        "would expand to all of its values and trip the 1000-cell cap. Prefer "
        "the most recent code in a time dimension.\n"
        "4. Call query_psa_dataset(dataset_path, selections). If it returns "
        "validation_error, fix the selection it names and retry once.\n"
        "5. Report the figures with the table title, the source_url, and the "
        "reference_period the query returns. State the reference period as the "
        "vintage of the data. Never present the OpenSTAT publication timestamp "
        "as the data vintage; they are different things.\n"
        "6. Surface every entry in `caveats`. A null value means PSA published "
        "'..' for that cell, which is a missing value and never a zero. If the "
        "response carries upstream_error, say OpenSTAT was unreachable rather "
        "than reporting no data."
    )


def _register_tools() -> None:
    """Import every source module so its @mcp.tool decorators run.

    Called once at the bottom of this module, so a plain `import
    ph_civic_data_mcp.server` exposes the whole tool surface. Before v0.6.0
    only main() called this, so `fastmcp inspect` and any library import saw
    1 tool of 29: inspect imports the file and never calls main().

    Still public and still idempotent. The release-smoke workflow on already
    published wheels calls it by name.
    """
    global _TOOLS_REGISTERED
    if _TOOLS_REGISTERED:
        return
    _TOOLS_REGISTERED = True

    # Imported here, not at module top, so each source module can do
    # `from ph_civic_data_mcp.server import mcp` without a circular import.
    from ph_civic_data_mcp.sources import phivolcs  # noqa: F401
    from ph_civic_data_mcp.sources import pagasa  # noqa: F401
    from ph_civic_data_mcp.sources import philgeps  # noqa: F401
    from ph_civic_data_mcp.sources import psa  # noqa: F401
    from ph_civic_data_mcp.sources import psgc  # noqa: F401
    from ph_civic_data_mcp.sources import infra  # noqa: F401
    from ph_civic_data_mcp.sources import cross_source  # noqa: F401
    from ph_civic_data_mcp.sources import autostitch  # noqa: F401
    from ph_civic_data_mcp.sources import nasa_power  # noqa: F401
    from ph_civic_data_mcp.sources import open_meteo_aq  # noqa: F401
    from ph_civic_data_mcp.sources import modis_ndvi  # noqa: F401
    from ph_civic_data_mcp.sources import usgs  # noqa: F401
    from ph_civic_data_mcp.sources import ibtracs  # noqa: F401
    from ph_civic_data_mcp.sources import world_bank  # noqa: F401
    from ph_civic_data_mcp.sources import psa_catalog  # noqa: F401
    from ph_civic_data_mcp.sources import compare  # noqa: F401
    from ph_civic_data_mcp.sources import psic  # noqa: F401


def main() -> None:
    _register_tools()
    mcp.run()


# Register at import time. Everything the source modules import from this
# module is defined above, so this call must stay the last statement before
# the __main__ guard.
_register_tools()


if __name__ == "__main__":
    # When run as `python -m ph_civic_data_mcp.server`, Python loads this file twice
    # (once as __main__, once as ph_civic_data_mcp.server) which creates two FastMCP
    # instances. Tool decorators register against the ph_civic_data_mcp.server instance
    # while __main__ runs its own empty instance. Re-route through the proper module
    # so the console script path and `-m` invocation both register tools correctly.
    from ph_civic_data_mcp.server import main as _main

    _main()
