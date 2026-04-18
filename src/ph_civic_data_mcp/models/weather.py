from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class DailyForecast(BaseModel):
    date: date
    temp_min_c: float | None = None
    temp_max_c: float | None = None
    rainfall_mm: float | None = None
    wind_speed_kph: float | None = None
    wind_direction: str | None = None
    weather_description: str | None = None


class WeatherForecast(BaseModel):
    location: str
    forecast_issued: datetime
    days: list[DailyForecast]
    data_source: Literal["pagasa_api", "open_meteo"]
    data_retrieved_at: datetime


class Typhoon(BaseModel):
    local_name: str
    international_name: str | None = None
    category: str
    max_winds_kph: float | None = None
    within_par: bool
    signal_numbers: dict[str, int]
    bulletin_datetime: datetime
