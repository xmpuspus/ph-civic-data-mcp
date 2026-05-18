"""Live v0.4.0 demo: Rich CLI driving the ph-civic-data-mcp server over the
real MCP stdio protocol. Every panel shows real JSON returned by the server.

Pre-release note: v0.4.0 is not on PyPI yet, so this points the StdioTransport
at the LOCAL build via `uv run --directory` (real MCP protocol, real live PSA
data — only the server binary is the working tree, not the published wheel).
At release time, swap the transport command to `uvx ph-civic-data-mcp`.

Run:
    uv run python docs/live_demo_v040.py

Record to GIF:
    vhs docs/demo_v040_sources.tape
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from rich.console import Console, Group
from rich.json import JSON
from rich.panel import Panel
from rich.text import Text

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SCRIPT: list[tuple[str, str, dict, str]] = [
    (
        "PSA economy · national headline inflation (CPI 2018=100)",
        "get_inflation_stats",
        {},
        "inflation",
    ),
    (
        "PSA economy · regional inflation (Central Visayas)",
        "get_inflation_stats",
        {"area": "Central Visayas"},
        "inflation",
    ),
    (
        "PSA economy · Labor Force Survey key rates",
        "get_labor_stats",
        {},
        "labor",
    ),
    (
        "PSA · national health indicators",
        "get_health_indicators",
        {},
        "health",
    ),
    (
        "Auto-stitch · one-call area profile (Cebu)",
        "get_area_profile",
        {"location": "Cebu"},
        "profile",
    ),
]


def _unwrap(result):
    data = getattr(result, "data", None)
    if data is not None:
        return data
    content = getattr(result, "content", []) or []
    for c in content:
        text = getattr(c, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None


def render_inflation(data) -> Panel:
    pct = data.get("headline_inflation_pct")
    body = Text.assemble(
        (f"{data.get('area', '?')}\n", "bold cyan"),
        ("  headline inflation  ", "dim"),
        (f"{pct}% YoY" if pct is not None else "n/a", "bold yellow"),
        ("\n  reference period    ", "dim"),
        (str(data.get("reference_period", "?")), "bold"),
        ("\n  base year           ", "dim"),
        (str(data.get("base_year", "?")), "bold"),
        ("\n  source              ", "dim"),
        ("PSA CPI · table discovered by text, latest series picked", "italic"),
    )
    return Panel(body, title="[b]PSA inflation (CPI, All Items)[/b]", border_style="cyan")


def render_labor(data) -> Panel:
    def line(label, key):
        v = data.get(key)
        return Text.assemble(
            (f"  {label:<34}", "dim"),
            (f"{v}%" if v is not None else "n/a", "bold yellow"),
        )

    rows = [
        Text(f"{data.get('area', 'Philippines')}", style="bold cyan"),
        line("labor force participation rate", "labor_force_participation_rate_pct"),
        line("employment rate", "employment_rate_pct"),
        line("unemployment rate", "unemployment_rate_pct"),
        line("underemployment rate", "underemployment_rate_pct"),
        Text.assemble(
            ("  reference period               ", "dim"),
            (str(data.get("reference_period", "?")), "bold"),
        ),
    ]
    return Panel(
        Group(*rows),
        title="[b]PSA Labor Force Survey · key rates[/b]",
        border_style="cyan",
    )


def render_health(data) -> Panel:
    rows = [Text("Philippines · national indicators", style="bold cyan")]
    for ind in data.get("indicators", []):
        rows.append(
            Text.assemble(
                ("  • ", "green"),
                (f"{ind.get('indicator', '?')}", "bold"),
            )
        )
        rows.append(
            Text.assemble(
                ("      ", ""),
                (f"{ind.get('value', 'n/a')}", "bold yellow"),
                (f"  {ind.get('unit') or ''}", "dim"),
                (f"   ({ind.get('reference_period', '?')})", "dim"),
            )
        )
    return Panel(Group(*rows), title="[b]PSA health (subject 1D)[/b]", border_style="cyan")


def render_profile(data) -> Panel:
    r = data.get("resolved", {})
    d = data.get("demographics", {})
    e = data.get("economy", {})
    h = data.get("hazard", {})
    c = data.get("correlations", {})
    pop = d.get("population")
    rows = [
        Text.assemble(
            ("resolved   ", "dim"),
            (f"{r.get('name', '?')} ", "bold"),
            (f"· {r.get('region', '?')} · PSGC {r.get('psgc_code', '?')}", "dim"),
        ),
        Text.assemble(
            ("people     ", "dim"),
            (f"{pop:,}" if isinstance(pop, int) else "n/a", "bold yellow"),
            (f"  · poverty {d.get('poverty_incidence_pct', '?')}%", "dim"),
        ),
        Text.assemble(
            ("economy    ", "dim"),
            (f"inflation {e.get('headline_inflation_pct', '?')}% ", "bold yellow"),
            (f"({e.get('inflation_reference_period', '?')})  ", "dim"),
            (f"unemployment {e.get('unemployment_rate_pct', '?')}%", "bold yellow"),
        ),
        Text.assemble(
            ("hazard     ", "dim"),
            (f"{h.get('earthquake_risk_level', '?')} ", "bold green"),
            (f"({h.get('recent_earthquakes_30d', 0)} quakes/30d)", "dim"),
        ),
        Text.assemble(
            ("correlated ", "dim"),
            (
                f"{c.get('infra_notices_per_100k_population', 'n/a')} infra notices / 100k",
                "bold magenta",
            ),
        ),
    ]
    caveats = data.get("caveats") or []
    if caveats:
        rows.append(Text(""))
        for cav in caveats[:2]:
            rows.append(Text.assemble(("  ⚠ ", "yellow"), (cav, "dim")))
    return Panel(
        Group(*rows),
        title="[b]Auto-stitch · PSGC + PSA + PhilGEPS + PHIVOLCS + PAGASA, one call[/b]",
        border_style="magenta",
    )


RENDERERS = {
    "inflation": render_inflation,
    "labor": render_labor,
    "health": render_health,
    "profile": render_profile,
}


async def main() -> None:
    console = Console()
    console.print()
    console.print(
        Panel.fit(
            Text.assemble(
                ("ph-civic-data-mcp v0.4.0", "bold cyan"),
                ("  ·  PSA economy + auto-stitch  ·  ", "dim"),
                ("real MCP stdio protocol", "bold yellow"),
            ),
            border_style="cyan",
        )
    )
    transport = StdioTransport(
        command="uv",
        args=["run", "--directory", REPO, "ph-civic-data-mcp"],
        env={**os.environ},
    )
    client = Client(transport)
    async with client:
        with console.status("[cyan]connecting via MCP stdio transport...", spinner="dots"):
            tools = await client.list_tools()
        console.print(f"[green]● connected[/green] · [bold]{len(tools)}[/bold] tools registered\n")
        for idx, (title, tool_name, args, key) in enumerate(SCRIPT, 1):
            console.print(Text.assemble((f"  [{idx}/{len(SCRIPT)}] ", "dim"), (title, "bold")))
            with console.status(
                f"[yellow]→ calling[/yellow] [bold]{tool_name}[/bold]({args})",
                spinner="dots12",
            ):
                t0 = time.perf_counter()
                result = await client.call_tool(tool_name, args)
                elapsed_ms = (time.perf_counter() - t0) * 1000
            data = _unwrap(result)
            renderer = RENDERERS.get(key)
            panel = renderer(data) if renderer else Panel(JSON.from_data(data))
            console.print(panel)
            console.print(
                Text.assemble(
                    ("  ● ", "green"),
                    (f"{tool_name}  ", "bold"),
                    (f"{elapsed_ms:.0f} ms  ", "yellow"),
                    ("· real MCP protocol · local v0.4.0 build", "dim"),
                )
            )
            console.print()
            await asyncio.sleep(0.4)


if __name__ == "__main__":
    asyncio.run(main())
