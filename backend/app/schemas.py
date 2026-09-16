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
    signal_observation_lag_policy: str
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
    observation_lag_months: int = 0
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


RelativeCyclePhase = Literal["recovery", "expansion", "slowdown", "contraction"]
RegimeStatus = Literal[
    "confirmed",
    "candidate",
    "transition",
    "held_uncomparable",
    "stale",
    "insufficient",
]
PhaseBasis = Literal[
    "active_decision",
    "carried_forward",
    "pending_confirmation",
    "unclassified",
]


class RegimeMethodologyOut(BaseModel):
    classification_scope: Literal["relative_growth_cycle_not_recession_call"]
    level_source: str
    level_smoothing_months: int
    momentum_definition: str
    momentum_comparison_months: int
    level_neutral: float
    level_buffer: float
    momentum_buffer: float
    confirmation_months_with_leading: int
    confirmation_months_without_leading: int
    phase_order: list[RelativeCyclePhase]
    role_comparability: str
    balanced_panel_months: int
    basis_change_policy: str
    coincident_min_coverage: float
    coincident_high_coverage: float
    absolute_anchor_min_valid: int
    absolute_breadth_expansionary: float
    absolute_breadth_contractionary: float
    inflation_direction_buffer_pp: float
    driver_decomposition: str
    decomposition_tolerance: float


class RegimeDriverOut(BaseModel):
    code: str
    name: str
    block_key: str
    source_period: str | None = None
    contribution: float


class RegimeDriverContributionOut(BaseModel):
    code: str
    name: str
    block_key: str
    effective_weight: float
    recent_score: float
    comparison_score: float
    level_contribution: float
    momentum_contribution: float
    recent_source_periods: list[str]
    comparison_source_periods: list[str]


class RegimeDriverDecompositionOut(BaseModel):
    role: Literal["coincident", "leading"]
    status: Literal["available", "unavailable"]
    reason: Literal["insufficient_history", "insufficient_common_basis"] | None = None
    basis_signature: str | None = None
    basis_codes: list[str]
    basis_coverage: float
    recent_window_start: str | None = None
    recent_window_end: str | None = None
    comparison_window_start: str | None = None
    comparison_window_end: str | None = None
    level_gap: float | None = None
    momentum_3m: float | None = None
    level_contribution_sum: float | None = None
    momentum_contribution_sum: float | None = None
    level_residual: float | None = None
    momentum_residual: float | None = None
    additivity_passed: bool
    drivers: list[RegimeDriverContributionOut]


RegimeStateChangeReason = Literal[
    "first_decision",
    "level_axis_changed",
    "momentum_axis_changed",
    "raw_phase_changed",
    "candidate_started",
    "candidate_progressed",
    "candidate_reset",
    "candidate_cleared",
    "phase_confirmed",
    "phase_maintained",
    "phase_carried_forward",
    "basis_changed_hold",
    "insufficient_hold",
    "dead_zone_unclassified",
    "level_dead_zone_inherited",
    "momentum_dead_zone_inherited",
    "leading_shortened_confirmation",
]


class RegimeStateChangeOut(BaseModel):
    previous_decision_period: str | None = None
    previous_level_axis: Literal["above", "below", "neutral", "unavailable"] | None = None
    previous_momentum_axis: Literal["rising", "falling", "neutral", "unavailable"] | None = None
    previous_raw_phase: RelativeCyclePhase | None = None
    previous_confirmed_phase: RelativeCyclePhase | None = None
    reason_codes: list[RegimeStateChangeReason]


class AbsoluteAnchorPointOut(BaseModel):
    code: str
    name: str
    three_month_average: float | None = None
    threshold: float
    gap: float | None = None
    state: Literal["above", "below", "unavailable"]


class AbsoluteAnchorOut(BaseModel):
    state: Literal["expansionary", "mixed", "contractionary", "unavailable"]
    breadth: float | None = None
    gap: float | None = None
    valid_count: int
    total_count: int
    conflict: bool
    anchors: list[AbsoluteAnchorPointOut]


class RegimeInflationOut(BaseModel):
    period: str
    value: float | None = None
    level: Literal["very_low", "mild", "elevated", "unavailable"]
    three_month_average: float | None = None
    momentum: float | None = None
    state: Literal[
        "deflation_pressure",
        "low_inflation",
        "moderate",
        "heating",
        "unavailable",
    ]
    direction: Literal["reflation", "disinflation", "stable", "unavailable"]
    ppi_value: float | None = None
    ppi_three_month_average: float | None = None
    rationale: str


