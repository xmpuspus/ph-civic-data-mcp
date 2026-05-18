"""Internal validation models for the PSA OpenSTAT expansion (v0.4.0).

Returned from tools as model.model_dump(mode="json") plus the standard
{source, source_url, license, data_retrieved_at} envelope, per repo convention.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class InflationStats(BaseModel):
    area: str
    headline_inflation_pct: float | None = None
    reference_period: str | None = None
    base_year: str | None = None
    series: Literal["Consumer Price Index, All Items"] = "Consumer Price Index, All Items"
    reference_note: str | None = None
    source: Literal["PSA"] = "PSA"


class LaborStats(BaseModel):
    area: str
    employment_rate_pct: float | None = None
    unemployment_rate_pct: float | None = None
    underemployment_rate_pct: float | None = None
    labor_force_participation_rate_pct: float | None = None
    reference_period: str | None = None
    reference_note: str | None = None
    source: Literal["PSA"] = "PSA"


class HealthIndicator(BaseModel):
    indicator: str
    value: float | None = None
    unit: str | None = None
    area: str = "Philippines"
    reference_period: str | None = None
    source: Literal["PSA"] = "PSA"
