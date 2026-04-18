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
    source: Literal["PSA"] = "PSA"


class PovertyStats(BaseModel):
    region: str
    poverty_incidence_pct: float
    subsistence_incidence_pct: float | None = None
    reference_year: int
    source: Literal["PSA"] = "PSA"