class RegimeMonthOut(BaseModel):
    period: str
    phase: RelativeCyclePhase | None = None
    phase_label: str
    raw_phase: RelativeCyclePhase | None = None
    phase_status: RegimeStatus
    phase_basis: PhaseBasis
    confirmed: bool
    confirmed_phase: RelativeCyclePhase | None = None
    candidate_phase: RelativeCyclePhase | None = None
    candidate_since: str | None = None
    confirmed_since: str | None = None
    candidate_streak: int
    required_confirmation_months: int | None = None
    duration_months: int | None = None
    undecidable_streak: int
    carry_forward_months: int
    last_decision_period: str | None = None
    decision_eligible: bool
    decision_reasons: list[str]
    coincident_comparable: bool
    leading_comparable: bool
    coincident_coverage: float | None = None
    leading_coverage: float | None = None
    coincident_basis: list[str]
    leading_basis: list[str]
    coincident_basis_signature: str | None = None
    leading_basis_signature: str | None = None
    coincident_basis_coverage: float | None = None
    leading_basis_coverage: float | None = None
    coincident_basis_changed: bool
    leading_basis_changed: bool
    coincident_decomposition: RegimeDriverDecompositionOut
    leading_decomposition: RegimeDriverDecompositionOut
    coincident_index: float | None = None
    leading_index: float | None = None
    level_3m: float | None = None
    level_gap: float | None = None
    momentum_3m: float | None = None
    diagnostic_level_3m: float | None = None
    diagnostic_momentum_3m: float | None = None
    level_axis: Literal["above", "below", "neutral", "unavailable"]
    momentum_axis: Literal["rising", "falling", "neutral", "unavailable"]
    leading_level_3m: float | None = None
    leading_gap: float | None = None
    leading_momentum_3m: float | None = None
    leading_diagnostic_level_3m: float | None = None
    leading_diagnostic_momentum_3m: float | None = None
    leading_direction: Literal["up", "down", "neutral", "unavailable"]
    leading_confirmation: Literal["confirmed", "divergent", "neutral", "unavailable"]
    confidence: Literal["high", "medium", "low", "insufficient"]
    confidence_reasons: list[str]
    data_basis: Literal["final"]
    realtime_coverage: float
    absolute_anchor: AbsoluteAnchorOut
    inflation: RegimeInflationOut
    summary: str
    outlook: str
    triggers: list[str]
    state_change: RegimeStateChangeOut
    positive_contributions: list[RegimeDriverOut]
    negative_contributions: list[RegimeDriverOut]


class RegimeTrajectoryPointOut(BaseModel):
    period: str
    level_gap: float
    momentum_3m: float | None = None
    diagnostic_momentum_3m: float | None = None
    phase: RelativeCyclePhase | None = None
    phase_status: RegimeStatus
    comparable: bool


class RegimeTimelineOut(BaseModel):
    phase: RelativeCyclePhase
    phase_label: str
    start_period: str
    end_period: str
    duration_months: int
    ongoing: bool


class ChinaCycleRegimeOut(BaseModel):
    region: Literal["CN"]
    country: str
    title: str
    mode: Literal["current"]
    data_basis: Literal["final"]
    as_of: str | None = None
    last_decision_period: str | None = None
    realtime_coverage: float | None = None
    a1_methodology_version: str | None = None
    methodology_version: str
    methodology_note: str
    methodology: RegimeMethodologyOut
    change_conditions: list[str]
    latest: RegimeMonthOut | None = None
    months: list[RegimeMonthOut]
    trajectory: list[RegimeTrajectoryPointOut]
    timeline: list[RegimeTimelineOut]
    a1_warnings: list[str]
    warnings: list[str]


class CycleBacktestDefinitionOut(BaseModel):
    timezone: Literal["Asia/Shanghai"]
    decision_schedule: Literal["observation_month_plus_1_day_20_18_00"]
    strict: Literal[True]
    stored_available_at_timezone: str
    observation_alignment: str
    availability_policy: str
    vintage_selection: str
    final_reference: str
    final_cutoff_at: datetime
    a1_methodology_version: str
    a2_methodology_version: str
    a3_methodology_version: str


