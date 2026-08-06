"""v0.5.0 demo: the resolver upgrades + volcano-aware risk profile, one
Rich-rendered pass against the freshly published PyPI package.

Shows the audit-driven v0.5.0 surfaces an agent actually feels:
- nickname aliases ("QC" -> Quezon City)
- "X City" <-> "City of X" bridging ("Manila City" resolved to Danao City,
  Cebu on v0.4.0 — score 0.61; now City of Manila at 1.0)
- `alternatives` on ambiguous names ("San Juan")
- volcano alert levels stitched into assess_area_risk

Run:
    uv run python docs/live_demo_v050.py

Record to GIF:
    vhs docs/demo_v050.tape
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
from rich.table import Table
from rich.text import Text

sys.path.insert(0, os.path.dirname(__file__))
from live_demo import render_risk  # noqa: E402


def _unwrap(result: object) -> object:
    if hasattr(result, "data"):
        return result.data
    if hasattr(result, "structuredContent"):
        return result.structuredContent
    return result


def _render_resolve(payload: dict, note: str | None = None) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("name", f"[bold]{payload.get('name')}[/bold]")
    table.add_row("psgc_code", str(payload.get("psgc_code")))
    table.add_row("level", str(payload.get("level")))
    table.add_row("match_score", f"{payload.get('match_score', '?')}")
    alternatives = payload.get("alternatives") or []
    if alternatives:
        alt_str = "  ".join(f"{a.get('name')} ({a.get('psgc_code')})" for a in alternatives[:3])
        table.add_row("alternatives", f"[yellow]{alt_str}[/yellow]")
    return Panel(
        table,
        title=(
            f"[bold cyan]resolve_ph_location[/bold cyan] · "
            f"[white]'{payload.get('query', '')}'[/white]"
        )
        if payload.get("query")
        else "[bold cyan]resolve_ph_location[/bold cyan]",
        subtitle=f"[dim]{note}[/dim]" if note else None,
        border_style="cyan",
    )


SCRIPT: list[tuple[str, str, dict, str | None]] = [
    ("alias · 'QC'", "resolve_ph_location", {"query": "QC"}, None),
    (
        "city-form bridge · 'Manila City'",
        "resolve_ph_location",
        {"query": "Manila City"},
        "v0.4.0 resolved this query to Danao City, Cebu (score 0.61)",
    ),
    (
        "ambiguous name · 'San Juan'",
        "resolve_ph_location",
        {"query": "San Juan"},
        "runner-up candidates are no longer silent",
    ),
    ("multi-hazard risk · Albay", "assess_area_risk", {"location": "Albay"}, None),
]


async def main() -> None:
    console = Console()
    console.print()
    console.print(
        Panel.fit(
            Text.assemble(
                ("ph-civic-data-mcp v0.5.0  ·  ", "dim"),
                ("reliability + agent-UX pass", "bold cyan"),
                ("  ·  live from PyPI", "dim"),
            ),
            border_style="cyan",
        )
    )

    transport = StdioTransport(command="uvx", args=["ph-civic-data-mcp"], env={**os.environ})
    client = Client(transport)

    async with client:
        with console.status("[cyan]MCP handshake...", spinner="dots"):
            tools = await client.list_tools()
        console.print(f"[green]● connected[/green] · [bold]{len(tools)}[/bold] tools registered\n")

        for idx, (title, tool_name, args, note) in enumerate(SCRIPT, 1):
            console.print(Text.assemble((f"  [{idx}/{len(SCRIPT)}] ", "dim"), (title, "bold")))
            with console.status(
                f"[yellow]→ calling[/yellow] [bold]{tool_name}[/bold]({args})",
                spinner="dots12",
            ):
                t0 = time.perf_counter()
                result = await client.call_tool(tool_name, args)
                elapsed = (time.perf_counter() - t0) * 1000
            data = _unwrap(result)
            if tool_name == "resolve_ph_location":
                data.setdefault("query", args.get("query"))
                console.print(_render_resolve(data, note))
            else:
                console.print(render_risk(data))
            console.print(
                Text.assemble(
                    ("  ● ", "green"),
                    (f"{tool_name}  ", "bold"),
                    (f"{elapsed:.0f} ms", "yellow"),
                    ("  · real MCP protocol · live PyPI", "dim"),
                )
            )
            console.print()
            await asyncio.sleep(0.3)


if __name__ == "__main__":
    asyncio.run(main())
