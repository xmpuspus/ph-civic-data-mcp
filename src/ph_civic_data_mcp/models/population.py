from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PopulationStats(BaseModel):
    region: str
    year: int
    population: int
    growth_rate_pct: float | None = None
    density_per_sqkm: float | None = None
    reference_note: str | None = None
    # Added in 0.6.1 (additive). `region` keeps the matched label for older
    # callers; `geography` carries the same label, and `geography_level` says
    # what kind of place it is.
    geography: str | None = None
    geography_level: str | None = None
    psgc_code: str | None = None
    census: str | None = None
    reference_date: str | None = None
    source: Literal["PSA"] = "PSA"


class PovertyStats(BaseModel):
    region: str
    poverty_incidence_pct: float
    subsistence_incidence_pct: float | None = None
    reference_year: int
    source: Literal["PSA"] = "PSA"
