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
