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

export function getIndicators(region?: string, includeHidden = false): Promise<IndicatorSummary[]> {
  const query = new URLSearchParams();
  if (region) query.set("region", region);
  if (includeHidden) query.set("include_hidden", "true");
  const suffix = query.size ? `?${query}` : "";
  return apiFetch(`/api/indicators${suffix}`);
}

export interface IndicatorCatalogEntry {
  code: string;
  name: string;
  category: string;
  region: string;
  unit: string;
  is_visible: boolean;
  sort_order: number;
  source: string;
  frequency: "daily" | "weekly" | "monthly" | "quarterly" | "event";
  seasonal_adjustment: string;
  measure_type: string;
  aggregation: string;
  cumulative: boolean;
  direction: string;
  transform: string;
  release_lag_months: number;
  valid_min: number | null;
  valid_max: number | null;
  notes: string;
  formula: string | null;
  formula_version: string | null;
  input_codes: string[] | null;
}

export function getIndicatorCatalog(region?: string): Promise<IndicatorCatalogEntry[]> {
  const suffix = region ? `?${new URLSearchParams({ region })}` : "";
  return apiFetch(`/api/indicator-catalog${suffix}`);
}

export interface RefreshResult {
  indicator_code: string;
  status: string;
  row_count: number;
  changed_count: number;
  duration_ms: number;
  started_at: string;
  finished_at: string;
  last_success_at: string | null;
  source_verified_through: string | null;
  error: string | null;
  quality_issues: Array<Record<string, unknown>>;
}

export interface RefreshRun {
  id: number;
  trigger: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  total_indicators: number;
  success_count: number;
  no_change_count: number;
  failed_count: number;
  error: string | null;
  results: RefreshResult[];
}

