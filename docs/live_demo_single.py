"""One-source focused demo: accepts a source key on argv, runs that subset of
tool calls against a live PyPI server via the MCP protocol, renders with Rich.

Usage:
    uv run python docs/live_demo_single.py phivolcs
    uv run python docs/live_demo_single.py pagasa
    uv run python docs/live_demo_single.py philgeps
    uv run python docs/live_demo_single.py psa
    uv run python docs/live_demo_single.py combined
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

sys.path.insert(0, os.path.dirname(__file__))
from live_demo import RENDERERS, _unwrap  # noqa: E402

SUITES: dict[str, list[tuple[str, str, dict, str]]] = {
    "phivolcs": [
        ("latest earthquakes", "get_latest_earthquakes",
         {"min_magnitude": 2.0, "limit": 5}, "earthquakes"),
        ("Mayon volcano status", "get_volcano_status",
         {"volcano_name": "Mayon"}, "volcano"),
    ],
    "pagasa": [
        ("3-day forecast · Cebu City", "get_weather_forecast",
         {"location": "Cebu City", "days": 3}, "weather"),
        ("active typhoons in PAR", "get_active_typhoons", {}, "typhoons"),
    ],
    "philgeps": [
        ("procurement summary", "get_procurement_summary", {}, "procurement"),
    ],
    "psa": [
        ("population · NCR (2020 Census)", "get_population_stats",
         {"region": "NCR"}, "population"),
        ("poverty · national (2023)", "get_poverty_stats", {}, "poverty"),
    ],
    "aqicn": [
        ("air quality · Manila", "get_air_quality",
         {"city": "Manila"}, "air_quality"),
    ],
    "combined": [
        ("multi-hazard risk · Manila", "assess_area_risk",
         {"location": "Manila"}, "risk"),
    ],
}


async def main(suite_key: str) -> None:
    suite = SUITES[suite_key]
    console = Console()
    console.print()
    console.print(
        Panel.fit(
            Text.assemble(
                ("ph-civic-data-mcp  ·  ", "dim"),
                (suite_key.upper(), "bold cyan"),
                ("  ·  live from PyPI", "dim"),
            ),
            border_style="cyan",
        )
    )

    transport = StdioTransport(
        command="uvx",
        args=["ph-civic-data-mcp"],
        env={**os.environ},
    )
    client = Client(transport)

    async with client:
        with console.status("[cyan]MCP handshake...", spinner="dots"):
            tools = await client.list_tools()
        console.print(f"[green]● connected[/green] · [bold]{len(tools)}[/bold] tools\n")

        for idx, (title, tool_name, args, key) in enumerate(suite, 1):
            console.print(Text.assemble(
                (f"  [{idx}/{len(suite)}] ", "dim"),
                (title, "bold"),
            ))
            with console.status(
                f"[yellow]→ calling[/yellow] [bold]{tool_name}[/bold]({args})",
                spinner="dots12",
            ):
                t0 = time.perf_counter()
                result = await client.call_tool(tool_name, args)
                elapsed = (time.perf_counter() - t0) * 1000
            data = _unwrap(result)
            console.print(RENDERERS[key](data))
            console.print(Text.assemble(
                ("  ● ", "green"),
                (f"{tool_name}  ", "bold"),
                (f"{elapsed:.0f} ms", "yellow"),
                ("  · real MCP protocol · live PyPI", "dim"),
            ))
            console.print()
            await asyncio.sleep(0.3)


if __name__ == "__main__":
    suite = sys.argv[1] if len(sys.argv) > 1 else "phivolcs"
    if suite not in SUITES:
        print(f"unknown suite: {suite}  (options: {list(SUITES)})", file=sys.stderr)
        sys.exit(2)
    asyncio.run(main(suite))
