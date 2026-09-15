const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface IndicatorSummary {
  code: string;
  name: string;
  category: string;
  region: string;
  unit: string;
  latest_date: string | null;
  latest_value: number | null;
  change_pct: number | null;
  recent_values: number[];
  freshness: "current" | "event" | "delayed" | "stale" | "missing";
  freshness_label: string;
}

export interface DataPoint {
  date: string;
  value: number;
}

export interface IndicatorHistory {
  code: string;
  name: string;
  unit: string;
  points: DataPoint[];
}

export interface ForecastPoint {
  date: string;
  value: number;
  lower: number;
  upper: number;
}

export interface ForecastOut {
  code: string;
  name: string;
  forecast_unit: string;
  history: DataPoint[];
  forecast: ForecastPoint[];
}

async function apiFetch<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function getIndicators(region?: string): Promise<IndicatorSummary[]> {
  return apiFetch(region ? `/api/indicators?region=${region}` : "/api/indicators");
}

export function getIndicatorHistory(code: string): Promise<IndicatorHistory> {
  return apiFetch(`/api/indicators/${code}/history`);
}

export function getIndicatorForecast(code: string, horizon?: number): Promise<ForecastOut> {
  const query = horizon ? `?horizon=${horizon}` : "";
  return apiFetch(`/api/indicators/${code}/forecast${query}`);
}

export interface CurrencyOption {
  code: string;
  name: string;
}

export interface ForexHistory {
  base: string;
  target: string;
  points: DataPoint[];
}

export interface ForexForecast {
  base: string;
  target: string;
  forecast_unit: string;
  history: DataPoint[];
  forecast: ForecastPoint[];
}

export function getCurrencies(): Promise<CurrencyOption[]> {
  return apiFetch("/api/forex/currencies");
}

export function getForexHistory(base: string, target: string): Promise<ForexHistory> {
  return apiFetch(`/api/forex/history?base=${base}&target=${target}`);
}

export function getForexForecast(base: string, target: string, horizon?: number): Promise<ForexForecast> {
  const horizonQuery = horizon ? `&horizon=${horizon}` : "";
  return apiFetch(`/api/forex/forecast?base=${base}&target=${target}${horizonQuery}`);
}

export interface PopulationPoint {
  year: number;
  value: number;
}

export interface PopulationSeries {
  key: string;
  name: string;
  unit: string;
  points: PopulationPoint[];
}

export interface PopulationPyramidBar {
  age_group: string;
  male: number;
  female: number;
  total: number;
}

export interface PopulationDashboard {
  region: string;
  country: string;
  source: string;
  source_url: string;
  last_updated: string | null;
  series: PopulationSeries[];
  pyramid_year: number | null;
  pyramid: PopulationPyramidBar[];
}

export function getPopulation(region: string): Promise<PopulationDashboard> {
  return apiFetch(`/api/population/${region}`);
}

export interface EmploymentPoint {
  period: string;
  value: number;
}

export interface EmploymentSeries {
  key: string;
  name: string;
  unit: string;
  frequency: string;
  source_type: string;
  scope: string;
  points: EmploymentPoint[];
}

export interface EmploymentSnapshot {
  year: number;
  employment_total: number;
  urban_employment: number;
  rural_employment: number;
  migrant_workers: number;
  annual_urban_unemployment: number;
  weekly_hours: number;
  source: string;
  source_url: string;
}

export interface EmploymentSource {
  name: string;
  url: string;
  description: string;
}

export interface EmploymentDashboard {
  region: string;
  country: string;
  latest_month: string | null;
  wdi_updated: string | null;
  monthly_series: EmploymentSeries[];
  annual_series: EmploymentSeries[];
  official_snapshot: EmploymentSnapshot;
  sources: EmploymentSource[];
  warnings: string[];
}

export function getEmployment(region: string): Promise<EmploymentDashboard> {
  return apiFetch(`/api/employment/${region}`);
}

export interface InternationalEmploymentDashboard {
  region: string;
  country: string;
  latest_month: string | null;
  series: EmploymentSeries[];
  sources: EmploymentSource[];
  warnings: string[];
}

export type USEmploymentDashboard = InternationalEmploymentDashboard;

export function getInternationalEmployment(region: string): Promise<InternationalEmploymentDashboard> {
  return apiFetch(`/api/employment/${region}`);
}

export function getUSEmployment(): Promise<InternationalEmploymentDashboard> {
  return getInternationalEmployment("US");
}

