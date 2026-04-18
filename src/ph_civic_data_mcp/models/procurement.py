from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel


class ProcurementRecord(BaseModel):
    reference_number: str | None = None
    title: str
    agency: str
    region: str | None = None
    mode_of_procurement: str | None = None
    approved_budget: float | None = None
    currency: str = "PHP"
    status: str | None = None
    date_published: date | None = None
    award_date: date | None = None
    source: Literal["PhilGEPS"] = "PhilGEPS"