class CycleBacktestCoverageOut(BaseModel):
    scheduled_months: int
    display_evaluable_months: int
    display_evaluable_rate: float | None = None
    decision_evaluable_months: int
    decision_evaluable_rate: float | None = None
    unavailable_months: int
    reason_counts: dict[str, int]


class CycleBacktestConfusionOut(BaseModel):
    realtime_phase: RelativeCyclePhase
    final_phase: RelativeCyclePhase
    count: int


class CycleBacktestStabilityOut(BaseModel):
    comparable_months: int
    agreement_count: int | None = None
    agreement_rate: float | None = None
    flip_count: int | None = None
    flip_rate: float | None = None
    decision_comparable_months: int
    decision_agreement_count: int | None = None
    decision_agreement_rate: float | None = None
    carried_forward_comparable_months: int
    carried_forward_agreement_count: int | None = None
    carried_forward_agreement_rate: float | None = None
    carried_forward_flip_count: int | None = None
    carried_forward_flip_rate: float | None = None
    mixed_basis_comparable_months: int
    pending_confirmation_comparable_months: int
    level_axis_comparable_months: int
    level_axis_agreement_rate: float | None = None
    momentum_axis_comparable_months: int
    momentum_axis_agreement_rate: float | None = None
    confusion: list[CycleBacktestConfusionOut]
    minimum_rate_sample: int
    sample_note: str | None = None


class CycleBacktestTransitionEventOut(BaseModel):
    final_phase: RelativeCyclePhase
    final_confirmation_period: str
    realtime_confirmation_period: str | None = None
    signed_lag_months: int | None = None
    match_status: Literal["matched", "unmatched"]


class CycleBacktestTransitionsOut(BaseModel):
    matched_count: int
    lag_median_months: float | None = None
    lag_q1_months: float | None = None
    lag_q3_months: float | None = None
    unmatched_realtime: int
    unmatched_final: int
    minimum_lag_sample: int
    events: list[CycleBacktestTransitionEventOut]


class CycleBacktestPhaseDistributionSampleOut(BaseModel):
    sample_months: int
    realtime: dict[RelativeCyclePhase, int]
    final: dict[RelativeCyclePhase, int]


class CycleBacktestPhaseDistributionOut(BaseModel):
    display: CycleBacktestPhaseDistributionSampleOut
    decision: CycleBacktestPhaseDistributionSampleOut


class CycleBacktestCountGateOut(BaseModel):
    observed: int
    minimum: int
    passed: bool


class CycleBacktestPhaseGateOut(BaseModel):
    required: list[RelativeCyclePhase]
    realtime_observed: list[RelativeCyclePhase]
    final_observed: list[RelativeCyclePhase]
    passed: bool


class CycleBacktestSensitivityGatesOut(BaseModel):
    formal_decision_sample: CycleBacktestCountGateOut
    four_phase_coverage: CycleBacktestPhaseGateOut
    matched_transitions: CycleBacktestCountGateOut
    all_passed: bool
    failed_gates: list[
        Literal[
            "formal_decision_sample",
            "four_phase_coverage",
            "matched_transitions",
        ]
    ]
    conclusion: str


class CycleBacktestChangedJudgementOut(BaseModel):
    observation_period: str
    baseline_realtime_phase: RelativeCyclePhase
    diagnostic_realtime_phase: RelativeCyclePhase
    baseline_final_phase: RelativeCyclePhase
    diagnostic_final_phase: RelativeCyclePhase
    baseline_agreement: bool
    diagnostic_agreement: bool


class CycleBacktestPairedSliceOut(BaseModel):
    label_basis: Literal["display_phase", "confirmed_phase"]
    common_months: int
    minimum_rate_sample: int
    baseline_agreement_count: int | None = None
    baseline_agreement_rate: float | None = None
    diagnostic_agreement_count: int | None = None
    diagnostic_agreement_rate: float | None = None
    agreement_rate_delta_percentage_points: float | None = None
    changed_judgement_months: list[CycleBacktestChangedJudgementOut]
    agreement_outcome_changed_months: list[str]
    sample_note: str | None = None


class CycleBacktestPairedComparisonOut(BaseModel):
    display: CycleBacktestPairedSliceOut
    decision: CycleBacktestPairedSliceOut


