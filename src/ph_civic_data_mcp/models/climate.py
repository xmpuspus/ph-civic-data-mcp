"""Pydantic models for v0.2.0 sources (NASA POWER, Open-Meteo AQ, MODIS, USGS, IBTrACS, World Bank)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class SolarClimateDay(BaseModel):
    date: date
    solar_irradiance_kwh_m2: float | None = None
    temp_c: float | None = None
    precipitation_mm: float | None = None
    windspeed_ms: float | None = None


class SolarClimate(BaseModel):
    latitude: float
    longitude: float
    start_date: date
    end_date: date
    days: list[SolarClimateDay]
    source: str = "NASA POWER"
    data_retrieved_at: datetime


class AirQuality(BaseModel):
    location: str
    latitude: float
    longitude: float
    measured_at: datetime
    pm2_5: float | None = None
    pm10: float | None = None
    carbon_monoxide: float | None = None
    nitrogen_dioxide: float | None = None
    sulphur_dioxide: float | None = None
    ozone: float | None = None
    european_aqi: int | None = None
    us_aqi: int | None = None
    aqi_category: str | None = None
    source: str = "Open-Meteo Air Quality"
    data_retrieved_at: datetime


class VegetationSample(BaseModel):
    composite_date: str
    ndvi: float | None = None
    evi: float | None = None


class VegetationIndex(BaseModel):
    latitude: float
    longitude: float
    product: str
    band: str
    samples: list[VegetationSample]
    source: str = "NASA MODIS via ORNL DAAC"
    data_retrieved_at: datetime


class USGSEarthquake(BaseModel):
    datetime_utc: datetime
    magnitude: float
    magnitude_type: str | None = None
    depth_km: float | None = None
    latitude: float
    longitude: float
    place: str
    usgs_event_id: str
    felt_reports: int | None = None
    tsunami: bool = False
    url: str
    source: str = "USGS FDSN"
    data_retrieved_at: datetime


class HistoricalTyphoon(BaseModel):
    sid: str
    name: str
    season: int
    basin: str
    max_wind_kt: float | None = None
    min_pressure_mb: float | None = None
    start_time_utc: datetime | None = None
    end_time_utc: datetime | None = None
    track_points: int
    passed_within_par: bool
    source: str = "NOAA IBTrACS"
    data_retrieved_at: datetime


class WorldBankIndicator(BaseModel):
    indicator_id: str
    indicator_name: str
    country: str
    country_iso3: str
    observations: list[dict]
    source: str = "World Bank Open Data"
    data_retrieved_at: datetime
