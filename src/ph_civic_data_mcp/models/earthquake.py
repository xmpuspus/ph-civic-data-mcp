from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class Earthquake(BaseModel):
    datetime_pst: datetime
    latitude: float
    longitude: float
    depth_km: float
    magnitude: float
    location: str
    intensity: str | None = None
    bulletin_url: str | None = None
    source: Literal["PHIVOLCS"] = "PHIVOLCS"
    data_retrieved_at: datetime
