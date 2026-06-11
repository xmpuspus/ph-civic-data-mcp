"""PSA OpenSTAT (PXWeb) — population and poverty statistics.

Landmines (from validation log):
- 6: Never hardcode table paths. Discover via browse API.
- Table 1A/PO holds population (2020 Census only, no year dimension).
- Table 1E/FY holds Full-Year poverty statistics (2018/2021/2023).
"""

from __future__ import annotations

from datetime import datetime, timezone

from ph_civic_data_mcp.models.population import PopulationStats, PovertyStats
from ph_civic_data_mcp.models.psa import HealthIndicator, InflationStats, LaborStats
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

PSA_API_BASE = "https://openstat.psa.gov.ph/PXWeb/api/v1/en"
PSA_LICENSE = "PSA Open Data terms (Philippine Statistics Authority, OpenSTAT)"


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
        # Not cached: the geo dimension can drift; don't pin a miss for 24h.
        return {
            "region": region or "Philippines",
            "caveats": [f"Region '{region}' not found in PSA geographic dimension"],
            "source": "PSA",
            "data_retrieved_at": _now().isoformat(),
        }
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
        # Not cached: likely a transient PXWeb failure, not a data property.
        return {
            "region": geo_text,
            "upstream_error": True,
            "caveats": ["PSA PXWeb query returned no data"],
            "source": "PSA",
            "data_retrieved_at": _now().isoformat(),
        }

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


# ---------------------------------------------------------------------------
# v0.4.0 expansion: generic browse-discovery helpers + inflation/labor/health.
#
# Every new tool follows the SAME convention as population/poverty above:
# hardcode only the stable subject path prefix (e.g. "2M/PI/CPI/2018NEW"),
# discover the .px leaf by text predicate (landmine #6 — never hardcode .px
# IDs), POST with response format "json" (the proven shape that returns real
# cells: {"data": [{"key": [...], "values": ["str"]}]}), and read the data
# vintage from the table's own Year/time dimension — never from the response
# generation timestamp.
# ---------------------------------------------------------------------------

_MISSING = {"..", "...", "-", "", "n.a.", "na", "*"}


