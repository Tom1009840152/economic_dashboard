from datetime import date, datetime

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
    freshness: str = "missing"
    freshness_label: str = "等待数据更新"

    model_config = {"from_attributes": True}


class DataPointOut(BaseModel):
    date: date
    value: float
    release_date: date | None = None
    available_at: datetime | None = None
    retrieved_at: datetime | None = None
    source_url: str | None = None
    status: str | None = None
    version: int = 1

    model_config = {"from_attributes": True}


class IndicatorHistory(BaseModel):
    code: str
    name: str
    unit: str
    points: list[DataPointOut]


class DataPointVintageOut(DataPointOut):
    indicator_code: str


class ForecastPoint(BaseModel):
    date: date
    value: float
    lower: float
    upper: float


class ForecastOut(BaseModel):
    code: str
    name: str
    forecast_unit: str
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
    forecast_unit: str
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


class AnalysisPointOut(BaseModel):
    period: str
    value: float


class AnalysisSeriesOut(BaseModel):
    key: str
    name: str
    unit: str
    points: list[AnalysisPointOut]


class MonetaryTransmissionSignalOut(BaseModel):
    key: str
    name: str
    value: float
    unit: str
    period: str
    state: str
    interpretation: str
    formula: str


class MonetaryTransmissionDashboardOut(BaseModel):
    region: str
    country: str
    title: str
    status: str
    tone: str
    summary: str
    as_of: str
    signals: list[MonetaryTransmissionSignalOut]
    series: list[AnalysisSeriesOut]
    sources: list[EmploymentSourceOut]
    warnings: list[str]