export interface AnalysisPoint {
  period: string;
  value: number;
}

export interface AnalysisSeries {
  key: string;
  name: string;
  unit: string;
  points: AnalysisPoint[];
  maintenance?: string;
  verified_through?: string;
  source?: string;
  source_url?: string;
}

export interface MonetaryTransmissionSignal {
  key: string;
  name: string;
  value: number;
  unit: string;
  period: string;
  state: string;
  interpretation: string;
  formula: string;
  formula_version?: string;
  data_origin?: "stored" | "calculated_fallback";
}

export interface MonetaryTransmissionDashboard {
  region: string;
  country: string;
  title: string;
  status: string;
  tone: "positive" | "neutral" | "caution";
  summary: string;
  as_of: string;
  signals: MonetaryTransmissionSignal[];
  series: AnalysisSeries[];
  sources: EmploymentSource[];
  warnings: string[];
}

export function getChinaMonetaryTransmission(): Promise<MonetaryTransmissionDashboard> {
  return apiFetch("/api/analysis/cn/monetary-transmission");
}

export type ActivityMatrixRole = "coincident" | "leading";
export type ActivityMatrixConfidence = "high" | "medium" | "low" | "insufficient";

export type ActivityMatrixSignalOperation =
  | "level"
  | "mean"
  | "difference"
  | "trailing_mean_3";

export interface ActivityMatrixSignalMeta {
  code: string;
  name: string;
  input_codes: string[];
  weight: number;
  operation: ActivityMatrixSignalOperation;
  direction: "positive" | "negative";
  frequency: string;
  calibration_start: string | null;
  observation_lag_months: number;
  sources: string[];
}

export interface ActivityMatrixBlockMeta {
  key: string;
  name: string;
  role: ActivityMatrixRole;
  weight: number;
  minimum_coverage: number;
  indicator_codes: string[];
  signals: ActivityMatrixSignalMeta[];
}

export interface ActivityMatrixSignalPoint {
  code: string;
  name: string;
  input_codes: string[];
  weight: number;
  raw_value: number | null;
  source_period: string | null;
  standardized_score: number | null;
  realtime_ready: boolean;
  contribution: number | null;
}

export interface ActivityMatrixBlockPoint {
  key: string;
  name: string;
  role: ActivityMatrixRole;
  score: number | null;
  coverage: number;
  realtime_coverage: number;
  available_count: number;
  total_count: number;
  minimum_coverage: number;
  contribution: number | null;
  signals: ActivityMatrixSignalPoint[];
}

export interface ActivityMatrixValidationPoint {
  code: string;
  name: string;
  raw_value: number | null;
  source_period: string | null;
  standardized_score: number | null;
}

export interface ActivityMatrixMonth {
  period: string;
  composite_index: number | null;
  coincident_index: number | null;
  leading_index: number | null;
  coincident_coverage: number;
  leading_coverage: number;
  overall_coverage: number;
  input_coverage: number;
  realtime_coverage: number;
  active_blocks: number;
  confidence: ActivityMatrixConfidence;
  comparable_to_previous: boolean;
  composition_changed: boolean;
  comparison_reasons: string[];
  blocks: ActivityMatrixBlockPoint[];
  validation: ActivityMatrixValidationPoint[];
}

export interface ActivityMatrixContribution {
  code: string;
  name: string;
  role: ActivityMatrixRole;
  block_key: string;
  source_period: string | null;
  standardized_score: number;
  contribution: number;
}

export interface ActivityMatrixInputStatus {
  code: string;
  name: string;
  input_codes: string[];
  frequency: string;
  latest_observation: string | null;
  latest_value: number | null;
  lag_months: number | null;
  is_stale: boolean;
  used_in_latest: boolean;
  used_observation: string | null;
}

export interface ActivityMatrixLatest {
  period: string;
  composite_index: number | null;
  coincident_index: number | null;
  leading_index: number | null;
  overall_coverage: number;
  input_coverage: number;
  realtime_coverage: number;
  confidence: ActivityMatrixConfidence;
  positive_contributions: ActivityMatrixContribution[];
  negative_contributions: ActivityMatrixContribution[];
  input_latest_periods: ActivityMatrixInputStatus[];
}

