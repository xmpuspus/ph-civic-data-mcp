"""PSA OpenSTAT (PXWeb) — population and poverty statistics.

Landmines (from validation log):
- 6: Never hardcode table paths. Discover via browse API.
- Table 1A/PO holds population (2020 Census only, no year dimension).
- Table 1E/FY holds Full-Year poverty statistics (2018/2021/2023).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ph_civic_data_mcp.models.population import PopulationStats, PovertyStats
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

PSA_API_BASE = "https://openstat.psa.gov.ph/PXWeb/api/v1/en"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _get_json(url: str) -> dict | list | None:
    try:
        response = await fetch_with_retry(CLIENT, "GET", url)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        log_stderr(f"PSA fetch error for {url}: {exc}")
        return None


async def _post_json(url: str, query: dict) -> dict | None:
    try:
        response = await fetch_with_retry(CLIENT, "POST", url, json=query)
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        log_stderr(f"PSA POST error for {url}: {exc}")
        return None


_DISCOVERY_CACHE: dict[str, tuple[str, dict]] = {}


async def _discover_population_table() -> tuple[str, dict] | None:
    """Return (table_url, metadata) for the total-population table."""
    if "population" in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE["population"]
    tables = await _get_json(f"{PSA_API_BASE}/DB/1A/PO/")
    if not isinstance(tables, list):
        return None
    for entry in tables:
        text = entry.get("text", "").lower()
        if "total population" in text and ("region" in text or "household" in text):
            table_id = entry.get("id")
            table_url = f"{PSA_API_BASE}/DB/1A/PO/{table_id}"
            meta = await _get_json(table_url)
            if isinstance(meta, dict):
                _DISCOVERY_CACHE["population"] = (table_url, meta)
                return table_url, meta
    return None


async def _discover_fy_poverty_entries() -> list[dict]:
    """Single browse of 1E/FY cached and reused for poverty + subsistence discovery."""
    if "fy_entries" in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE["fy_entries"][1]  # type: ignore[return-value]
    tables = await _get_json(f"{PSA_API_BASE}/DB/1E/FY/")
    if not isinstance(tables, list):
        return []
    _DISCOVERY_CACHE["fy_entries"] = ("1E/FY", tables)  # type: ignore[assignment]
    return tables


async def _discover_poverty_table() -> tuple[str, dict] | None:
    if "poverty" in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE["poverty"]
    tables = await _discover_fy_poverty_entries()
    for entry in tables:
        text = entry.get("text", "").lower()
        if text.startswith("table 1.") and "poverty incidence" in text and "families" in text:
            table_id = entry.get("id")
            table_url = f"{PSA_API_BASE}/DB/1E/FY/{table_id}"
            meta = await _get_json(table_url)
            if isinstance(meta, dict):
                _DISCOVERY_CACHE["poverty"] = (table_url, meta)
                return table_url, meta
    return None


async def _discover_subsistence_table() -> tuple[str, dict] | None:
    if "subsistence" in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE["subsistence"]
    tables = await _discover_fy_poverty_entries()
    for entry in tables:
        text = entry.get("text", "").lower()
        if text.startswith("table 3.") and "subsistence incidence" in text and "families" in text:
            table_id = entry.get("id")
            table_url = f"{PSA_API_BASE}/DB/1E/FY/{table_id}"
            meta = await _get_json(table_url)
            if isinstance(meta, dict):
                _DISCOVERY_CACHE["subsistence"] = (table_url, meta)
                return table_url, meta
    return None


def _find_geo_value(meta: dict, region: str | None, geo_code: str) -> tuple[str, str] | None:
    """Return (value_code, value_text) matching the requested region in the geo variable.

    geo_code is either "Geographic Location" or "Geolocation" depending on table.
    """
    for var in meta.get("variables", []):
        code = var.get("code") or var.get("text", "")
        if code.lower() != geo_code.lower() and geo_code.lower() not in code.lower():
            continue
        values = var.get("values", [])
        texts = var.get("valueTexts", [])
        if region is None:
            for val, txt in zip(values, texts):
                if "philippines" in txt.lower():
                    return val, txt
            if values:
                return values[0], texts[0]
        region_norm = region.strip().lower()
        for val, txt in zip(values, texts):
            t_norm = txt.lower().strip(" .")
            if region_norm == t_norm or region_norm in t_norm:
                return val, txt.strip(" .")
        # try matching against region codes (I, II, III, NCR, CAR, BARMM)
        aliases = {"ncr": "national capital", "car": "cordillera", "barmm": "bangsamoro"}
        target = aliases.get(region_norm, region_norm)
        for val, txt in zip(values, texts):
            if target in txt.lower():
                return val, txt.strip(" .")
    return None


def _variable_values(meta: dict, code_match: str) -> tuple[str, list[str], list[str]]:
    """Return (code_exact, values, texts) for first variable whose code contains code_match."""
    for var in meta.get("variables", []):
        code = var.get("code", "") or var.get("text", "")
        if code_match.lower() in code.lower():
            return code, var.get("values", []), var.get("valueTexts", [])
    return "", [], []


@mcp.tool()
async def get_population_stats(
    region: str | None = None,
    year: int | None = None,
) -> dict:
    """Philippine population from PSA OpenSTAT (2020 Census).

    Args:
        region: e.g. "NCR", "Region VII", "Cordillera Administrative Region".
                None returns national total.
        year: Ignored — latest data is 2020 Census; field kept for API stability.
    """
    key = cache_key({"tool": "population", "region": region, "year": year})
    cache = CACHES["psa_population"]
    if key in cache:
        return cache[key]

    discovered = await _discover_population_table()
    if discovered is None:
        return {
            "region": region or "Philippines",
            "caveats": ["PSA PXWeb population table discovery failed"],
            "source": "PSA",
            "data_retrieved_at": _now().isoformat(),
        }
    table_url, meta = discovered

    geo_hit = _find_geo_value(meta, region, "Geographic Location")
    if geo_hit is None:
        result = {
            "region": region or "Philippines",
            "caveats": [f"Region '{region}' not found in PSA geographic dimension"],
            "source": "PSA",
            "data_retrieved_at": _now().isoformat(),
        }
        cache[key] = result
        return result
    geo_val, geo_text = geo_hit

    param_code, param_values, _ = _variable_values(meta, "Parameter")
    param_val = param_values[0] if param_values else "0"

    query = {
        "query": [
            {"code": "Geographic Location", "selection": {"filter": "item", "values": [geo_val]}},
            {
                "code": param_code or "Parameter",
                "selection": {"filter": "item", "values": [param_val]},
            },
        ],
        "response": {"format": "json"},
    }
    payload = await _post_json(table_url, query)
    if payload is None or not payload.get("data"):
        result = {
            "region": geo_text,
            "caveats": ["PSA PXWeb query returned no data"],
            "source": "PSA",
            "data_retrieved_at": _now().isoformat(),
        }
        cache[key] = result
        return result

    try:
        population = int(payload["data"][0]["values"][0])
    except (KeyError, IndexError, ValueError):
        population = 0

    stats = PopulationStats(
        region=geo_text,
        year=2020,
        population=population,
        reference_note=(
            "PSA 2020 Census of Population and Housing. Latest available PH census data."
        ),
    )
    result = {
        **stats.model_dump(mode="json"),
        "source_table": table_url,
        "data_retrieved_at": _now().isoformat(),
    }
    cache[key] = result
    return result


@mcp.tool()
async def get_poverty_stats(region: str | None = None) -> dict:
    """Poverty incidence from PSA (latest: 2023 Full-Year).

    Args:
        region: PH region (None returns national).
    """
    key = cache_key({"tool": "poverty", "region": region})
    cache = CACHES["psa_poverty"]
    if key in cache:
        return cache[key]

    poverty = await _discover_poverty_table()
    subsistence = await _discover_subsistence_table()
    if poverty is None:
        return {
            "region": region or "Philippines",
            "caveats": ["PSA PXWeb poverty table discovery failed"],
            "source": "PSA",
            "data_retrieved_at": _now().isoformat(),
        }

    table_url, meta = poverty
    geo_hit = _find_geo_value(meta, region, "Geolocation")
    if geo_hit is None:
        return {
            "region": region or "Philippines",
            "caveats": [f"Region '{region}' not found in PSA poverty table"],
            "source": "PSA",
            "data_retrieved_at": _now().isoformat(),
        }
    geo_val, geo_text = geo_hit

    measure_code, measure_values, measure_texts = _variable_values(meta, "Incidence")
    incidence_val = measure_values[0]
    for val, txt in zip(measure_values, measure_texts):
        if "poverty incidence" in txt.lower() and "famil" in txt.lower():
            incidence_val = val
            break

    year_code, year_values, year_texts = _variable_values(meta, "Year")
    year_val = year_values[-1] if year_values else "0"
    year_text = year_texts[-1] if year_texts else "latest"
    try:
        year_int = int(year_text)
    except ValueError:
        year_int = 2023

    query = {
        "query": [
            {"code": "Geolocation", "selection": {"filter": "item", "values": [geo_val]}},
            {
                "code": measure_code or "Threshold/Incidence/Measures of Precision",
                "selection": {"filter": "item", "values": [incidence_val]},
            },
            {
                "code": year_code or "Year",
                "selection": {"filter": "item", "values": [year_val]},
            },
        ],
        "response": {"format": "json"},
    }
    payload = await _post_json(table_url, query)
    poverty_pct: float | None = None
    if payload and payload.get("data"):
        try:
            poverty_pct = float(payload["data"][0]["values"][0])
        except (KeyError, IndexError, ValueError):
            poverty_pct = None

    if poverty_pct is None:
        return {
            "region": geo_text,
            "caveats": ["PSA PXWeb poverty query returned no usable data"],
            "source": "PSA",
            "data_retrieved_at": _now().isoformat(),
        }

    subsistence_pct: float | None = None
    if subsistence is not None:
        sub_url, sub_meta = subsistence
        sub_geo = _find_geo_value(sub_meta, region, "Geolocation")
        sub_measure_code, sub_mv, sub_mt = _variable_values(sub_meta, "Incidence")
        if sub_geo and sub_mv:
            sub_incidence_val = sub_mv[0]
            for v, t in zip(sub_mv, sub_mt):
                if "subsistence" in t.lower() and "famil" in t.lower():
                    sub_incidence_val = v
                    break
            sub_year_code, sub_yv, _ = _variable_values(sub_meta, "Year")
            sub_year_val = sub_yv[-1] if sub_yv else "0"
            sub_query = {
                "query": [
                    {
                        "code": "Geolocation",
                        "selection": {"filter": "item", "values": [sub_geo[0]]},
                    },
                    {
                        "code": sub_measure_code,
                        "selection": {"filter": "item", "values": [sub_incidence_val]},
                    },
                    {
                        "code": sub_year_code or "Year",
                        "selection": {"filter": "item", "values": [sub_year_val]},
                    },
                ],
                "response": {"format": "json"},
            }
            sub_payload = await _post_json(sub_url, sub_query)
            if sub_payload and sub_payload.get("data"):
                try:
                    subsistence_pct = float(sub_payload["data"][0]["values"][0])
                except (KeyError, IndexError, ValueError):
                    subsistence_pct = None

    stats = PovertyStats(
        region=geo_text,
        poverty_incidence_pct=poverty_pct,
        subsistence_incidence_pct=subsistence_pct,
        reference_year=year_int,
    )
    result = {
        **stats.model_dump(mode="json"),
        "source_table": table_url,
        "data_retrieved_at": _now().isoformat(),
    }
    cache[key] = result
    return result