def _to_float(raw: object) -> float | None:
    """PSA encodes missing cells as the literal string '..' (and friends)."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s.lower() in _MISSING:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _year_max(meta: dict) -> int:
    """Largest 4-digit year across the table's time/Year dimension.

    1D health tables carry a 'Year' variable that is NOT time-typed, so match
    on the code/text as well as the time flag.
    """
    best = 0
    for var in meta.get("variables", []):
        code = (var.get("code") or "").lower()
        text = (var.get("text") or "").lower()
        if not (var.get("time") or code == "year" or text == "year"):
            continue
        for vt in var.get("valueTexts", []):
            digits = "".join(ch for ch in str(vt) if ch.isdigit())
            if len(digits) >= 4:
                year = int(digits[:4])
                if 1900 < year < 2100:
                    best = max(best, year)
    return best


async def _browse(subpath: str) -> list[dict]:
    """List entries under a fixed subject path. Cached 24h."""
    key = cache_key({"browse": subpath})
    cache = CACHES["psa_browse"]
    if key in cache:
        return cache[key]
    entries = await _get_json(f"{PSA_API_BASE}/DB/{subpath}/")
    result = entries if isinstance(entries, list) else []
    cache[key] = result
    return result


async def _pick_latest_table(
    subpath: str,
    must_have: list[str],
    must_not: list[str] | None = None,
) -> tuple[str, dict] | None:
    """Discover the .px leaf under `subpath` whose text matches the predicate.

    PSA splits long series into era tables with near-identical titles
    (backcasted 1958-1994 vs current 2019-2026). Among all predicate matches we
    pick the table whose Year dimension reaches the most recent year, so callers
    always get the current series, never a backcast trap.
    """
    must_not = must_not or []
    discovery_key = f"latest::{subpath}::{must_have}::{must_not}"
    if discovery_key in _DISCOVERY_CACHE:
        return _DISCOVERY_CACHE[discovery_key]
    entries = await _browse(subpath)
    best: tuple[int, str, dict] | None = None
    for entry in entries:
        if entry.get("type") != "t":
            continue
        text = (entry.get("text") or "").lower()
        if not all(m in text for m in must_have):
            continue
        if any(n in text for n in must_not):
            continue
        table_url = f"{PSA_API_BASE}/DB/{subpath}/{entry['id']}"
        meta = await _get_json(table_url)
        if not isinstance(meta, dict):
            continue
        ymax = _year_max(meta)
        if best is None or ymax > best[0]:
            best = (ymax, table_url, meta)
    if best is None:
        return None
    found = (best[1], best[2])
    _DISCOVERY_CACHE[discovery_key] = found  # type: ignore[assignment]
    return found


def _key_columns(payload: dict) -> list[str]:
    """Column codes that line up positionally with each data row's `key`.

    The PXWeb "json" format appends the content column (type 'c') to `columns`
    but NOT to `key`; key positions are the dimension columns ('t'/'d') in order.
    """
    return [c.get("code", "") for c in payload.get("columns", []) if c.get("type") in ("t", "d")]


def _rows(payload: dict) -> list[tuple[dict[str, str], float | None]]:
    """Return [( {col_code: value_code}, numeric_or_None ), ...] for each cell."""
    cols = _key_columns(payload)
    out: list[tuple[dict[str, str], float | None]] = []
    for row in payload.get("data", []):
        key = row.get("key", [])
        values = row.get("values", [])
        mapping = {cols[i]: key[i] for i in range(min(len(cols), len(key)))}
        out.append((mapping, _to_float(values[0] if values else None)))
    return out


def _value_text(meta: dict, code: str, value_code: str) -> str:
    for var in meta.get("variables", []):
        if var.get("code") == code:
            for v, t in zip(var.get("values", []), var.get("valueTexts", [])):
                if v == value_code:
                    return t
    return value_code


def _match_value(meta: dict, code: str, *needles: str) -> str | None:
    """First value-code in variable `code` whose text contains all needles."""
    for var in meta.get("variables", []):
        if var.get("code") != code:
            continue
        for v, t in zip(var.get("values", []), var.get("valueTexts", [])):
            low = t.lower()
            if all(n.lower() in low for n in needles):
                return v
    return None


def _var_by_code(meta: dict, *substrs: str) -> dict | None:
    for var in meta.get("variables", []):
        code = (var.get("code") or "").lower()
        if any(s in code for s in substrs):
            return var
    return None


_PERIOD_ORDER = [
    "jan",
    "feb",
    "mar",
    "apr",
    "may",
    "jun",
    "jul",
    "aug",
    "sep",
    "oct",
    "nov",
    "dec",
]


@mcp.tool()
async def get_inflation_stats(area: str | None = None) -> dict:
    """Headline consumer-price inflation (year-on-year, all items) from PSA.

    Source: PSA OpenSTAT Consumer Price Index, 2018-based. The tool discovers
    the current CPI series by text (never a hardcoded table id) and returns the
    most recently published month's year-on-year change. Reports the exact
    reference period — PSA publishes with a lag, so this is the latest available
    figure, not necessarily the current month.

    Args:
        area: Region or "Philippines". None returns the national figure.
              e.g. "NCR", "Region VII", "Davao Region".
    """
    key = cache_key({"tool": "inflation", "area": area})
    cache = CACHES["psa_prices"]
    if key in cache:
        return cache[key]

    def _err(msg: str) -> dict:
        # Error results are never cached — a transient PXWeb failure must not
        # pin a null inflation figure for the 24h success TTL.
        return {
            "area": area or "Philippines",
            "headline_inflation_pct": None,
            "reference_period": None,
            "upstream_error": True,
            "caveats": [msg],
            "source": "PSA",
            "source_url": f"{PSA_API_BASE}/DB/2M/PI/CPI/",
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    discovered = await _pick_latest_table(
        "2M/PI/CPI/2018NEW",
        ["year-on-year changes", "by commodity group"],
        ["core"],
    )
    if discovered is None:
        return _err("PSA CPI table discovery failed")
    table_url, meta = discovered

    geo_hit = _find_geo_value(meta, area, "Geolocation")
    if geo_hit is None:
        return _err(f"Area '{area}' not found in PSA CPI geographic dimension")
    geo_val, geo_text = geo_hit

    all_items = _match_value(meta, "Commodity Description", "all item")
    if all_items is None:
        return _err("Could not locate the 'ALL ITEMS' commodity row in PSA CPI table")

    year_var = _var_by_code(meta, "year") or {}
    year_codes = year_var.get("values", [])
    period_var = _var_by_code(meta, "period") or {}
    period_codes = period_var.get("values", [])
    if not year_codes or not period_codes:
        return _err("PSA CPI table is missing Year/Period dimensions")
    base_year = None
    title = meta.get("title", "")
    if "2018=100" in title.replace(" ", ""):
        base_year = "2018"

    # Walk newest year backwards until we find a published month.
    for year_code in reversed(year_codes):
        query = {
            "query": [
                {"code": "Geolocation", "selection": {"filter": "item", "values": [geo_val]}},
                {
                    "code": "Commodity Description",
                    "selection": {"filter": "item", "values": [all_items]},
                },
                {"code": "Year", "selection": {"filter": "item", "values": [year_code]}},
                {"code": "Period", "selection": {"filter": "item", "values": period_codes}},
            ],
            "response": {"format": "json"},
        }
        payload = await _post_json(table_url, query)
        if not payload or not payload.get("data"):
            continue
        period_code_var = next(
            (c for c in _key_columns(payload) if c.lower() == "period"), "Period"
        )
        by_period: dict[str, float] = {}
        for mapping, val in _rows(payload):
            if val is None:
                continue
            pcode = mapping.get(period_code_var)
            if pcode is not None:
                by_period[_value_text(meta, "Period", pcode).strip().lower()] = val
        if not by_period:
            continue
        # Latest published month in calendar order; fall back to annual average.
        chosen_label = None
        chosen_val = None
        for month in reversed(_PERIOD_ORDER):
            if month in by_period:
                chosen_label = month.capitalize()
                chosen_val = by_period[month]
                break
        if chosen_val is None:
            for avg_key in ("ave", "average", "annual"):
                if avg_key in by_period:
                    chosen_label = "full-year average"
                    chosen_val = by_period[avg_key]
                    break
        if chosen_val is None:
            continue
        year_text = _value_text(meta, "Year", year_code)
        stats = InflationStats(
            area=geo_text,
            headline_inflation_pct=chosen_val,
            reference_period=f"{year_text} {chosen_label}",
            base_year=base_year,
            reference_note=(
                "Year-on-year change of the Consumer Price Index, All Items. "
                "PSA publishes monthly with a lag; this is the latest available "
                "reference period, not necessarily the current month."
            ),
        )
        result = {
            **stats.model_dump(mode="json"),
            "source_table": table_url,
            "source": "PSA",
            "source_url": table_url,
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }
        cache[key] = result
        return result

    return _err("PSA CPI query returned no published data")


@mcp.tool()
async def get_labor_stats(region: str | None = None) -> dict:
    """Key labor-force indicators from the PSA Labor Force Survey.

    Returns labor-force participation, employment, unemployment, and
    underemployment rates for the latest published reference period. The PSA
    key-indicator series is national; a `region` argument is recorded as a
    caveat because this table has no regional breakdown.

    Args:
        region: Accepted for API symmetry. The LFS key-indicator table is
                national only; passing a region adds an explanatory caveat.
    """
    key = cache_key({"tool": "labor", "region": region})
    cache = CACHES["psa_labor"]
    if key in cache:
        return cache[key]
    caveats: list[str] = []
    if region:
        caveats.append(
            "PSA LFS key-indicator table is national; regional breakdown is not "
            "available in this series."
        )

    def _err(msg: str) -> dict:
        # Error results are never cached (see get_inflation_stats._err).
        return {
            "area": "Philippines",
            "employment_rate_pct": None,
            "unemployment_rate_pct": None,
            "underemployment_rate_pct": None,
            "labor_force_participation_rate_pct": None,
            "reference_period": None,
            "upstream_error": True,
            "caveats": [*caveats, msg],
            "source": "PSA",
            "source_url": f"{PSA_API_BASE}/DB/1B/LFS/",
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    discovered = await _pick_latest_table("1B/LFS", ["rates", "key employment indicators"], [])
    if discovered is None:
        return _err("PSA Labor Force Survey table discovery failed")
    table_url, meta = discovered

    sex_both = _match_value(meta, "Sex", "both")
    rates_var = _var_by_code(meta, "rate") or {}
    year_var = _var_by_code(meta, "year") or {}
    month_var = _var_by_code(meta, "month") or {}
    if not (sex_both and rates_var and year_var and month_var):
        return _err("PSA LFS table is missing expected Sex/Rates/Year/Month dimensions")

    rate_codes = rates_var.get("values", [])
    month_codes = month_var.get("values", [])

    for year_code in reversed(year_var.get("values", [])):
        query = {
            "query": [
                {"code": "Year", "selection": {"filter": "item", "values": [year_code]}},
                {"code": "Month", "selection": {"filter": "item", "values": month_codes}},
                {"code": "Rates", "selection": {"filter": "item", "values": rate_codes}},
                {"code": "Sex", "selection": {"filter": "item", "values": [sex_both]}},
            ],
            "response": {"format": "json"},
        }
        payload = await _post_json(table_url, query)
        if not payload or not payload.get("data"):
            continue
        cols = _key_columns(payload)
        month_col = next((c for c in cols if c.lower() == "month"), "Month")
        rate_col = next((c for c in cols if c.lower() == "rates"), "Rates")
        # month_code -> {rate_code: value}
        grid: dict[str, dict[str, float]] = {}
        for mapping, val in _rows(payload):
            if val is None:
                continue
            mc = mapping.get(month_col)
            rc = mapping.get(rate_col)
            if mc is None or rc is None:
                continue
            grid.setdefault(mc, {})[rc] = val
        if not grid:
            continue

        def _month_rank(mc: str) -> int:
            label = _value_text(meta, "Month", mc).strip().lower()
            if label in ("annual", "average", "ave"):
                return 99
            for i, m in enumerate(
                [
                    "january",
                    "february",
                    "march",
                    "april",
                    "may",
                    "june",
                    "july",
                    "august",
                    "september",
                    "october",
                    "november",
                    "december",
                ]
            ):
                if label.startswith(m):
                    return i
            return -1

        best_month = max(grid.keys(), key=_month_rank)
        rates = grid[best_month]

        def _rate(*needles: str) -> float | None:
            code = _match_value(meta, "Rates", *needles)
            return rates.get(code) if code else None

        stats = LaborStats(
            area="Philippines",
            labor_force_participation_rate_pct=_rate("labor force participation"),
            employment_rate_pct=_rate("employment rate"),
            unemployment_rate_pct=_rate("unemployment rate"),
            underemployment_rate_pct=_rate("underemployment rate"),
            reference_period=(
                f"{_value_text(meta, 'Year', year_code)} {_value_text(meta, 'Month', best_month)}"
            ),
            reference_note=(
                "PSA Labor Force Survey key employment indicators, national. "
                "Latest available reference period."
            ),
        )
        result = {
            **stats.model_dump(mode="json"),
            "caveats": caveats,
            "source_table": table_url,
            "source": "PSA",
            "source_url": table_url,
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }
        cache[key] = result
        return result

    return _err("PSA LFS query returned no published data")


def _unit_from_title(title: str) -> str | None:
    """Pull a parenthetical unit out of a health table title.

    >>> _unit_from_title("Maternal mortality ratio (per 100,000 live births)")
    'per 100,000 live births'
    """
    start = title.find("(")
    end = title.find(")", start + 1)
    if start != -1 and end != -1:
        return title[start + 1 : end].strip()
    return None


async def _latest_health_value(table_url: str, meta: dict) -> tuple[float | None, str | None]:
    """Latest numeric cell + its reference year for a (small) 1D health table.

    1D tables are tiny (≤ a few hundred cells). We select the single newest year
    and the first geolocation value (these tables are national), keeping the
    query well under the cell cap.
    """
    year_var = _var_by_code(meta, "year") or next(
        (v for v in meta.get("variables", []) if (v.get("text") or "").lower() == "year"),
        {},
    )
    year_codes = year_var.get("values", [])
    if not year_codes:
        return None, None
    query_dims: list[dict] = []
    for var in meta.get("variables", []):
        code = var.get("code", "")
        values = var.get("values", [])
        if not values:
            continue
        if var is year_var or code == year_var.get("code"):
            continue
        # Geolocation / background characteristics: take the first (aggregate) value.
        query_dims.append({"code": code, "selection": {"filter": "item", "values": [values[0]]}})
    for year_code in reversed(year_codes):
        query = {
            "query": [
                *query_dims,
                {
                    "code": year_var.get("code", "Year"),
                    "selection": {"filter": "item", "values": [year_code]},
                },
            ],
            "response": {"format": "json"},
        }
        payload = await _post_json(table_url, query)
        if not payload or not payload.get("data"):
            continue
        for _, val in _rows(payload):
            if val is not None:
                return val, _value_text(meta, year_var.get("code", "Year"), year_code)
    return None, None


@mcp.tool()
async def get_health_indicators(indicator: str | None = None) -> dict:
    """National health indicators from PSA OpenSTAT (subject 1D).

    With no argument, returns the curated national headline set (maternal
    mortality ratio and total fertility rate). Pass a free-text `indicator` to
    fuzzy-match any table published under the Health subject — the available
    list is browse-discovered, never hardcoded.

    Args:
        indicator: Optional free-text indicator name, e.g. "maternal mortality",
                   "fertility". None returns the default headline set.
    """
    key = cache_key({"tool": "health", "indicator": indicator})
    cache = CACHES["psa_health"]
    if key in cache:
        return cache[key]

    entries = await _browse("1D")
    tables = [e for e in entries if e.get("type") == "t"]
    available = [e.get("text", "") for e in tables]
    if not tables:
        # Not cached: discovery failure is usually a transient PXWeb error.
        return {
            "indicators": [],
            "upstream_error": True,
            "caveats": ["PSA Health (1D) table discovery failed"],
            "source": "PSA",
            "source_url": f"{PSA_API_BASE}/DB/1D/",
            "license": PSA_LICENSE,
            "data_retrieved_at": _now().isoformat(),
        }

    if indicator:
        want = indicator.lower().strip()
        chosen = [
            e
            for e in tables
            if want in (e.get("text") or "").lower()
            or any(tok in (e.get("text") or "").lower() for tok in want.split())
        ]
        caveat = None
        if not chosen:
            caveat = f"No PSA Health table matched '{indicator}'. Available indicators: {available}"
    else:
        chosen = [
            e
            for e in tables
            if "maternal mortality" in (e.get("text") or "").lower()
            or "fertility rate" in (e.get("text") or "").lower()
        ]
        caveat = None

    indicators: list[dict] = []
    for entry in chosen:
        table_url = f"{PSA_API_BASE}/DB/1D/{entry['id']}"
        meta = await _get_json(table_url)
        if not isinstance(meta, dict):
            continue
        value, year_text = await _latest_health_value(table_url, meta)
        title = meta.get("title", entry.get("text", ""))
        model = HealthIndicator(
            indicator=title,
            value=value,
            unit=_unit_from_title(title),
            area="Philippines",
            reference_period=year_text,
        )
        indicators.append({**model.model_dump(mode="json"), "source_table": table_url})

    result = {
        "indicators": indicators,
        "available_indicators": available,
        "caveats": [caveat] if caveat else [],
        "source": "PSA",
        "source_url": f"{PSA_API_BASE}/DB/1D/",
        "license": PSA_LICENSE,
        "data_retrieved_at": _now().isoformat(),
    }
    cache[key] = result
    return result
