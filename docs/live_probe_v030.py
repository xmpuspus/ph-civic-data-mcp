"""Live-probe every new v0.3.0 tool and dump truncated JSON for the README."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from ph_civic_data_mcp.server import _register_tools, get_data_freshness

_register_tools()

from ph_civic_data_mcp.sources.cross_source import flag_infra_anomalies
from ph_civic_data_mcp.sources.infra import (
    get_infra_project,
    search_infra_projects,
    summarize_infra_spending,
)
from ph_civic_data_mcp.sources.psgc import (
    get_location_hierarchy,
    list_admin_units,
    resolve_ph_location,
)


def trunc(obj: object, max_items: int = 5) -> object:
    if isinstance(obj, list):
        if len(obj) > max_items:
            return [trunc(x, max_items) for x in obj[:max_items]] + [
                f"... ({len(obj) - max_items} more)"
            ]
        return [trunc(x, max_items) for x in obj]
    if isinstance(obj, dict):
        return {k: trunc(v, max_items) for k, v in obj.items()}
    return obj


async def run_one(label: str, coro: object) -> tuple[str, object]:
    try:
        result = await coro
    except Exception as exc:
        return label, {"error": f"{type(exc).__name__}: {exc}"}
    return label, result


async def main() -> None:
    """Sequential calls so upstream caches warm cleanly between dependent tools."""
    outputs: dict[str, object] = {}

    # Resolve a real query first so the hierarchy probe uses a fresh code.
    resolved = await resolve_ph_location("Cebu City")
    cebu_code = resolved.get("psgc_code") or "072217000"

    sequential_tasks = [
        ("resolve_ph_location ('Sta. Mesa, Manila')", resolve_ph_location("Sta. Mesa, Manila")),
        ("resolve_ph_location ('Pampanga')", resolve_ph_location("Pampanga")),
        ("list_admin_units (top-level regions)", list_admin_units(limit=4)),
        (f"get_location_hierarchy ({cebu_code})", get_location_hierarchy(cebu_code)),
        # Order matters: search warms the philgeps cache before summary/flags reuse it.
        (
            "search_infra_projects (keyword='construction', limit=3)",
            search_infra_projects(keyword="construction", limit=3),
        ),
        ("summarize_infra_spending ()", summarize_infra_spending()),
        (
            "flag_infra_anomalies (min_cost_php=50_000_000)",
            flag_infra_anomalies(min_cost_php=50_000_000),
        ),
        ("get_data_freshness ()", get_data_freshness()),
    ]
    for label, coro in sequential_tasks:
        label, result = await run_one(label, coro)
        outputs[label] = trunc(result, max_items=3)

    sample_search = await search_infra_projects(keyword="construction", limit=1)
    sample_id = sample_search[0]["project_id"] if sample_search else "PHILGEPS-INF-SAMPLE"
    fetched = await get_infra_project(sample_id)
    outputs[f"get_infra_project ('{sample_id}')"] = trunc(fetched, max_items=3)

    out_path = Path("/tmp/live_probe_v030_output.json")
    out_path.write_text(json.dumps(outputs, indent=2, default=str))
    print(f"wrote {out_path}")

    for label, result in outputs.items():
        if isinstance(result, dict) and result.get("error"):
            print(f"FAIL  {label}: {result['error']}")
        elif isinstance(result, list) and not result:
            print(f"EMPTY {label}: []")
        elif isinstance(result, dict) and result.get("caveats"):
            print(f"WARN  {label}: caveats={result['caveats']}")
        else:
            if isinstance(result, list):
                print(f"OK    {label}: {len(result)} item(s)")
            else:
                keys = list(result.keys())[:8]
                print(f"OK    {label}: keys={keys}")


if __name__ == "__main__":
    asyncio.run(main())
