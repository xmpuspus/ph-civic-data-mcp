"""Live-probe every new v0.2.0 tool and dump truncated JSON for the README."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from ph_civic_data_mcp.server import _register_tools

_register_tools()

from ph_civic_data_mcp.sources.ibtracs import get_historical_typhoons_ph
from ph_civic_data_mcp.sources.modis_ndvi import get_vegetation_index
from ph_civic_data_mcp.sources.nasa_power import get_solar_and_climate
from ph_civic_data_mcp.sources.open_meteo_aq import get_air_quality
from ph_civic_data_mcp.sources.usgs import get_usgs_earthquakes_ph
from ph_civic_data_mcp.sources.world_bank import get_world_bank_indicator


def trunc(obj: object, max_items: int = 5) -> object:
    """Trim large lists for README display."""
    if isinstance(obj, list):
        if len(obj) > max_items:
            return [trunc(x, max_items) for x in obj[:max_items]] + [f"... ({len(obj) - max_items} more)"]
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
    outputs = {}
    tasks = [
        run_one(
            "get_solar_and_climate (Manila, 7 days)",
            get_solar_and_climate(14.5995, 120.9842, start_date="2026-04-01", end_date="2026-04-07"),
        ),
        run_one("get_air_quality (Manila)", get_air_quality("Manila")),
        run_one(
            "get_vegetation_index (Nueva Ecija farmland)",
            get_vegetation_index(15.58, 121.0, start_date="2026-01-01", end_date="2026-04-18"),
        ),
        run_one(
            "get_usgs_earthquakes_ph (last 30d, m>=5.0)",
            get_usgs_earthquakes_ph(min_magnitude=5.0, limit=10),
        ),
        run_one("get_historical_typhoons_ph (last 3 years)", get_historical_typhoons_ph(limit=6)),
        run_one("get_world_bank_indicator (gdp)", get_world_bank_indicator("gdp", per_page=10)),
    ]
    results = await asyncio.gather(*tasks)
    for label, result in results:
        outputs[label] = trunc(result, max_items=3)

    out_path = Path("/tmp/live_probe_output.json")
    out_path.write_text(json.dumps(outputs, indent=2, default=str))
    print(f"wrote {out_path}")

    # Also print a one-line pass/fail per tool
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
                keys = list(result.keys())[:6]
                print(f"OK    {label}: keys={keys}")


if __name__ == "__main__":
    asyncio.run(main())
