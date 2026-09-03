"""World Bank Open Data — Philippine macro indicators.

Public JSON API. Covers GDP, poverty, unemployment, urbanization, education,
health outcomes, and thousands more — whatever indicator code is passed.
https://datahelpdesk.worldbank.org/knowledgebase/articles/898581-api-basic-call-structures
"""

from __future__ import annotations

import math
import re

from datetime import datetime, timezone

from ph_civic_data_mcp.models.climate import WorldBankIndicator
from ph_civic_data_mcp._mcp import mcp
from ph_civic_data_mcp.utils.cache import CACHES, cache_key
from ph_civic_data_mcp.utils.envelope import failure_result
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


# A World Bank indicator code is letters, digits, dots and hyphens, e.g.
# NY.GDP.MKTP.CD. Anything else is path syntax, and the code goes straight into
# the URL after WB_BASE. "../../../country/USA/indicator/SP.POP.TOTL" returned
# United States figures under this tool's hardcoded "Philippines" label.
_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _resolve(indicator: str) -> str:
    return INDICATOR_ALIASES.get(indicator.lower().strip(), indicator.strip())


def _valid_code(code: str) -> bool:
    return bool(_CODE_RE.match(code)) and ".." not in code


class WorldBankUpstreamError(RuntimeError):
    """A World Bank fetch failed, or sent a payload with nothing usable in it.

    Raised so a transient empty-but-200 response can never enter the 24h TTL
    cache as if it were a real "this indicator has no data" answer.
    """


async def _fetch_observations(code: str, per_page: int) -> tuple[str | None, list[dict], int]:
    """(indicator_name, observations, skipped_count) for one indicator.

    Raises on a bad payload. A real zero answer and a transient empty answer
    look the same on the surface: a 200 with no usable rows. World Bank's own
    `total` count in the response metadata tells them apart. `total` at 0
    means the indicator truly has no published data, which is worth caching.
    `total` above 0 with no usable rows means the rows did not come back this
    time, which must not be cached as a zero. A row whose `value` cannot
    convert to a finite number (a non-numeric string, NaN, or inf) is
    skipped and counted, never published as a figure.
    """
    url = f"{WB_BASE}/{code}"
    params = {"format": "json", "per_page": per_page}
    response = await fetch_with_retry(CLIENT, "GET", url, params=params)
    response.raise_for_status()
    payload = response.json()

    if not isinstance(payload, list) or len(payload) < 2:
        raise WorldBankUpstreamError(f"Unexpected World Bank response shape for indicator {code!r}")

    metadata = payload[0] if isinstance(payload[0], dict) else {}
    records = payload[1]
    if records is None:
        raise WorldBankUpstreamError(
            f"World Bank returned a null data array for indicator {code!r}"
        )
    if not isinstance(records, list):
        raise WorldBankUpstreamError(
            f"World Bank returned a non-list data array for indicator {code!r}"
        )

    indicator_name = None
    observations: list[dict] = []
    skipped = 0
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if indicator_name is None and isinstance(rec.get("indicator"), dict):
            indicator_name = rec["indicator"].get("value")
        value = rec.get("value")
        if value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if not math.isfinite(numeric_value):
            skipped += 1
            continue
        observations.append(
            {
                "year": int(rec["date"]) if str(rec.get("date", "")).isdigit() else rec.get("date"),
                "value": numeric_value,
                "unit": rec.get("unit") or "",
            }
        )

    try:
        total = int(metadata.get("total"))
    except (TypeError, ValueError):
        total = None
    # total == 0 is the one degenerate case that is a real answer: the
    # indicator truly has no data. A missing or unreadable total is not a
    # confirmed zero, so it raises the same as a nonzero total with no rows.
    if not observations and total != 0:
        raise WorldBankUpstreamError(
            f"World Bank returned {len(records)} rows with nothing usable for indicator "
            f"{code!r} (metadata total={metadata.get('total')!r}); not a real zero."
        )
    return indicator_name, observations, skipped


@mcp.tool(
    title="World Bank indicator for the Philippines",
    tags={"economy", "open-data", "philippines", "world-bank"},
    annotations={
        "title": "World Bank indicator for the Philippines",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": True,
        "destructiveHint": False,
    },
)
async def get_world_bank_indicator(indicator: str, per_page: int = 20) -> dict:
    """World Bank macroeconomic and social indicator for the Philippines.

    Accepts a World Bank indicator code, such as "NY.GDP.MKTP.CD", or a
    friendly alias, such as "gdp", "poverty_ratio", "inflation", or
    "urban_population_pct". The tool checks the indicator code shape before
    it reaches the URL, so a bad value cannot redirect the request to
    another country's data. Examples:

      get_world_bank_indicator("gdp")                        # GDP, latest 20 years
      get_world_bank_indicator("NY.GDP.MKTP.CD", per_page=5)  # same indicator, 5 years
      get_world_bank_indicator("poverty_ratio")               # poverty headcount ratio

    On failure: an indicator code or alias that fails the shape check
    returns data_status "invalid_request", with validation_error true and
    observations []. An upstream fetch failure returns data_status
    "unavailable", with upstream_error true, observations [], and the real
    error text in caveats. A row with a non-numeric or non-finite value
    (NaN, inf) is skipped and counted in caveats. A response where every row
    is non-numeric or non-finite returns data_status "unavailable" instead
    of a false empty answer.

    Args:
        indicator: WB code or alias. See INDICATOR_ALIASES in source for the
                   curated list of common indicators.
        per_page: Number of observations to return (latest first, default 20).
    """
    code = _resolve(indicator)
    if not _valid_code(code):
        # Never cached, and never fetched: this is a caller mistake.
        return failure_result(
            "World Bank Open Data",
            WB_BASE,
            f"{indicator!r} is not a World Bank indicator code. Use a code "
            "like 'NY.GDP.MKTP.CD' or an alias like 'gdp'.",
            validation_error=True,
            indicator_id=indicator,
            indicator_name=None,
            country="Philippines",
            country_iso3="PHL",
            observations=[],
        )
    per_page = max(1, min(int(per_page), 100))
    ckey = cache_key({"tool": "wb", "indicator": code, "per_page": per_page})
    cache = CACHES["world_bank"]
    if ckey in cache:
        return cache[ckey]

    url = f"{WB_BASE}/{code}"

    try:
        indicator_name, observations, skipped = await _fetch_observations(code, per_page)
    except WorldBankUpstreamError as exc:
        log_stderr(f"World Bank error: {exc}")
        return failure_result(
            "World Bank Open Data",
            url,
            f"World Bank fetch failed: {exc}",
            indicator_id=code,
            indicator_name=None,
            country="Philippines",
            country_iso3="PHL",
            observations=[],
        )
    except Exception as exc:
        log_stderr(f"World Bank error: {exc}")
        return failure_result(
            "World Bank Open Data",
            url,
            f"World Bank fetch failed: {type(exc).__name__}: {exc}",
            indicator_id=code,
            indicator_name=None,
            country="Philippines",
            country_iso3="PHL",
            observations=[],
        )

    result = WorldBankIndicator(
        indicator_id=code,
        indicator_name=indicator_name or code,
        country="Philippines",
        country_iso3="PHL",
        observations=observations,
        data_retrieved_at=_now(),
    ).model_dump(mode="json")
    if skipped:
        result["caveats"] = [
            f"Skipped {skipped} row(s) with a non-numeric or non-finite value for indicator {code!r}."
        ]
    cache[ckey] = result
    return result
