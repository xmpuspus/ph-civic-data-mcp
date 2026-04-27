"""v0.3.0 accountability demo: one Rich-rendered pass through the new tools
against a freshly installed PyPI package.

Run:
    uv run python docs/live_demo_v030.py

Record to GIF:
    vhs docs/demo_accountability.tape
"""

from __future__ import annotations

import asyncio
import os
import time

from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _unwrap(result: object) -> object:
    """FastMCP returns CallToolResult; .data carries the parsed JSON."""
    if hasattr(result, "data"):
        return result.data
    if hasattr(result, "structuredContent"):
        return result.structuredContent
    return result


def _render_resolve(payload: dict) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    matched = payload.get("matched", payload.get("psgc_code") is not None)
    if not matched:
        table.add_row("query", str(payload.get("query")))
        table.add_row("matched", "[red]no[/red]")
        for c in payload.get("caveats", [])[:2]:
            table.add_row("caveat", c)
    else:
        table.add_row("psgc_code", str(payload.get("psgc_code")))
        table.add_row("name", str(payload.get("name")))
        table.add_row("level", str(payload.get("level")))
        table.add_row("region_code", str(payload.get("region_code")))
        table.add_row("match_score", f"{payload.get('match_score', '?')}")
        table.add_row("source_url", str(payload.get("source_url"))[:80])
        table.add_row("license", str(payload.get("license"))[:80])
    return Panel(
        table, title="[bold cyan]resolve_ph_location[/bold cyan]", border_style="cyan"
    )


def _render_search(payload: list) -> Panel:
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("agency", overflow="fold")
    table.add_column("title", overflow="fold")
    table.add_column("category", style="cyan", no_wrap=True)
    table.add_column("cost_php", justify="right", style="yellow")
    for project in payload[:5]:
        cost = project.get("cost_php")
        cost_str = f"₱{cost:,.0f}" if cost is not None else "n/a"
        table.add_row(
            (project.get("agency") or "")[:42],
            (project.get("title") or "")[:60],
            project.get("category") or "?",
            cost_str,
        )
    return Panel(
        table,
        title=f"[bold cyan]search_infra_projects[/bold cyan] · {len(payload)} matches",
        border_style="cyan",
    )


def _render_summary(payload: dict) -> Panel:
    cat_lines: list[str] = []
    for category, count in (payload.get("by_category") or {}).items():
        cat_lines.append(f"  {category:<24} {count}")
    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    table.add_row("total_count", str(payload.get("total_count")))
    total = payload.get("total_value_php")
    table.add_row("total_value", "n/a (not in listing)" if total is None else f"₱{total:,.0f}")
    table.add_row(
        "reference_period",
        f"{(payload.get('reference_period') or {}).get('from')} → {(payload.get('reference_period') or {}).get('to')}",
    )
    body = Group(table, Text("\n".join(cat_lines), style="dim"))
    return Panel(
        body,
        title="[bold cyan]summarize_infra_spending[/bold cyan]",
        subtitle="[dim]" + (payload.get("disclaimer") or "")[:80] + "[/dim]",
        border_style="cyan",
    )


def _render_flags(payload: dict) -> Panel:
    examined = payload.get("projects_examined", 0)
    flagged_count = payload.get("flagged_count", 0)
    rules = payload.get("rules_summary") or {}
    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("rule_fired", style="cyan")
    table.add_column("project_id")
    table.add_column("title", overflow="fold")
    for flag in (payload.get("flagged") or [])[:5]:
        table.add_row(
            flag.get("rule_fired") or "?",
            (flag.get("project_id") or "")[:14],
            (flag.get("title") or "")[:62],
        )
    rule_summary = ", ".join(f"{k}={v}" for k, v in rules.items()) or "(no flags)"
    return Panel(
        table,
        title=(
            f"[bold cyan]flag_infra_anomalies[/bold cyan] · "
            f"{flagged_count} flags / {examined} projects"
        ),
        subtitle=f"[dim]{rule_summary} · {payload.get('disclaimer', '')[:60]}[/dim]",
        border_style="cyan",
    )


SCRIPT: list[tuple[str, str, dict, str]] = [
    (
        "PSGC · resolve 'Sta. Mesa, Manila'",
        "resolve_ph_location",
        {"query": "Sta. Mesa, Manila"},
        "resolve",
    ),
    (
        "infra · construction projects in Pampanga",
        "search_infra_projects",
        {"keyword": "construction", "region": "Pampanga", "limit": 5},
        "search",
    ),
    (
        "infra · spending summary",
        "summarize_infra_spending",
        {},
        "summary",
    ),
    (
        "anomaly indicator · cross-source flags",
        "flag_infra_anomalies",
        {"min_cost_php": 50_000_000},
        "flags",
    ),
]

RENDERERS = {
    "resolve": _render_resolve,
    "search": _render_search,
    "summary": _render_summary,
    "flags": _render_flags,
}


async def main() -> None:
    console = Console()
    console.print()
    console.print(
        Panel.fit(
            Text.assemble(
                ("ph-civic-data-mcp v0.3.0  ·  ", "dim"),
                ("PH Accountability layer", "bold cyan"),
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
        console.print(f"[green]● connected[/green] · [bold]{len(tools)}[/bold] tools registered\n")

        for idx, (title, tool_name, args, renderer_key) in enumerate(SCRIPT, 1):
            console.print(
                Text.assemble(
                    (f"  [{idx}/{len(SCRIPT)}] ", "dim"),
                    (title, "bold"),
                )
            )
            with console.status(
                f"[yellow]→ calling[/yellow] [bold]{tool_name}[/bold]({args})",
                spinner="dots12",
            ):
                t0 = time.perf_counter()
                result = await client.call_tool(tool_name, args)
                elapsed = (time.perf_counter() - t0) * 1000
            data = _unwrap(result)
            console.print(RENDERERS[renderer_key](data))
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