export interface ActivityMatrixMethodology {
  standardization: string;
  rolling_window_months: number;
  monthly_min_history: number;
  quarterly_min_history: number;
  zscore_clip: number;
  quarterly_forward_fill_months: number;
  signal_observation_lag_policy: string;
  block_min_coverage: number;
  minimum_active_blocks: number;
  overall_min_coverage: number;
  neutral_level: number;
  index_scale: number;
  missing_value_policy: string;
  weighting: string;
  input_coverage_definition: string;
  overall_coverage_definition: string;
  realtime_coverage_definition: string;
}

export interface ActivityMatrixValidationMeta {
  code: string;
  name: string;
  unit: string;
  source: string;
  frequency: string;
  purpose: "external_validation_only";
}

export interface ActivityMatrixDashboard {
  region: string;
  country: string;
  title: string;
  mode: "current";
  data_basis: "final";
  as_of: string | null;
  realtime_coverage: number | null;
  method: string;
  methodology_version: string;
  methodology_note: string;
  methodology: ActivityMatrixMethodology;
  blocks: ActivityMatrixBlockMeta[];
  validation_indicators: ActivityMatrixValidationMeta[];
  months: ActivityMatrixMonth[];
  latest: ActivityMatrixLatest | null;
  warnings: string[];
}

export function getChinaActivityMatrix(months = 120): Promise<ActivityMatrixDashboard> {
  const query = new URLSearchParams({ months: String(months) });
  return apiFetch(`/api/analysis/cn/business-cycle/matrix?${query}`);
}

export type BusinessCyclePhase =
  | "recovery"
  | "expansion"
  | "slowdown"
  | "contraction";

export type BusinessCyclePhaseStatus =
  | "confirmed"
  | "candidate"
  | "transition"
  | "held_uncomparable"
  | "stale"
  | "insufficient";

export interface BusinessCycleDriver {
  code: string;
  name: string;
  block_key: string;
  source_period: string | null;
  contribution: number;
}

export interface BusinessCycleAbsoluteAnchorPoint {
  code: string;
  name: string;
  three_month_average: number | null;
  threshold: number;
  gap: number | null;
  state: "above" | "below" | "unavailable";
}

export interface BusinessCycleAbsoluteAnchor {
  state: "expansionary" | "mixed" | "contractionary" | "unavailable";
  breadth: number | null;
  gap: number | null;
  valid_count: number;
  total_count: number;
  conflict: boolean;
  anchors: BusinessCycleAbsoluteAnchorPoint[];
}

export interface BusinessCycleInflation {
  period: string;
  value: number | null;
  level: "very_low" | "mild" | "elevated" | "unavailable";
  three_month_average: number | null;
  momentum: number | null;
  state:
    | "deflation_pressure"
    | "low_inflation"
    | "moderate"
    | "heating"
    | "unavailable";
  direction: "reflation" | "disinflation" | "stable" | "unavailable";
  ppi_value: number | null;
  ppi_three_month_average: number | null;
  rationale: string;
}

export interface BusinessCycleRegimePoint {
  period: string;
  phase: BusinessCyclePhase | null;
  phase_label: string;
  raw_phase: BusinessCyclePhase | null;
  phase_status: BusinessCyclePhaseStatus;
  confirmed: boolean;
  confirmed_phase: BusinessCyclePhase | null;
  candidate_phase: BusinessCyclePhase | null;
  candidate_since: string | null;
  confirmed_since: string | null;
  candidate_streak: number;
  required_confirmation_months: number | null;
  duration_months: number | null;
  undecidable_streak: number;
  decision_eligible: boolean;
  decision_reasons: string[];
  coincident_comparable: boolean;
  leading_comparable: boolean;
  coincident_coverage: number | null;
  leading_coverage: number | null;
  coincident_basis: string[];
  leading_basis: string[];
  coincident_basis_signature: string | null;
  leading_basis_signature: string | null;
  coincident_basis_coverage: number | null;
  leading_basis_coverage: number | null;
  coincident_basis_changed: boolean;
  leading_basis_changed: boolean;
  coincident_index: number | null;
  leading_index: number | null;
  level_3m: number | null;
  level_gap: number | null;
  momentum_3m: number | null;
  diagnostic_level_3m: number | null;
  diagnostic_momentum_3m: number | null;
  level_axis: "above" | "below" | "neutral" | "unavailable";
  momentum_axis: "rising" | "falling" | "neutral" | "unavailable";
  leading_level_3m: number | null;
  leading_gap: number | null;
  leading_momentum_3m: number | null;
  leading_diagnostic_level_3m: number | null;
  leading_diagnostic_momentum_3m: number | null;
  leading_direction: "up" | "down" | "neutral" | "unavailable";
  leading_confirmation: "confirmed" | "divergent" | "neutral" | "unavailable";
  confidence: ActivityMatrixConfidence;
  confidence_reasons: string[];
  data_basis: "final";
  realtime_coverage: number;
  absolute_anchor: BusinessCycleAbsoluteAnchor;
  inflation: BusinessCycleInflation;
  summary: string;
  outlook: string;
  triggers: string[];
  positive_contributions: BusinessCycleDriver[];
  negative_contributions: BusinessCycleDriver[];
}