class CycleBacktestFixedSurveyCoreOut(BaseModel):
    name: Literal["fixed_survey_core_v1"]
    diagnostic_only: Literal[True]
    signal_weights: dict[str, float]
    missing_policy: str
    production_gate_policy: str
    leading_and_confirmation_policy: str
    stability: CycleBacktestStabilityOut
    phase_distribution: CycleBacktestPhaseDistributionOut
    transitions: CycleBacktestTransitionsOut
    gates: CycleBacktestSensitivityGatesOut
    paired_comparison: CycleBacktestPairedComparisonOut


class CycleBacktestExcludeJanuaryOut(BaseModel):
    evaluation_window_only: Literal[True]
    filter: Literal["observation_period_month_is_not_january"]
    note: str
    excluded_months: list[str]
    stability: CycleBacktestStabilityOut
    phase_distribution: CycleBacktestPhaseDistributionOut
    transitions: CycleBacktestTransitionsOut
    gates: CycleBacktestSensitivityGatesOut
    agreement_rate_delta_percentage_points: float | None = None
    decision_agreement_rate_delta_percentage_points: float | None = None


class CycleBacktestRobustnessOut(BaseModel):
    full_sample: CycleBacktestStabilityOut
    exclude_covid_2020: CycleBacktestStabilityOut
    fixed_survey_core: CycleBacktestFixedSurveyCoreOut
    exclude_january_observation: CycleBacktestExcludeJanuaryOut


class CycleBacktestInputReadinessOut(BaseModel):
    code: str
    name: str
    role: str
    observation_lag_months: int = 0
    final_observations: int
    known_available_at_observations: int
    on_schedule_observations: int
    unknown_available_at_observations: int
    ambiguous_revision_observations: int
    unknown_revision_observations: int
    non_reconstructable_observations: int
    availability_rate: float | None = None
    on_schedule_rate: float | None = None
    first_known_period: str | None = None
    last_known_period: str | None = None


class CycleBacktestAvailabilityOut(BaseModel):
    final_observations: int
    strict_observations: int
    observation_coverage: float | None = None
    required_input_codes: int
    available_input_codes: int
    input_code_coverage: float
    unknown_available_at_observations: int
    not_yet_available_observations: int
    ambiguous_revisions_excluded: int
    unknown_revisions_excluded: int
    non_reconstructable_observations_excluded: int
    revised_observations: int
    missing_final_observations: int


class CycleBacktestPhaseSnapshotOut(BaseModel):
    phase: RelativeCyclePhase | None = None
    phase_label: str
    phase_status: RegimeStatus
    phase_basis: PhaseBasis
    confirmed_phase: RelativeCyclePhase | None = None
    confirmed_since: str | None = None
    last_decision_period: str | None = None
    carry_forward_months: int
    decision_eligible: bool
    raw_phase: RelativeCyclePhase | None = None
    candidate_phase: RelativeCyclePhase | None = None
    candidate_since: str | None = None
    candidate_streak: int = 0
    required_confirmation_months: int | None = None
    level_axis: Literal["above", "below", "neutral", "unavailable"]
    momentum_axis: Literal["rising", "falling", "neutral", "unavailable"]
    level_3m: float | None = None
    level_gap: float | None = None
    momentum_3m: float | None = None
    leading_level_3m: float | None = None
    leading_gap: float | None = None
    leading_momentum_3m: float | None = None
    leading_direction: Literal["up", "down", "neutral", "unavailable"] = (
        "unavailable"
    )
    coincident_index: float | None = None
    leading_index: float | None = None
    coincident_basis_signature: str | None = None
    leading_basis_signature: str | None = None
    coincident_basis_changed: bool = False
    leading_basis_changed: bool = False
    confidence: Literal["high", "medium", "low", "insufficient"]


class CycleBacktestAttributionMetricOut(BaseModel):
    status: Literal["available", "unavailable", "additivity_failed"]
    realtime: float | None = None
    hybrid: float | None = None
    final: float | None = None
    revision_path_delta: float | None = None
    support_expansion_path_delta: float | None = None
    total_delta: float | None = None
    residual: float | None = None
    additivity_passed: bool


class CycleBacktestAttributionPhaseStepOut(BaseModel):
    revision_step: Literal["changed", "unchanged", "not_comparable"]
    support_step: Literal["changed", "unchanged", "not_comparable"]


