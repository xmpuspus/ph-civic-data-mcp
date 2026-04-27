"""Live end-to-end demo: Rich CLI driving `uvx ph-civic-data-mcp` from PyPI.

Every panel below shows real JSON returned by the MCP protocol from a freshly
installed PyPI package. No scripted data.

Run:
    uv run python docs/live_demo.py

Record to GIF:
    vhs docs/demo.tape
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from rich import box
from rich.console import Console, Group
from rich.json import JSON
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# Script: (title, tool_name, args, renderer-key)
SCRIPT: list[tuple[str, str, dict, str]] = [
    (
        "PHIVOLCS · latest earthquakes",
        "get_latest_earthquakes",
        {"min_magnitude": 2.0, "limit": 3},
        "earthquakes",
    ),
    (
        "PHIVOLCS · Mayon volcano",
        "get_volcano_status",
        {"volcano_name": "Mayon"},
        "volcano",
    ),
    (
        "PAGASA · 3-day forecast (Cebu)",
        "get_weather_forecast",
        {"location": "Cebu City", "days": 3},
        "weather",
    ),
    (
        "PAGASA · active typhoons",
        "get_active_typhoons",
        {},
        "typhoons",
    ),
    (
        "PhilGEPS · procurement summary",
        "get_procurement_summary",
        {},
        "procurement",
    ),
    (
        "PSA · NCR population (2020 Census)",
        "get_population_stats",
        {"region": "NCR"},
        "population",
    ),
    (
        "PSA · national poverty (2023)",
        "get_poverty_stats",
        {},
        "poverty",
    ),
    (
        "Cross-source · multi-hazard risk for Manila",
        "assess_area_risk",
        {"location": "Manila"},
        "risk",
    ),
]
# NB: get_air_quality tool was removed from the server (PH AQICN stations went dark).


def _unwrap(result):
    """FastMCP CallToolResult → plain Python object."""
    data = getattr(result, "data", None)
    if data is not None:
        return data
    # Fallback: first text content, parsed as JSON
    content = getattr(result, "content", []) or []
    for c in content:
        text = getattr(c, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None


def render_earthquakes(data) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan", expand=True)
    t.add_column("Mag", justify="right", style="yellow", width=6)
    t.add_column("Datetime (PST)", width=20)
    t.add_column("Location")
    t.add_column("Depth", justify="right", width=7)
    for q in data:
        t.add_row(
            f"M{q['magnitude']}",
            str(q["datetime_pst"])[:19],
            q["location"],
            f"{int(q['depth_km'])} km",
        )
    return Panel(t, title="[b]PHIVOLCS earthquakes[/b]", border_style="cyan")


def render_volcano(data) -> Panel:
    if not data:
        return Panel("[dim]No monitored volcano returned[/dim]", title="[b]Volcano status[/b]")
    rows = []
    for v in data:
        level = v.get("alert_level")
        lvl_color = {
            0: "green",
            1: "green",
            2: "yellow",
            3: "red",
            4: "red",
            5: "red",
            None: "dim",
        }.get(level, "white")
        rows.append(
            Text.assemble(
                (f"{v['name']:12s} ", "bold"),
                ("alert level ", "dim"),
                (f"{level}", f"bold {lvl_color}"),
                ("   "),
                (v.get("status_description") or "", "italic"),
            )
        )
    return Panel(Group(*rows), title="[b]WOVODAT volcano bulletin[/b]", border_style="red")


def render_weather(data) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan", expand=True)
    t.add_column("Date", width=12)
    t.add_column("Temp (°C)", justify="center", width=16)
    t.add_column("Rain", justify="right", width=8)
    t.add_column("Conditions")
    for d in data.get("days", []):
        t.add_row(
            d["date"],
            f"[yellow]{d.get('temp_min_c', '?')} – {d.get('temp_max_c', '?')}[/yellow]",
            f"[green]{d.get('rainfall_mm', '?')} mm[/green]",
            d.get("weather_description") or "",
        )
    source = data.get("data_source", "?")
    return Panel(
        t,
        title=f"[b]PAGASA · {data.get('location', '?')}[/b]  [dim](via {source})[/dim]",
        border_style="cyan",
    )


def render_typhoons(data) -> Panel:
    if not data:
        return Panel(
            "[green]● No Active Tropical Cyclone within the Philippine Area of Responsibility[/green]",
            title="[b]PAGASA typhoon bulletin[/b]",
            border_style="green",
        )
    rows = [
        Text.assemble(
            ("● ", "bold red"),
            (f"{t['local_name']}  ", "bold"),
            (f"[{t.get('category', '?')}]  ", "yellow"),
            (f"{t.get('max_winds_kph', '?')} kph", "dim"),
        )
        for t in data
    ]
    return Panel(Group(*rows), title="[b]Active typhoons[/b]", border_style="red")


def render_procurement(data) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan", expand=True)
    t.add_column("Mode of procurement")
    t.add_column("Count", justify="right", style="yellow")
    for mode, count in data.get("by_mode", {}).items():
        t.add_row(mode, str(count))

    agencies = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan", expand=True)
    agencies.add_column("Top agencies (by notice count)")
    agencies.add_column("#", justify="right", style="yellow", width=5)
    for a in (data.get("top_agencies") or [])[:5]:
        agencies.add_row(a["agency"][:60], str(a["count"]))

    return Panel(
        Group(t, Text(""), agencies),
        title=f"[b]PhilGEPS · {data.get('total_count', '?')} latest notices[/b]",
        border_style="cyan",
    )


def render_population(data) -> Panel:
    pop = data.get("population")
    fmt = f"{pop:,}" if isinstance(pop, int) else "n/a"
    body = Text.assemble(
        (f"{data.get('region', '?')}\n", "bold cyan"),
        ("  population  ", "dim"),
        (fmt, "bold yellow"),
        ("\n  year        ", "dim"),
        (str(data.get("year", "?")), "bold"),
        ("\n  source      ", "dim"),
        ("PSA OpenSTAT · PXWeb (discovered via browse API)", "italic"),
    )
    return Panel(body, title="[b]PSA population[/b]", border_style="cyan")


def render_poverty(data) -> Panel:
    pct = data.get("poverty_incidence_pct")
    body = Text.assemble(
        (f"{data.get('region', '?')}\n", "bold cyan"),
        ("  poverty incidence  ", "dim"),
        (f"{pct}%" if pct is not None else "n/a", "bold yellow"),
        ("\n  reference year     ", "dim"),
        (str(data.get("reference_year", "?")), "bold"),
    )
    return Panel(body, title="[b]PSA poverty (Full-Year)[/b]", border_style="cyan")


def render_risk(data) -> Panel:
    rows = [
        Text.assemble(
            ("location       ", "dim"),
            (f"{data.get('location', '?')}", "bold"),
        ),
        Text.assemble(
            ("earthquake     ", "dim"),
            (f"{data.get('earthquake_risk_level', '?')}", "bold green"),
            (
                f"   ({data.get('recent_earthquakes_30d', 0)} quakes in last 30 d · max "
                f"M{data.get('max_magnitude_30d', 0)})",
                "dim",
            ),
        ),
        Text.assemble(
            ("typhoon        ", "dim"),
            (
                "Active signal" if data.get("typhoon_signal_active") else "No active signal",
                "bold green",
            ),
        ),
        Text.assemble(
            ("active alerts  ", "dim"),
            (str(len(data.get("active_alerts") or [])), "bold yellow"),
            ("  PAGASA advisories", "dim"),
        ),
    ]
    caveats = data.get("caveats") or []
    if caveats:
        rows.append(Text(""))
        for c in caveats:
            rows.append(Text.assemble(("  ⚠ ", "yellow"), (c, "dim")))
    return Panel(
        Group(*rows),
        title="[b]Multi-hazard risk · parallel PHIVOLCS + PAGASA[/b]",
        border_style="magenta",
    )


RENDERERS = {
    "earthquakes": render_earthquakes,
    "volcano": render_volcano,
    "weather": render_weather,
    "typhoons": render_typhoons,
    "procurement": render_procurement,
    "population": render_population,
    "poverty": render_poverty,
    "risk": render_risk,
}


async def main() -> None:
    console = Console()

    console.print()
    console.print(
        Panel.fit(
            Text.assemble(
                ("ph-civic-data-mcp", "bold cyan"),
                ("  ·  live from PyPI  ·  ", "dim"),
                ("spawning via ", "dim"),
                ("`uvx ph-civic-data-mcp`", "bold yellow"),
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
        with console.status("[cyan]connecting via MCP stdio transport...", spinner="dots"):
            tools = await client.list_tools()
        console.print(f"[green]● connected[/green] · [bold]{len(tools)}[/bold] tools registered\n")

        for idx, (title, tool_name, args, key) in enumerate(SCRIPT, 1):
            banner = Text.assemble(
                (f"  [{idx}/{len(SCRIPT)}] ", "dim"),
                (title, "bold"),
            )
            console.print(banner)

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
                    ("  ", ""),
                    ("● ", "green"),
                    (f"{tool_name}  ", "bold"),
                    (f"{elapsed_ms:.0f} ms  ", "yellow"),
                    ("· real MCP protocol · live PyPI", "dim"),
                )
            )
            console.print()
            await asyncio.sleep(0.4)

    console.print(
        Panel.fit(
            Text.assemble(
                ("done · ", "dim"),
                (f"{len(SCRIPT)} tool calls", "bold green"),
                (" · all responses above are real MCP protocol output", "dim"),
            ),
            border_style="green",
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
