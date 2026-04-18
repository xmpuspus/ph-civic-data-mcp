from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AirQuality(BaseModel):
    city: str
    station_name: str | None = None
    aqi: int
    aqi_category: str
    pm25: float | None = None
    pm10: float | None = None
    no2: float | None = None
    so2: float | None = None
    o3: float | None = None
    co: float | None = None
    dominant_pollutant: str | None = None
    health_advisory: str | None = None
    measured_at: datetime
    source: str = "AQICN"
    data_retrieved_at: datetime
