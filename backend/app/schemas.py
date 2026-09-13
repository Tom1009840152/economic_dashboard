from datetime import date, datetime
from typing import Literal

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


class IndicatorCatalogOut(BaseModel):
    code: str
    name: str
    category: str
    region: str
    unit: str
    is_visible: bool
    sort_order: int
    source: str
    frequency: str
    seasonal_adjustment: str
    measure_type: str
    aggregation: str
    cumulative: bool
    direction: str
    transform: str
    release_lag_months: int
    valid_min: float | None = None
    valid_max: float | None = None
    notes: str
    formula: str | None = None
    formula_version: str | None = None
    input_codes: list[str] | None = None


class DataPointOut(BaseModel):
    date: date
    value: float
    release_date: date | None = None
    available_at: datetime | None = None
    retrieved_at: datetime | None = None
    source_url: str | None = None
    status: str | None = None
    formula_version: str | None = None
    version: int = 1

    model_config = {"from_attributes": True}


class IndicatorHistory(BaseModel):
    code: str
    name: str
    unit: str
    points: list[DataPointOut]


class DataPointVintageOut(DataPointOut):
    indicator_code: str


class AsOfDataPointOut(DataPointOut):
    indicator_code: str
    indicator_name: str
    unit: str
    effective_available_at: datetime
    availability_basis: Literal["available_at", "retrieved_at_fallback"]


class AsOfHistoryOut(BaseModel):
    code: str
    name: str
    unit: str
    as_of: datetime
    mode: Literal["strict", "fallback"]
    availability_policy: Literal["published_only", "published_or_first_seen"]
    availability_note: str
    total: int
    offset: int
    limit: int
    points: list[AsOfDataPointOut]


class AsOfSnapshotOut(BaseModel):
    as_of: datetime
    observation_end: date
    mode: Literal["strict", "fallback"]
    availability_policy: Literal["published_only", "published_or_first_seen"]
    availability_note: str
    requested_count: int
    returned_count: int
    unknown_codes: list[str]
    unavailable_codes: list[str]
    missing_codes: list[str]
    points: list[AsOfDataPointOut]


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
    maintenance: str | None = None
    verified_through: str | None = None
    source: str | None = None
    source_url: str | None = None


class MonetaryTransmissionSignalOut(BaseModel):
    key: str
    name: str
    value: float
    unit: str
    period: str
    state: str
    interpretation: str
    formula: str
    formula_version: str | None = None
    data_origin: str | None = None


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


class CycleMethodologyOut(BaseModel):
    standardization: str
    rolling_window_months: int
    monthly_min_history: int
    quarterly_min_history: int
    zscore_clip: float
    quarterly_forward_fill_months: int
    block_min_coverage: float
    minimum_active_blocks: int
    overall_min_coverage: float
    neutral_level: float
    index_scale: float
    missing_value_policy: str
    weighting: str
    input_coverage_definition: str
    overall_coverage_definition: str
    realtime_coverage_definition: str


class CycleSignalDefinitionOut(BaseModel):
    code: str
    name: str
    input_codes: list[str]
    weight: float
    operation: Literal["level", "mean", "difference", "trailing_mean_3"]
    direction: Literal["positive", "negative"]
    frequency: str
    calibration_start: str | None = None
    sources: list[str]


class CycleBlockDefinitionOut(BaseModel):
    key: str
    name: str
    role: Literal["coincident", "leading"]
    weight: float
    minimum_coverage: float
    indicator_codes: list[str]
    signals: list[CycleSignalDefinitionOut]


class CycleValidationIndicatorOut(BaseModel):
    code: str
    name: str
    unit: str
    source: str
    frequency: str
    purpose: Literal["external_validation_only"]


class CycleSignalPointOut(BaseModel):
    code: str
    name: str
    input_codes: list[str]
    weight: float
    raw_value: float | None = None
    source_period: str | None = None
    standardized_score: float | None = None
    realtime_ready: bool
    contribution: float | None = None


class CycleBlockPointOut(BaseModel):
    key: str
    name: str
    role: Literal["coincident", "leading"]
    score: float | None = None
    coverage: float
    realtime_coverage: float
    available_count: int
    total_count: int
    minimum_coverage: float
    contribution: float | None = None
    signals: list[CycleSignalPointOut]


class CycleValidationPointOut(BaseModel):
    code: str
    name: str
    raw_value: float | None = None
    source_period: str | None = None
    standardized_score: float | None = None


class CycleMonthOut(BaseModel):
    period: str
    composite_index: float | None = None
    coincident_index: float | None = None
    leading_index: float | None = None
    coincident_coverage: float
    leading_coverage: float
    overall_coverage: float
    input_coverage: float
    realtime_coverage: float
    active_blocks: int
    confidence: Literal["high", "medium", "low", "insufficient"]
    comparable_to_previous: bool
    composition_changed: bool
    comparison_reasons: list[str]
    blocks: list[CycleBlockPointOut]
    validation: list[CycleValidationPointOut]


class CycleContributionOut(BaseModel):
    code: str
    name: str
    role: Literal["coincident", "leading"]
    block_key: str
    source_period: str | None = None
    standardized_score: float
    contribution: float


class CycleInputLatestOut(BaseModel):
    code: str
    name: str
    input_codes: list[str]
    frequency: str
    latest_observation: str | None = None
    latest_value: float | None = None
    lag_months: int | None = None
    is_stale: bool
    used_in_latest: bool
    used_observation: str | None = None
    required_formula_version: str | None = None
    latest_formula_version: str | None = None
    latest_status: str | None = None
    latest_stored_observation: str | None = None
    latest_stored_formula_version: str | None = None
    latest_stored_status: str | None = None
    excluded_version_observations: int = 0


class CycleLatestOut(BaseModel):
    period: str
    composite_index: float
    coincident_index: float
    leading_index: float
    overall_coverage: float
    input_coverage: float
    realtime_coverage: float
    confidence: Literal["high", "medium", "low"]
    positive_contributions: list[CycleContributionOut]
    negative_contributions: list[CycleContributionOut]
    input_latest_periods: list[CycleInputLatestOut]


class ChinaBusinessCycleMatrixOut(BaseModel):
    region: Literal["CN"]
    country: str
    title: str
    mode: Literal["current"]
    data_basis: Literal["final"]
    as_of: str | None = None
    realtime_coverage: float | None = None
    method: str
    methodology_version: str
    methodology_note: str
    methodology: CycleMethodologyOut
    blocks: list[CycleBlockDefinitionOut]
    validation_indicators: list[CycleValidationIndicatorOut]
    months: list[CycleMonthOut]
    latest: CycleLatestOut | None = None
    warnings: list[str]
