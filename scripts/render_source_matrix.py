"""Print the README source table from SOURCE_CATALOG.

Run this after any change to SOURCE_CATALOG and paste the output between the
source-matrix markers in README.md. It never hand-types a row, so the table
cannot drift from the server's own source list.

    ./.venv/bin/python3 scripts/render_source_matrix.py
"""

from __future__ import annotations

from ph_civic_data_mcp.server import SOURCE_CATALOG

# One short line per source. Keep in step with SOURCE_CATALOG: a new source
# with no entry here raises, rather than printing a blank cell.
WHAT_IT_GIVES: dict[str, str] = {
    "PSGC": "Place codes and names, region down to barangay",
    "PHIVOLCS earthquakes": "Earthquake events and full bulletins",
    "PHIVOLCS volcanoes": "Alert level and bulletin per monitored volcano",
    "PAGASA forecast": "10-day weather forecast, with an Open-Meteo fallback",
    "PAGASA typhoons": "Active typhoon bulletins and weather alerts",
    "PhilGEPS notices / infra": "Procurement notices, the infra subset, spending summaries",
    "PSA OpenSTAT": "Population, poverty, CPI, labor, health, and the full statistical catalog",
    "Area profile (auto-stitch)": "One place profile composed live from every source below",
    "NASA POWER": "Daily solar irradiance and climate at any point",
    "Open-Meteo air quality": "PM2.5, PM10, NO2, SO2, O3, CO, and AQI",
    "NASA MODIS NDVI": "NDVI and EVI vegetation indices at any point",
    "USGS FDSN": "Philippine-region earthquakes, cross-checked against PHIVOLCS",
    "NOAA IBTrACS": "Historical tropical cyclone tracks through the Philippine AOR",
    "World Bank Open Data": "Philippine macroeconomic indicators",
    "HDX": "Humanitarian dataset search, with a per-dataset license",
}


def _no_emdash(text: str) -> str:
    """Swap an em dash for a comma. SOURCE_CATALOG carries a few; the README
    style bans the dash everywhere, including inside a generated table."""
    return text.replace(" — ", ", ")


def _format_ttl(seconds: int) -> str:
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"{hours} h"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"{minutes} min"
    return f"{seconds} s"


def render() -> str:
    lines = [
        "| Source | What it gives | Freshness | Cache TTL | License |",
        "|---|---|---|---|---|",
    ]
    for entry in SOURCE_CATALOG:
        name = entry["source"]
        if name not in WHAT_IT_GIVES:
            raise ValueError(f"add a WHAT_IT_GIVES entry for '{name}' before rendering")
        row = (
            f"| {name} | {WHAT_IT_GIVES[name]} | {_no_emdash(entry['freshness'])} | "
            f"{_format_ttl(entry['cache_ttl_seconds'])} | {_no_emdash(entry['license'])} |"
        )
        lines.append(row)
    return "\n".join(lines)


if __name__ == "__main__":
    print(render())
