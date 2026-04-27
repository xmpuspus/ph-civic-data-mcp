from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel


class InfraProject(BaseModel):
    project_id: str
    title: str
    agency: str
    region: str | None = None
    province: str | None = None
    category: str | None = None
    cost_php: float | None = None
    currency: str = "PHP"
    progress_pct: float | None = None
    funding_source: str | None = None
    contractor: str | None = None
    status: str | None = None
    date_published: date | None = None
    award_date: date | None = None
    lat: float | None = None
    lng: float | None = None
    documents: list[str] = []
    source: Literal["PhilGEPS"] = "PhilGEPS"
    source_url: str | None = None
    license: str = "Public — PhilGEPS open notice listing"


class InfraSpendingSummary(BaseModel):
    total_count: int
    total_value_php: float | None
    by_category: dict[str, int]
    by_funding_source: dict[str, int]
    by_region: dict[str, int]
    top_agencies: list[dict]
    reference_period: dict[str, str | None]
    note: str
    source: Literal["PhilGEPS"] = "PhilGEPS"
    source_url: str
    license: str = "Public — PhilGEPS open notice listing"