export function getRefreshStatus(): Promise<RefreshRun | null> {
  return apiFetch("/api/refresh/status");
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

export type USMacroTone = "positive" | "neutral" | "caution" | "negative" | "unavailable";
export type USMacroTrend = "up" | "down" | "flat" | "unavailable";
export type USMacroConfidence = "high" | "medium" | "low" | "unavailable";

export interface USMacroMetric {
  key: string;
  label: string;
  value: number | null;
  unit: string;
  period: string | null;
  frequency: "daily" | "monthly" | "quarterly" | "event" | "mixed";
  freshness: "current" | "stale" | "missing";
  trend: USMacroTrend;
  reference_value: number | null;
  reference_period: string | null;
  interpretation: string;
  formula: string;
  source_codes: string[];
}

export interface USMacroPillar {
  key: "growth" | "labour" | "inflation" | "financial_conditions" | "monetary_policy";
  title: string;
  state_key: string;
  state_label: string;
  tone: USMacroTone;
  summary: string;
  confidence: USMacroConfidence;
  metrics: USMacroMetric[];
}

export interface USRecessionBreadth {
  state_key: "limited" | "elevated" | "broad" | "unavailable";
  state_label: string;
  tone: USMacroTone;
  active_signals: number;
  total_signals: number;
  triggers: string[];
  offsets: string[];
  summary: string;
  methodology: string;
}

export interface USTransmissionStep {
  key: string;
  title: string;
  state_label: string;
  tone: USMacroTone;
  detail: string;
  periods: string[];
}

export interface USMacroOverviewDashboard {
  region: "US";
  country: string;
  title: string;
  status: "ok" | "partial" | "unavailable";
  confidence: USMacroConfidence;
  coverage: number;
  realtime_ready: false;
  tone: USMacroTone;
  headline: string;
  as_of: string | null;
  freshness: {
    market_observation_date: string | null;
    monthly_observation_period: string | null;
    quarterly_observation_period: string | null;
    employment_observation_period: string | null;
  };
  methodology_version: string;
  methodology_note: string;
  pillars: USMacroPillar[];
  recession_breadth: USRecessionBreadth;
  transmission: USTransmissionStep[];
  watch_items: string[];
  data_source_path: "/data-sources/us";
  warnings: string[];
}

export function getUSMacroOverview(): Promise<USMacroOverviewDashboard> {
  return apiFetch("/api/analysis/us/overview");
}

export type EUMacroTone = USMacroTone;
export type EUMacroTrend = USMacroTrend;
export type EUMacroConfidence = USMacroConfidence;
export type EUMacroMetric = USMacroMetric;

export interface EUMacroPillar extends Omit<USMacroPillar, "key" | "metrics"> {
  key: "growth" | "labour" | "inflation" | "financial_conditions" | "monetary_policy";
  metrics: EUMacroMetric[];
}

export interface EUDownturnBreadth {
  state_key: "limited" | "elevated" | "broad" | "unavailable";
  state_label: string;
  tone: EUMacroTone;
  active_signals: number;
  total_signals: number;
  triggers: string[];
  offsets: string[];
  summary: string;
  methodology: string;
}

export interface EUTransmissionStep extends Omit<USTransmissionStep, "tone"> {
  tone: EUMacroTone;
}

export interface EUMacroOverviewDashboard {
  region: "EU";
  country: string;
  title: string;
  status: "ok" | "partial" | "unavailable";
  confidence: EUMacroConfidence;
  coverage: number;
  realtime_ready: false;
  tone: EUMacroTone;
  headline: string;
  as_of: string | null;
  freshness: {
    market_observation_date: string | null;
    monthly_observation_period: string | null;
    quarterly_observation_period: string | null;
    employment_observation_period: string | null;
  };
  methodology_version: string;
  methodology_note: string;
  pillars: EUMacroPillar[];
  downturn_breadth: EUDownturnBreadth;
  transmission: EUTransmissionStep[];
  watch_items: string[];
  data_source_path: "/data-sources/eu";
  warnings: string[];
}

export function getEUMacroOverview(): Promise<EUMacroOverviewDashboard> {
  return apiFetch("/api/analysis/eu/overview");
}

export type UKMacroTone = USMacroTone;
export type UKMacroTrend = USMacroTrend;
export type UKMacroConfidence = USMacroConfidence;
export type UKMacroMetric = USMacroMetric;

export interface UKMacroPillar extends Omit<USMacroPillar, "key" | "metrics"> {
  key: "growth" | "labour" | "inflation" | "financial_conditions" | "monetary_policy";
  metrics: UKMacroMetric[];
}

export interface UKDownturnBreadth {
  state_key: "limited" | "elevated" | "broad" | "unavailable";
  state_label: string;
  tone: UKMacroTone;
  active_signals: number;
  total_signals: number;
  triggers: string[];
  offsets: string[];
  summary: string;
  methodology: string;
}

export interface UKTransmissionStep extends Omit<USTransmissionStep, "tone"> {
  tone: UKMacroTone;
}

export interface UKMacroOverviewDashboard {
  region: "GB";
  country: string;
  title: string;
  status: "ok" | "partial" | "unavailable";
  confidence: UKMacroConfidence;
  coverage: number;
  realtime_ready: false;
  tone: UKMacroTone;
  headline: string;
  as_of: string | null;
  freshness: {
    market_observation_date: string | null;
    monthly_observation_period: string | null;
    quarterly_observation_period: string | null;
    employment_observation_period: string | null;
  };
  methodology_version: string;
  methodology_note: string;
  pillars: UKMacroPillar[];
  downturn_breadth: UKDownturnBreadth;
  transmission: UKTransmissionStep[];
  watch_items: string[];
  data_source_path: "/data-sources/uk";
  warnings: string[];
}

export function getUKMacroOverview(): Promise<UKMacroOverviewDashboard> {
  return apiFetch("/api/analysis/uk/overview");
}

export type JPMacroTone = USMacroTone;
export type JPMacroTrend = USMacroTrend;
export type JPMacroConfidence = USMacroConfidence;
export type JPMacroMetric = USMacroMetric;

export interface JPMacroPillar extends Omit<USMacroPillar, "key" | "metrics"> {
  key: "growth" | "labour" | "inflation" | "financial_conditions" | "monetary_policy";
  metrics: JPMacroMetric[];
}

export interface JPDownturnBreadth extends Omit<USRecessionBreadth, "tone"> {
  tone: JPMacroTone;
}

export interface JPTransmissionStep extends Omit<USTransmissionStep, "tone"> {
  tone: JPMacroTone;
}

export interface JPMacroOverviewDashboard {
  region: "JP";
  country: string;
  title: string;
  status: "ok" | "partial" | "unavailable";
  confidence: JPMacroConfidence;
  coverage: number;
  realtime_ready: false;
  tone: JPMacroTone;
  headline: string;
  as_of: string | null;
  freshness: {
    market_observation_date: string | null;
    monthly_observation_period: string | null;
    quarterly_observation_period: string | null;
    employment_observation_period: string | null;
  };
  methodology_version: string;
  methodology_note: string;
  pillars: JPMacroPillar[];
  downturn_breadth: JPDownturnBreadth;
  transmission: JPTransmissionStep[];
  watch_items: string[];
  data_source_path: "/data-sources/jp";
  warnings: string[];
}

export function getJPMacroOverview(): Promise<JPMacroOverviewDashboard> {
  return apiFetch("/api/analysis/jp/overview");
}

export type KRMacroTone = USMacroTone;
export type KRMacroTrend = USMacroTrend;
export type KRMacroConfidence = USMacroConfidence;
export type KRMacroMetric = USMacroMetric;

export interface KRMacroPillar extends Omit<USMacroPillar, "key" | "metrics"> {
  key: "growth" | "labour" | "inflation" | "financial_conditions" | "monetary_policy";
  metrics: KRMacroMetric[];
}

export interface KRDownturnBreadth extends Omit<USRecessionBreadth, "tone"> {
  tone: KRMacroTone;
}

export interface KRTransmissionStep extends Omit<USTransmissionStep, "tone"> {
  tone: KRMacroTone;
}

export interface KRMacroOverviewDashboard {
  region: "KR";
  country: string;
  title: string;
  status: "ok" | "partial" | "unavailable";
  confidence: KRMacroConfidence;
  coverage: number;
  realtime_ready: false;
  tone: KRMacroTone;
  headline: string;
  as_of: string | null;
  freshness: {
    market_observation_date: string | null;
    monthly_observation_period: string | null;
    quarterly_observation_period: string | null;
    employment_observation_period: string | null;
  };
  methodology_version: string;
  methodology_note: string;
  pillars: KRMacroPillar[];
  downturn_breadth: KRDownturnBreadth;
  transmission: KRTransmissionStep[];
  watch_items: string[];
  data_source_path: "/data-sources/kr";
  warnings: string[];
}

export function getKRMacroOverview(): Promise<KRMacroOverviewDashboard> {
  return apiFetch("/api/analysis/kr/overview");
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
  current_value?: number;
  effective_date?: string;
  verified_through?: string;
  catalog_updated_at?: string;
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
  /** @deprecated Use freshness.market_observation_date. */
  as_of: string;
  freshness?: {
    market_observation_date: string;
    policy_rate_verified_through: string;
    credit_observation_period: string;
  };
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

export type BusinessCyclePhaseBasis =
  | "active_decision"
  | "carried_forward"
  | "pending_confirmation"
  | "unclassified";

export interface BusinessCycleDriver {
  code: string;
  name: string;
  block_key: string;
  source_period: string | null;
  contribution: number;
}

export interface BusinessCycleDriverContribution {
  code: string;
  name: string;
  block_key: string;
  effective_weight: number;
  recent_score: number;
  comparison_score: number;
  level_contribution: number;
  momentum_contribution: number;
  recent_source_periods: string[];
  comparison_source_periods: string[];
}

export interface BusinessCycleDriverDecomposition {
  role: "coincident" | "leading";
  status: "available" | "unavailable";
  reason: "insufficient_history" | "insufficient_common_basis" | null;
  basis_signature: string | null;
  basis_codes: string[];
  basis_coverage: number;
  recent_window_start: string | null;
  recent_window_end: string | null;
  comparison_window_start: string | null;
  comparison_window_end: string | null;
  level_gap: number | null;
  momentum_3m: number | null;
  level_contribution_sum: number | null;
  momentum_contribution_sum: number | null;
  level_residual: number | null;
  momentum_residual: number | null;
  additivity_passed: boolean;
  drivers: BusinessCycleDriverContribution[];
}

export type BusinessCycleStateChangeReason =
  | "first_decision"
  | "level_axis_changed"
  | "momentum_axis_changed"
  | "raw_phase_changed"
  | "candidate_started"
  | "candidate_progressed"
  | "candidate_reset"
  | "candidate_cleared"
  | "phase_confirmed"
  | "phase_maintained"
  | "phase_carried_forward"
  | "basis_changed_hold"
  | "insufficient_hold"
  | "dead_zone_unclassified"
  | "level_dead_zone_inherited"
  | "momentum_dead_zone_inherited"
  | "leading_shortened_confirmation";

export interface BusinessCycleStateChange {
  previous_decision_period: string | null;
  previous_level_axis: "above" | "below" | "neutral" | "unavailable" | null;
  previous_momentum_axis: "rising" | "falling" | "neutral" | "unavailable" | null;
  previous_raw_phase: BusinessCyclePhase | null;
  previous_confirmed_phase: BusinessCyclePhase | null;
  reason_codes: BusinessCycleStateChangeReason[];
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
  /** Legacy display/backtest state; use current_phase for a present-tense interpretation. */
  phase: BusinessCyclePhase | null;
  current_phase?: BusinessCyclePhase | null;
  phase_label: string;
  raw_phase: BusinessCyclePhase | null;
  phase_status: BusinessCyclePhaseStatus;
  phase_basis: BusinessCyclePhaseBasis;
  confirmed: boolean;
  confirmed_phase: BusinessCyclePhase | null;
  candidate_phase: BusinessCyclePhase | null;
  candidate_since: string | null;
  confirmed_since: string | null;
  candidate_streak: number;
  required_confirmation_months: number | null;
  duration_months: number | null;
  undecidable_streak: number;
  carry_forward_months: number;
  last_decision_period: string | null;
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
  coincident_decomposition: BusinessCycleDriverDecomposition;
  leading_decomposition: BusinessCycleDriverDecomposition;
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
  state_change: BusinessCycleStateChange;
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
  current_phase_max_carry_months: number;
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
  driver_decomposition: string;
  decomposition_tolerance: number;
}

export function getChinaBusinessCycleRegime(months = 120): Promise<BusinessCycleRegimeDashboard> {
  const query = new URLSearchParams({ months: String(months) });
  return apiFetch(`/api/analysis/cn/business-cycle/regime?${query}`);
}

export type BusinessCycleBacktestStatus = "ok" | "limited" | "unavailable";
export type BusinessCycleBacktestMonthStatus = "evaluable" | "limited" | "unavailable";
export type BusinessCycleFinalReferenceStatus =
  | "same_endpoint_rerun"
  | "hidden_no_realtime_label"
  | "same_endpoint_unavailable";
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
  carried_forward_comparable_months: number;
  carried_forward_agreement_count: number | null;
  carried_forward_agreement_rate: number | null;
  carried_forward_flip_count: number | null;
  carried_forward_flip_rate: number | null;
  mixed_basis_comparable_months: number;
  pending_confirmation_comparable_months: number;
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
  phase_basis: BusinessCyclePhaseBasis;
  confirmed_phase: BusinessCyclePhase | null;
  confirmed_since: string | null;
  last_decision_period: string | null;
  carry_forward_months: number;
  decision_eligible: boolean;
  raw_phase?: BusinessCyclePhase | null;
  candidate_phase?: BusinessCyclePhase | null;
  candidate_since?: string | null;
  candidate_streak?: number;
  required_confirmation_months?: number | null;
  level_axis: "above" | "below" | "neutral" | "unavailable";
  momentum_axis: "rising" | "falling" | "neutral" | "unavailable";
  level_3m: number | null;
  level_gap?: number | null;
  momentum_3m: number | null;
  leading_level_3m?: number | null;
  leading_gap?: number | null;
  leading_momentum_3m?: number | null;
  leading_direction?: "up" | "down" | "neutral" | "unavailable";
  coincident_index: number | null;
  leading_index: number | null;
  coincident_basis_signature?: string | null;
  leading_basis_signature?: string | null;
  coincident_basis_changed?: boolean;
  leading_basis_changed?: boolean;
  confidence: ActivityMatrixConfidence;
}

export type BusinessCycleBacktestAttributionStatus =
  | "available"
  | "hybrid_unavailable"
  | "not_comparable"
  | "additivity_failed";

export type BusinessCycleBacktestAttributionStepResult =
  | "changed"
  | "unchanged"
  | "not_comparable";

export type BusinessCycleBacktestAttributionStepValue =
  | BusinessCycleBacktestAttributionStepResult
  | boolean
  | null;

export interface BusinessCycleBacktestAttributionPhaseStep {
  revision_step?: BusinessCycleBacktestAttributionStepValue;
  support_step?: BusinessCycleBacktestAttributionStepValue;
}

export interface BusinessCycleBacktestAttributionPhaseSteps {
  display?: BusinessCycleBacktestAttributionPhaseStep | null;
  confirmed?: BusinessCycleBacktestAttributionPhaseStep | null;
  raw?: BusinessCycleBacktestAttributionPhaseStep | null;
}

export type BusinessCycleBacktestAttributionMetricStatus =
  | "available"
  | "unavailable"
  | "not_comparable"
  | "additivity_failed";

export interface BusinessCycleBacktestAttributionMetric {
  status?: BusinessCycleBacktestAttributionMetricStatus;
  realtime?: number | null;
  hybrid?: number | null;
  final?: number | null;
  revision_path_delta?: number | null;
  support_expansion_path_delta?: number | null;
  total_delta?: number | null;
  residual?: number | null;
  additivity_passed?: boolean;
}

export interface BusinessCycleBacktestAttributionMetrics {
  level_gap?: BusinessCycleBacktestAttributionMetric | null;
  momentum_3m?: BusinessCycleBacktestAttributionMetric | null;
  leading_gap?: BusinessCycleBacktestAttributionMetric | null;
  leading_momentum_3m?: BusinessCycleBacktestAttributionMetric | null;
}

export interface BusinessCycleBacktestAttributionSupportAudit {
  realtime_support_count?: number | null;
  hybrid_support_count?: number | null;
  final_support_count?: number | null;
  matched_final_value_count?: number | null;
  revised_input_count?: number | null;
  expanded_input_count?: number | null;
  missing_counterpart_count?: number | null;
  ambiguous_counterpart_count?: number | null;
  formula_mismatch_count?: number | null;
  input_support_preserved?: boolean | null;
  missing_counterpart_keys?: string[];
  ambiguous_counterpart_keys?: string[];
  formula_mismatch_keys?: string[];
  support_window_start?: string | null;
  realtime_coincident_basis_signature?: string | null;
  hybrid_coincident_basis_signature?: string | null;
  final_coincident_basis_signature?: string | null;
  realtime_leading_basis_signature?: string | null;
  hybrid_leading_basis_signature?: string | null;
  final_leading_basis_signature?: string | null;
  basis_changed_on_revision_path?: boolean | null;
  basis_changed_on_support_path?: boolean | null;
  decision_eligibility_changed_on_revision_path?: boolean | null;
  decision_eligibility_changed_on_support_path?: boolean | null;
  state_path_changed?: boolean | null;
}

export type BusinessCycleBacktestAttributionPath =
  | "realtime_visible_information"
  | "final_values_on_realtime_support"
  | "full_final_information";

export interface BusinessCycleBacktestAttribution {
  observation_period?: string;
  decision_as_of?: string;
  attribution_methodology_version?: string;
  a1_methodology_version?: string;
  a2_methodology_version?: string;
  a3_methodology_version?: string;
  final_cutoff_at?: string;
  status: BusinessCycleBacktestAttributionStatus;
  realtime?: BusinessCycleBacktestRegimeSnapshot | null;
  hybrid?: BusinessCycleBacktestRegimeSnapshot | null;
  final?: BusinessCycleBacktestRegimeSnapshot | null;
  support_audit?: BusinessCycleBacktestAttributionSupportAudit | null;
  metrics?: BusinessCycleBacktestAttributionMetrics | null;
  phase_steps?: BusinessCycleBacktestAttributionPhaseSteps | null;
  path_order?: BusinessCycleBacktestAttributionPath[];
  reasons?: string[];
  warnings?: string[];
}

export interface BusinessCycleBacktestMonth {
  observation_period: string;
  decision_as_of: string;
  status: BusinessCycleBacktestMonthStatus;
  exclusion_reasons: string[];
  availability: BusinessCycleBacktestAvailability;
  realtime: BusinessCycleBacktestRegimeSnapshot | null;
  final: BusinessCycleBacktestRegimeSnapshot | null;
  final_reference_status: BusinessCycleFinalReferenceStatus;
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

export function getChinaBusinessCycleBacktestAttribution(
  period: string,
): Promise<BusinessCycleBacktestAttribution> {
  const query = new URLSearchParams({ period });
  return apiFetch(`/api/analysis/cn/business-cycle/backtest/attribution?${query}`);
}

export type MaritimeMetricState = "stronger" | "steady" | "weaker" | "unavailable";
export type MaritimeChokepointState = "above" | "normal" | "below" | "unavailable";
export type MaritimeLayerStatus = "available" | "pilot" | "planned";

export interface MaritimeMetric {
  key: string;
  label: string;
  value: number | null;
  unit: string;
  comparison_value: number | null;
  yoy_pct: number | null;
  state: MaritimeMetricState;
  coverage: number;
  interpretation: string;
}

export interface MaritimeTrendPoint {
  date: string;
  world_flow_yoy: number | null;
  country_exports_yoy: number | null;
  country_inputs_yoy: number | null;
}

export interface MaritimeCountryOption {
  code: string;
  name: string;
  kind: "country" | "region";
}

export interface MaritimeVesselMix {
  key: string;
  label: string;
  current_daily_mn_t: number | null;
  yoy_pct: number | null;
  share_pct: number | null;
}

export interface MaritimeChokepoint {
  key: string;
  name: string;
  current_daily_calls: number | null;
  current_daily_capacity_mn_t: number | null;
  yoy_pct: number | null;
  coverage: number;
  state: MaritimeChokepointState;
}

export interface MaritimeLayer {
  key: "quantity" | "congestion" | "price";
  title: string;
  status: MaritimeLayerStatus;
  detail: string;
}

export interface MaritimeSource {
  name: string;
  url: string;
  description: string;
  update_frequency: string;
  access_level: string;
}

export interface MaritimeObservatoryDashboard {
  title: string;
  status: "ok" | "partial" | "unavailable";
  as_of: string | null;
  source_history_start: string | null;
  trend_start: string | null;
  freshness: "current" | "stale" | "missing";
  window_days: number;
  comparison_days: number;
  methodology_version: string;
  realtime_ready: false;
  selected_country_code: string;
  selected_country_name: string;
  available_countries: MaritimeCountryOption[];
  headline: string;
  metrics: MaritimeMetric[];
  trend: MaritimeTrendPoint[];
  vessel_mix: MaritimeVesselMix[];
  chokepoints: MaritimeChokepoint[];
  layers: MaritimeLayer[];
  sources: MaritimeSource[];
  warnings: string[];
}

export function getMaritimeObservatory(
  country = "CHN",
): Promise<MaritimeObservatoryDashboard> {
  const query = new URLSearchParams({ country });
  return apiFetch(`/api/observatory/maritime?${query}`);
}

export interface MaritimeComparisonPoint {
  date: string;
  container_yoy: number | null;
  exports_yoy: number | null;
  inputs_yoy: number | null;
}

export interface MaritimeGlobalTrendPoint {
  date: string;
  container_yoy: number | null;
}

export interface MaritimeComparisonSeries {
  code: string;
  name: string;
  kind: "country" | "region";
  latest_container_yoy: number | null;
  latest_exports_yoy: number | null;
  latest_inputs_yoy: number | null;
  points: MaritimeComparisonPoint[];
}

export interface MaritimeComparisonDashboard {
  status: "ok" | "partial";
  as_of: string;
  source_history_start: string;
  trend_start: string | null;
  freshness: "current" | "stale" | "missing";
  window_days: number;
  comparison_days: number;
  methodology_version: string;
  global_trend: MaritimeGlobalTrendPoint[];
  series: MaritimeComparisonSeries[];
  warnings: string[];
}

export function getMaritimeComparison(
  geographies: string[],
): Promise<MaritimeComparisonDashboard> {
  const query = new URLSearchParams({ geographies: geographies.join(",") });
  return apiFetch(`/api/observatory/maritime/comparison?${query}`);
}
