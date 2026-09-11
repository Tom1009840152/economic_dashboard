from datetime import date

from pydantic import BaseModel


class IndicatorSummary(BaseModel):
    code: str
    name: str
    category: str
    region: str
    unit: str
    latest_date: date | None = None
    latest_value: float | None = None
    change_pct: float | None = None
    recent_values: list[float] = []

    model_config = {"from_attributes": True}


class DataPointOut(BaseModel):
    date: date
    value: float

    model_config = {"from_attributes": True}


class IndicatorHistory(BaseModel):
    code: str
    name: str
    unit: str
    points: list[DataPointOut]


class ForecastPoint(BaseModel):
    date: date
    value: float
    lower: float
    upper: float


class ForecastOut(BaseModel):
    code: str
    name: str
    history: list[DataPointOut]
    forecast: list[ForecastPoint]


class CurrencyOption(BaseModel):
    code: str
    name: str


class ForexHistoryOut(BaseModel):
    base: str
    target: str
    points: list[DataPointOut]


class ForexForecastOut(BaseModel):
    base: str
    target: str
    history: list[DataPointOut]
    forecast: list[ForecastPoint]


class PopulationPointOut(BaseModel):
    year: int
    value: float


class PopulationSeriesOut(BaseModel):
    key: str
    name: str
    unit: str
    points: list[PopulationPointOut]


class PopulationPyramidOut(BaseModel):
    age_group: str
    male: float
    female: float
    total: float


class PopulationDashboardOut(BaseModel):
    region: str
    country: str
    source: str
    source_url: str
    last_updated: str | None = None
    series: list[PopulationSeriesOut]
    pyramid_year: int | None = None
    pyramid: list[PopulationPyramidOut]


class EmploymentPointOut(BaseModel):
    period: str
    value: float


class EmploymentSeriesOut(BaseModel):
    key: str
    name: str
    unit: str
    frequency: str
    source_type: str
    scope: str
    points: list[EmploymentPointOut]


class EmploymentSnapshotOut(BaseModel):
    year: int
    employment_total: float
    urban_employment: float
    rural_employment: float
    migrant_workers: float
    annual_urban_unemployment: float
    weekly_hours: float
    source: str
    source_url: str


class EmploymentSourceOut(BaseModel):
    name: str
    url: str
    description: str


class EmploymentDashboardOut(BaseModel):
    region: str
    country: str
    latest_month: str | None = None
    wdi_updated: str | None = None
    monthly_series: list[EmploymentSeriesOut]
    annual_series: list[EmploymentSeriesOut]
    official_snapshot: EmploymentSnapshotOut
    sources: list[EmploymentSourceOut]
    warnings: list[str]


class InternationalEmploymentDashboardOut(BaseModel):
    region: str
    country: str
    latest_month: str | None = None
    series: list[EmploymentSeriesOut]
    sources: list[EmploymentSourceOut]
    warnings: list[str]
