"""FastMCP server entrypoint. Single shared `mcp` instance; sources import it lazily."""

from __future__ import annotations

from fastmcp import FastMCP

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
    """,
)


def _register_tools() -> None:
    """Lazy imports so source modules can import the shared `mcp` instance above."""
    from ph_civic_data_mcp.sources import phivolcs  # noqa: F401
    from ph_civic_data_mcp.sources import pagasa  # noqa: F401
    from ph_civic_data_mcp.sources import philgeps  # noqa: F401
    from ph_civic_data_mcp.sources import psa  # noqa: F401
    from ph_civic_data_mcp.sources import emb  # noqa: F401
    from ph_civic_data_mcp.sources import cross_source  # noqa: F401


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