class CycleBacktestAttributionPhaseStepsOut(BaseModel):
    display: CycleBacktestAttributionPhaseStepOut | None = None
    confirmed: CycleBacktestAttributionPhaseStepOut | None = None
    raw: CycleBacktestAttributionPhaseStepOut | None = None


class CycleBacktestAttributionSupportAuditOut(BaseModel):
    realtime_support_count: int
    hybrid_support_count: int
    final_support_count: int
    matched_final_value_count: int
    revised_input_count: int
    expanded_input_count: int
    missing_counterpart_count: int
    ambiguous_counterpart_count: int
    formula_mismatch_count: int
    input_support_preserved: bool
    missing_counterpart_keys: list[str]
    ambiguous_counterpart_keys: list[str]
    formula_mismatch_keys: list[str]
    support_window_start: str | None = None
    realtime_coincident_basis_signature: str | None = None
    hybrid_coincident_basis_signature: str | None = None
    final_coincident_basis_signature: str | None = None
    realtime_leading_basis_signature: str | None = None
    hybrid_leading_basis_signature: str | None = None
    final_leading_basis_signature: str | None = None
    basis_changed_on_revision_path: bool | None = None
    basis_changed_on_support_path: bool | None = None
    decision_eligibility_changed_on_revision_path: bool | None = None
    decision_eligibility_changed_on_support_path: bool | None = None
    state_path_changed: bool | None = None


class ChinaCycleBacktestAttributionOut(BaseModel):
    observation_period: str
    decision_as_of: datetime
    attribution_methodology_version: str
    a1_methodology_version: str
    a2_methodology_version: str
    a3_methodology_version: str
    path_order: list[Literal[
        "realtime_visible_information",
        "final_values_on_realtime_support",
        "full_final_information",
    ]]
    final_cutoff_at: datetime
    status: Literal[
        "available",
        "hybrid_unavailable",
        "not_comparable",
        "additivity_failed",
    ]
    reasons: list[str]
    realtime: CycleBacktestPhaseSnapshotOut | None = None
    hybrid: CycleBacktestPhaseSnapshotOut | None = None
    final: CycleBacktestPhaseSnapshotOut | None = None
    support_audit: CycleBacktestAttributionSupportAuditOut | None = None
    metrics: dict[str, CycleBacktestAttributionMetricOut]
    phase_steps: CycleBacktestAttributionPhaseStepsOut
    warnings: list[str]


FinalReferenceStatus = Literal[
    "same_endpoint_rerun",
    "hidden_no_realtime_label",
    "same_endpoint_unavailable",
]


class CycleBacktestMonthOut(BaseModel):
    observation_period: str
    decision_as_of: datetime
    status: Literal["evaluable", "limited", "unavailable"]
    exclusion_reasons: list[str]
    availability: CycleBacktestAvailabilityOut
    realtime: CycleBacktestPhaseSnapshotOut | None = None
    final: CycleBacktestPhaseSnapshotOut | None = None
    final_reference_status: FinalReferenceStatus
    comparable: bool
    phase_agreement: bool | None = None
    phase_changed: bool | None = None
    decision_comparable: bool
    decision_phase_agreement: bool | None = None
    comparison_type: Literal[
        "same",
        "phase_changed",
        "realtime_unclassified",
        "final_unclassified",
        "both_unclassified",
        "unavailable",
    ]
    coincident_revision_delta: float | None = None
    leading_revision_delta: float | None = None


class ChinaCycleBacktestOut(BaseModel):
    region: Literal["CN"]
    country: str
    title: str
    mode: Literal["pseudo_realtime"]
    data_basis: Literal["strict_available_at_vintage"]
    status: Literal["ok", "limited", "unavailable"]
    as_of: str | None = None
    start_period: str | None = None
    end_period: str | None = None
    methodology_note: str
    summary: str
    backtest_definition: CycleBacktestDefinitionOut
    coverage: CycleBacktestCoverageOut
    stability: CycleBacktestStabilityOut
    transitions: CycleBacktestTransitionsOut
    robustness: CycleBacktestRobustnessOut
    input_readiness: list[CycleBacktestInputReadinessOut]
    latest: CycleBacktestMonthOut | None = None
    months: list[CycleBacktestMonthOut]
    warnings: list[str]