export interface BusinessCycleTrajectoryPoint {
  period: string;
  level_gap: number;
  momentum_3m: number | null;
  diagnostic_momentum_3m: number | null;
  phase: BusinessCyclePhase | null;
  phase_status: BusinessCyclePhaseStatus;
  comparable: boolean;
}

export interface BusinessCycleTimelineItem {
  phase: BusinessCyclePhase;
  phase_label: string;
  start_period: string;
  end_period: string;
  duration_months: number;
  ongoing: boolean;
}

export interface BusinessCycleRegimeDashboard {
  region: "CN";
  country: string;
  title: string;
  as_of: string | null;
  last_decision_period: string | null;
  mode: "current";
  data_basis: "final";
  realtime_coverage: number | null;
  a1_methodology_version: string | null;
  methodology_version: string;
  methodology_note: string;
  methodology: BusinessCycleMethodology;
  latest: BusinessCycleRegimePoint | null;
  months: BusinessCycleRegimePoint[];
  trajectory: BusinessCycleTrajectoryPoint[];
  timeline: BusinessCycleTimelineItem[];
  change_conditions: string[];
  a1_warnings: string[];
  warnings: string[];
}

export interface BusinessCycleMethodology {
  classification_scope: "relative_growth_cycle_not_recession_call";
  level_source: string;
  level_smoothing_months: number;
  momentum_definition: string;
  momentum_comparison_months: number;
  level_neutral: number;
  level_buffer: number;
  momentum_buffer: number;
  confirmation_months_with_leading: number;
  confirmation_months_without_leading: number;
  phase_order: BusinessCyclePhase[];
  role_comparability: string;
  balanced_panel_months: number;
  basis_change_policy: string;
  coincident_min_coverage: number;
  coincident_high_coverage: number;
  absolute_anchor_min_valid: number;
  absolute_breadth_expansionary: number;
  absolute_breadth_contractionary: number;
  inflation_direction_buffer_pp: number;
}

export function getChinaBusinessCycleRegime(months = 120): Promise<BusinessCycleRegimeDashboard> {
  const query = new URLSearchParams({ months: String(months) });
  return apiFetch(`/api/analysis/cn/business-cycle/regime?${query}`);
}

export type BusinessCycleBacktestStatus = "ok" | "limited" | "unavailable";
export type BusinessCycleBacktestMonthStatus = "evaluable" | "limited" | "unavailable";
export type BusinessCycleBacktestComparison =
  | "same"
  | "phase_changed"
  | "realtime_unclassified"
  | "final_unclassified"
  | "both_unclassified"
  | "unavailable";

export interface BusinessCycleBacktestDefinition {
  timezone: string;
  decision_schedule: string;
  strict: boolean;
  stored_available_at_timezone: string;
  observation_alignment: string;
  availability_policy: string;
  vintage_selection: string;
  final_reference: string;
  final_cutoff_at: string;
  a1_methodology_version: string;
  a2_methodology_version: string;
  a3_methodology_version: string;
}

export interface BusinessCycleBacktestCoverage {
  scheduled_months: number;
  display_evaluable_months: number;
  display_evaluable_rate: number | null;
  decision_evaluable_months: number;
  decision_evaluable_rate: number | null;
  unavailable_months: number;
  reason_counts: Record<string, number>;
}

export interface BusinessCycleBacktestConfusionItem {
  realtime_phase: BusinessCyclePhase;
  final_phase: BusinessCyclePhase;
  count: number;
}

export interface BusinessCycleBacktestStability {
  comparable_months: number;
  agreement_count: number | null;
  agreement_rate: number | null;
  flip_count: number | null;
  flip_rate: number | null;
  decision_comparable_months: number;
  decision_agreement_count: number | null;
  decision_agreement_rate: number | null;
  level_axis_comparable_months: number;
  level_axis_agreement_rate: number | null;
  momentum_axis_comparable_months: number;
  momentum_axis_agreement_rate: number | null;
  confusion: BusinessCycleBacktestConfusionItem[];
  minimum_rate_sample: number;
  sample_note: string | null;
}

