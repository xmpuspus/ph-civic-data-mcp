"""World Bank Open Data — Philippine macro indicators.

Public JSON API. Covers GDP, poverty, unemployment, urbanization, education,
health outcomes, and thousands more — whatever indicator code is passed.
https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures
"""

from __future__ import annotations

from datetime import datetime, timezone

from ph_civic_data_mcp.models.climate import WorldBankIndicator
from ph_civic_data_mcp.server import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.http import CLIENT, fetch_with_retry, log_stderr

WB_BASE = "https://api.worldbank.org/v2/country/PHL/indicator"

# Curated aliases so agents don't need to memorize WB codes
INDICATOR_ALIASES: dict[str, str] = {
    "gdp": "NY.GDP.MKTP.CD",
    "gdp_current_usd": "NY.GDP.MKTP.CD",
    "gdp_per_capita": "NY.GDP.PCAP.CD",
    "gdp_growth": "NY.GDP.MKTP.KD.ZG",
    "inflation": "FP.CPI.TOTL.ZG",
    "unemployment": "SL.UEM.TOTL.ZS",
    "poverty_ratio": "SI.POV.NAHC",
    "gini": "SI.POV.GINI",
    "population": "SP.POP.TOTL",
    "population_growth": "SP.POP.GROW",
    "urban_population_pct": "SP.URB.TOTL.IN.ZS",
    "life_expectancy": "SP.DYN.LE00.IN",
    "co2_emissions_per_capita": "EN.ATM.CO2E.PC",
    "internet_users_pct": "IT.NET.USER.ZS",
    "electricity_access_pct": "EG.ELC.ACCS.ZS",
    "literacy_rate": "SE.ADT.LITR.ZS",
    "mobile_subscriptions_per_100": "IT.CEL.SETS.P2",
    "tax_revenue_pct_gdp": "GC.TAX.TOTL.GD.ZS",
    "gov_debt_pct_gdp": "GC.DOD.TOTL.GD.ZS",
    "fdi_net_inflows": "BX.KLT.DINV.CD.WD",
    "exports_pct_gdp": "NE.EXP.GNFS.ZS",
    "agriculture_pct_gdp": "NV.AGR.TOTL.ZS",
    "industry_pct_gdp": "NV.IND.TOTL.ZS",
    "services_pct_gdp": "NV.SRV.TOTL.ZS",
    "health_expenditure_pct_gdp": "SH.XPD.CHEX.GD.ZS",
    "school_enrollment_primary": "SE.PRM.ENRR",
    "infant_mortality": "SP.DYN.IMRT.IN",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve(indicator: str) -> str:
    return INDICATOR_ALIASES.get(indicator.lower().strip(), indicator.strip())


@mcp.tool()
async def get_world_bank_indicator(indicator: str, per_page: int = 20) -> dict:
    """World Bank macroeconomic/social indicator for the Philippines.

    Accepts a World Bank indicator code (e.g. 'NY.GDP.MKTP.CD') or a friendly
    alias (e.g. 'gdp', 'poverty_ratio', 'inflation', 'urban_population_pct').

    Args:
        indicator: WB code or alias. See INDICATOR_ALIASES in source for the
                   curated list of common indicators.
        per_page: Number of observations to return (latest first, default 20).
    """
    code = _resolve(indicator)
    per_page = max(1, min(int(per_page), 100))
    ckey = cache_key({"tool": "wb", "indicator": code, "per_page": per_page})
    cache = CACHES["world_bank"]
    if ckey in cache:
        return cache[ckey]

    url = f"{WB_BASE}/{code}"
    params = {"format": "json", "per_page": per_page}

    try:
        response = await fetch_with_retry(CLIENT, "GET", url, params=params)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        log_stderr(f"World Bank error: {exc}")
        return {
            "indicator_id": code,
            "indicator_name": None,
            "country": "Philippines",
            "country_iso3": "PHL",
            "observations": [],
            "source": "World Bank Open Data",
            "data_retrieved_at": _now().isoformat(),
            "caveats": [f"World Bank fetch failed: {type(exc).__name__}"],
        }

    if not isinstance(payload, list) or len(payload) < 2:
        return {
            "indicator_id": code,
            "indicator_name": None,
            "country": "Philippines",
            "country_iso3": "PHL",
            "observations": [],
            "source": "World Bank Open Data",
            "data_retrieved_at": _now().isoformat(),
            "caveats": [f"Unexpected WB response shape for indicator '{code}'"],
        }

    records = payload[1] or []
    indicator_name = None
    observations: list[dict] = []
    for rec in records:
        if indicator_name is None and isinstance(rec.get("indicator"), dict):
            indicator_name = rec["indicator"].get("value")
        value = rec.get("value")
        if value is None:
            continue
        observations.append(
            {
                "year": int(rec["date"]) if str(rec.get("date", "")).isdigit() else rec.get("date"),
                "value": value,
                "unit": rec.get("unit") or "",
            }
        )

    result = WorldBankIndicator(
        indicator_id=code,
        indicator_name=indicator_name or code,
        country="Philippines",
        country_iso3="PHL",
        observations=observations,
        data_retrieved_at=_now(),
    ).model_dump(mode="json")
    cache[ckey] = result
    return result