export interface BusinessCycleBacktestTransitionEvent {
  final_phase: BusinessCyclePhase;
  final_confirmation_period: string;
  realtime_confirmation_period: string | null;
  signed_lag_months: number | null;
  match_status: "matched" | "unmatched";
}

export interface BusinessCycleBacktestTransitions {
  matched_count: number;
  lag_median_months: number | null;
  lag_q1_months: number | null;
  lag_q3_months: number | null;
  unmatched_realtime: number;
  unmatched_final: number;
  minimum_lag_sample: number;
  events: BusinessCycleBacktestTransitionEvent[];
}

export interface BusinessCycleBacktestRobustness {
  full_sample: BusinessCycleBacktestStability;
  exclude_covid_2020: BusinessCycleBacktestStability;
}

export interface BusinessCycleBacktestInputReadiness {
  code: string;
  name: string;
  role: string;
  observation_lag_months: number;
  final_observations: number;
  known_available_at_observations: number;
  on_schedule_observations: number;
  unknown_available_at_observations: number;
  ambiguous_revision_observations: number;
  unknown_revision_observations: number;
  non_reconstructable_observations: number;
  availability_rate: number | null;
  on_schedule_rate: number | null;
  first_known_period: string | null;
  last_known_period: string | null;
}

export interface BusinessCycleBacktestAvailability {
  final_observations: number;
  strict_observations: number;
  observation_coverage: number | null;
  required_input_codes: number;
  available_input_codes: number;
  input_code_coverage: number;
  unknown_available_at_observations: number;
  not_yet_available_observations: number;
  ambiguous_revisions_excluded: number;
  unknown_revisions_excluded: number;
  non_reconstructable_observations_excluded: number;
  revised_observations: number;
  missing_final_observations: number;
}

export interface BusinessCycleBacktestRegimeSnapshot {
  phase: BusinessCyclePhase | null;
  phase_label: string;
  phase_status: BusinessCyclePhaseStatus;
  confirmed_phase: BusinessCyclePhase | null;
  confirmed_since: string | null;
  last_decision_period: string | null;
  decision_eligible: boolean;
  level_axis: "above" | "below" | "neutral" | "unavailable";
  momentum_axis: "rising" | "falling" | "neutral" | "unavailable";
  level_3m: number | null;
  momentum_3m: number | null;
  coincident_index: number | null;
  leading_index: number | null;
  confidence: ActivityMatrixConfidence;
}

export interface BusinessCycleBacktestMonth {
  observation_period: string;
  decision_as_of: string;
  status: BusinessCycleBacktestMonthStatus;
  exclusion_reasons: string[];
  availability: BusinessCycleBacktestAvailability;
  realtime: BusinessCycleBacktestRegimeSnapshot | null;
  final: BusinessCycleBacktestRegimeSnapshot | null;
  comparable: boolean;
  phase_agreement: boolean | null;
  phase_changed: boolean | null;
  decision_comparable: boolean;
  decision_phase_agreement: boolean | null;
  comparison_type: BusinessCycleBacktestComparison;
  coincident_revision_delta: number | null;
  leading_revision_delta: number | null;
}

export interface BusinessCycleBacktestDashboard {
  region: "CN";
  country: string;
  title: string;
  as_of: string | null;
  mode: "pseudo_realtime";
  data_basis: "strict_available_at_vintage";
  methodology_note: string;
  summary: string;
  status: BusinessCycleBacktestStatus;
  start_period: string | null;
  end_period: string | null;
  backtest_definition: BusinessCycleBacktestDefinition;
  coverage: BusinessCycleBacktestCoverage;
  stability: BusinessCycleBacktestStability;
  transitions: BusinessCycleBacktestTransitions;
  robustness: BusinessCycleBacktestRobustness;
  input_readiness: BusinessCycleBacktestInputReadiness[];
  latest: BusinessCycleBacktestMonth | null;
  months: BusinessCycleBacktestMonth[];
  warnings: string[];
}

export function getChinaBusinessCycleBacktest(months = 120): Promise<BusinessCycleBacktestDashboard> {
  const query = new URLSearchParams({ months: String(months) });
  return apiFetch(`/api/analysis/cn/business-cycle/backtest?${query}`);
}
